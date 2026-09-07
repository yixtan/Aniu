"""Persistence for run-report email delivery settings."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from backend.business.reports import EmailDeliverySettings
from backend.infra.db.models import EmailDeliverySettingsModel
from backend.infra.repositories.secret_store_repo import SecretStoreRepository
from backend.infra.security import SecretCodec

SECRET_NAMESPACE = "report_email"
SECRET_OWNER = "singleton"
SECRET_NAME = "api_key"
_ROW_ID = 1


def _parse_datetime(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.now(tz=UTC)


class EmailSettingsRepository:
    """The single settings row plus its encrypted provider API key."""

    def __init__(
        self,
        session: AsyncSession,
        secret_codec: SecretCodec | None = None,
    ) -> None:
        self._session = session
        self._secrets = SecretStoreRepository(session, secret_codec=secret_codec)

    async def get(self) -> EmailDeliverySettings | None:
        model = await self._session.get(EmailDeliverySettingsModel, _ROW_ID)
        return None if model is None else self._to_entity(model)

    async def save(
        self, settings: EmailDeliverySettings, *, api_key: str | None
    ) -> EmailDeliverySettings:
        model = await self._session.get(EmailDeliverySettingsModel, _ROW_ID)
        if model is None:
            model = EmailDeliverySettingsModel(id=_ROW_ID)
            self._session.add(model)
        model.sender = settings.sender
        model.recipient = settings.recipient
        model.enabled = settings.enabled
        model.api_key_last_four = settings.api_key_last_four
        if api_key is not None:
            await self._secrets.set_secret(
                SECRET_NAMESPACE, SECRET_OWNER, SECRET_NAME, api_key
            )
        await self._session.flush()
        return self._to_entity(model)

    async def get_api_key(self) -> str | None:
        try:
            return await self._secrets.get_secret(
                SECRET_NAMESPACE, SECRET_OWNER, SECRET_NAME
            )
        except ValueError:
            # Encrypted with a key that is no longer available; report it as
            # unset so the caller asks for it again instead of failing hard.
            return None

    def _to_entity(self, model: EmailDeliverySettingsModel) -> EmailDeliverySettings:
        return EmailDeliverySettings(
            sender=model.sender,
            recipient=model.recipient,
            enabled=bool(model.enabled),
            api_key_last_four=model.api_key_last_four,
            created_at=_parse_datetime(model.created_at),
            updated_at=_parse_datetime(model.updated_at),
        )


__all__ = [
    "SECRET_NAME",
    "SECRET_NAMESPACE",
    "SECRET_OWNER",
    "EmailSettingsRepository",
]
