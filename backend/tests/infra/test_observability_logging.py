"""Structured logging, redaction, access filtering, and rotation tests."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

from backend.infra.observability import (
    HealthAccessFilter,
    LoggingSettings,
    PrivateRotatingFileHandler,
    RedactionFilter,
    StructuredJsonFormatter,
    build_logging_config,
    redact_text,
)


def _record(
    *,
    name: str = "backend.test",
    message: object = "event",
    args: tuple[object, ...] = (),
    extra: dict[str, object] | None = None,
) -> logging.LogRecord:
    record = logging.LogRecord(
        name=name,
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=args,
        exc_info=None,
    )
    for key, value in (extra or {}).items():
        setattr(record, key, value)
    return record


def test_structured_formatter_exposes_context_and_redacts_secrets() -> None:
    record = _record(
        message="llm failed authorization=%s api_key=%s",
        args=("Bearer test-token-value", "test-api-key"),
        extra={
            "run_id": 20260729101,
            "stage_id": "research:1",
            "provider": "claude_api",
            "model": "test-model",
            "duration_ms": 123,
            "input_bytes": 456,
            "output_bytes": 789,
            "password": "must-not-appear",
        },
    )

    assert RedactionFilter().filter(record) is True
    payload = json.loads(StructuredJsonFormatter().format(record))

    assert payload["run_id"] == 20260729101
    assert payload["stage_id"] == "research:1"
    assert payload["provider"] == "claude_api"
    assert payload["duration_ms"] == 123
    assert "test-token-value" not in payload["message"]
    assert "test-api-key" not in payload["message"]
    assert "must-not-appear" not in json.dumps(payload)
    assert "[REDACTED]" in payload["message"]


def test_health_filter_drops_only_successful_high_frequency_liveness_requests() -> None:
    access_filter = HealthAccessFilter()

    def access(path: str, status_code: int) -> logging.LogRecord:
        return _record(
            name="uvicorn.access",
            message='%s - "%s %s HTTP/%s" %d',
            args=("127.0.0.1:1234", "GET", path, "1.1", status_code),
        )

    assert access_filter.filter(access("/health", 200)) is False
    assert access_filter.filter(access("/health?probe=1", 200)) is False
    assert access_filter.filter(access("/health/live", 204)) is False
    assert access_filter.filter(access("/health", 302)) is True
    assert access_filter.filter(access("/health", 503)) is True
    assert access_filter.filter(access("/health/ready", 200)) is True
    assert access_filter.filter(access("/api/aniu/runs", 200)) is True


def test_logging_config_prepares_private_rotating_file(tmp_path: Path) -> None:
    log_file = tmp_path / "logs" / "aniu.log"
    config = build_logging_config(
        LoggingSettings(
            level="INFO",
            file_path=log_file,
            max_bytes=128,
            backup_count=2,
        )
    )

    handler_config = config["handlers"]["rotating_file"]
    assert handler_config["class"] == (
        "backend.infra.observability.log_config.PrivateRotatingFileHandler"
    )
    assert handler_config["maxBytes"] == 128
    assert handler_config["backupCount"] == 2
    assert log_file.exists()
    assert log_file.stat().st_mode & 0o777 == 0o600

    handler = PrivateRotatingFileHandler(
        log_file,
        maxBytes=128,
        backupCount=2,
        encoding="utf-8",
    )
    handler.setFormatter(StructuredJsonFormatter())
    handler.addFilter(RedactionFilter())
    logger = logging.Logger("backend.rotation-test", level=logging.INFO)
    logger.addHandler(handler)
    for index in range(8):
        logger.info(
            "rotation_event_%s",
            index,
            extra={"run_id": 20260729101, "stage_id": "research:1"},
        )
    handler.close()

    assert (tmp_path / "logs" / "aniu.log.1").exists()
    assert log_file.stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "logs" / "aniu.log.1").stat().st_mode & 0o777 == 0o600


def test_redaction_filter_scrubs_nested_sensitive_keys() -> None:
    record = _record(
        message={
            "authorization": "Bearer nested-secret",
            "nested": {
                "client_secret": "hidden-client-secret",
                "session_token": "hidden-session-token",
                "total_tokens": 42,
            },
        }
    )

    assert RedactionFilter().filter(record) is True
    payload = json.loads(StructuredJsonFormatter().format(record))
    serialized = json.dumps(payload)

    assert "nested-secret" not in serialized
    assert "hidden-client-secret" not in serialized
    assert "hidden-session-token" not in serialized
    assert "[REDACTED]" in payload["message"]
    assert "42" in payload["message"]


def test_formatter_redacts_exception_messages() -> None:
    try:
        raise RuntimeError(
            "provider failed api_key=test-exception-key "
            "authorization=Bearer jwt-secret"
        )
    except RuntimeError:
        record = _record(message="provider request failed")
        record.exc_info = sys.exc_info()

    assert RedactionFilter().filter(record) is True
    payload = json.loads(StructuredJsonFormatter().format(record))
    serialized = json.dumps(payload)

    assert "test-exception-key" not in serialized
    assert "jwt-secret" not in serialized
    assert "[REDACTED]" in payload["exception"]


@pytest.mark.parametrize(
    ("raw", "secret"),
    [
        ("Authorization: Basic dXNlcjpwYXNz", "dXNlcjpwYXNz"),
        ("token=super-secret-token", "super-secret-token"),
        ('password="hello world secret"', "hello world secret"),
        ("Cookie: session=abc123; csrf=xyz789", "abc123"),
        ("GET /api?token=query-secret&limit=1", "query-secret"),
        ('{"api_key":"json-secret-value"}', "json-secret-value"),
        ("{'api_key': 'python-secret-value'}", "python-secret-value"),
        ("x_api_key=underscore-secret", "underscore-secret"),
        ("session_token=session-secret", "session-secret"),
        ('{"password":"prefix\\"SECRET_SUFFIX"}', "SECRET_SUFFIX"),
        ("{'api_key': 'prefix\\'SECRET_SUFFIX'}", "SECRET_SUFFIX"),
        ('password="line1\nSECRET_SUFFIX"', "SECRET_SUFFIX"),
        ('password="unterminated SECRET_SUFFIX', "SECRET_SUFFIX"),
        ('{\\"api_key\\":\\"nested-secret\\"}', "nested-secret"),
    ],
)
def test_text_redaction_covers_header_cookie_quoted_and_query_values(
    raw: str,
    secret: str,
) -> None:
    redacted = redact_text(raw)

    assert secret not in redacted
    assert "[REDACTED]" in redacted


def test_logging_config_rejects_symbolic_link_target(tmp_path: Path) -> None:
    target = tmp_path / "target.log"
    target.write_text("unchanged", encoding="utf-8")
    link = tmp_path / "aniu.log"
    link.symlink_to(target)

    config = build_logging_config(
        LoggingSettings(level="INFO", file_path=link, max_bytes=128, backup_count=2)
    )

    assert "rotating_file" not in config["handlers"]
    assert target.read_text(encoding="utf-8") == "unchanged"


SERVERCHAN_KEY = "SCT999999FAKEKEYFORTESTING"
WECOM_KEY = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def test_redacts_a_credential_carried_in_a_url_path() -> None:
    """Server酱 puts its SendKey in the path, where header rules cannot see it."""

    text = f"https://sctapi.ftqq.com/{SERVERCHAN_KEY}.send"

    assert redact_text(text) == "https://sctapi.ftqq.com/[REDACTED].send"


def test_redacts_a_credential_carried_in_a_key_query_parameter() -> None:
    """企业微信 group bots authenticate with a bare `key` query parameter."""

    text = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={WECOM_KEY}"

    assert redact_text(text) == (
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=[REDACTED]"
    )


@pytest.mark.parametrize(
    "text",
    [
        "https://api.example.com/v1/quotes?symbol=600519",
        "https://sctapi.ftqq.com/docs",
        "http://127.0.0.1:8125/hook",
        "GET /api/aniu/runs 200",
    ],
)
def test_ordinary_urls_are_left_alone(text: str) -> None:
    assert redact_text(text) == text


def test_redacts_a_url_object_passed_as_a_log_argument() -> None:
    """httpx logs request.url as an httpx.URL, not a str."""

    import httpx

    record = _record(
        name="httpx",
        message='HTTP Request: %s %s "%s %d %s"',
        args=(
            "POST",
            httpx.URL(f"https://sctapi.ftqq.com/{SERVERCHAN_KEY}.send"),
            "HTTP/1.1",
            200,
            "OK",
        ),
    )

    assert RedactionFilter().filter(record) is True
    assert SERVERCHAN_KEY not in record.getMessage()
    assert "[REDACTED]" in record.getMessage()


def test_redacts_a_preformatted_message_without_args() -> None:
    """A message built with an f-string carries its own secret."""

    record = _record(message=f"posting to https://sctapi.ftqq.com/{SERVERCHAN_KEY}.send")

    assert RedactionFilter().filter(record) is True
    assert SERVERCHAN_KEY not in record.getMessage()


def test_non_string_scalars_keep_their_type_through_redaction() -> None:
    record = _record(
        message="run finished",
        extra={"run_id": 42, "duration_ms": 1.5, "ok": True, "missing": None},
    )

    assert RedactionFilter().filter(record) is True
    assert record.run_id == 42
    assert record.duration_ms == 1.5
    assert record.ok is True
    assert record.missing is None
