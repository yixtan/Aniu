"""Tests for fixed two-stage model and prompt settings."""

import pytest

from backend.business.runs import StrategySnapshot
from backend.business.settings import (
    STAGE_IDS,
    AniuAgentPrompt,
    AppSettings,
    StageSettings,
)


def test_stage_settings_normalize_optional_thinking_effort() -> None:
    settings = StageSettings.from_mapping(
        {
            "stage_id": "Run",
            "model_selected_model_id": 11,
            "temperature": 0.2,
            "top_p": 0.8,
            "thinking_effort": " HIGH ",
            "prompt": "执行提示词",
        }
    )

    assert settings.thinking_effort == "high"
    assert settings.as_dict()["thinking_effort"] == "high"


def test_stage_settings_contract_has_no_render_or_tool_loop_switches() -> None:
    settings = StageSettings(
        stage_id="Summary",
        model_selected_model_id=11,
        temperature=0.2,
        top_p=0.8,
        prompt="总结提示词",
    )

    assert settings.model_selected_model_id == 11
    assert set(settings.as_dict()) == {
        "stage_id",
        "model_selected_model_id",
        "temperature",
        "top_p",
        "thinking_effort",
        "prompt",
        # Prompt text the operator writes, not a switch that changes how a
        # stage runs — which is what the assertions below guard against.
        "watchlist_prompt",
    }
    assert not hasattr(settings, "render_mode")
    assert not hasattr(settings, "html_prompt")
    assert not hasattr(settings, "max_tool_rounds")


def test_configured_stage_ids_include_dream_and_watch() -> None:
    assert STAGE_IDS == ("Run", "Summary", "Dream", "Watch")


def test_old_settings_inherit_run_model_for_missing_dream_stage() -> None:
    settings = AppSettings(
        stage_settings={
            "Run": StageSettings("Run", 11, 0.0, 1.0, "运行提示词"),
            "Summary": StageSettings("Summary", 11, 0.0, 1.0, "总结提示词"),
        }
    )

    assert settings.stage_settings["Dream"].model_selected_model_id == 11
    assert settings.stage_settings["Dream"].prompt

    with pytest.raises(ValueError, match="unknown stage_id"):
        StageSettings(
            stage_id="Research",
            model_selected_model_id=1,
            temperature=0,
            top_p=1,
            prompt="legacy",
        )


def test_snapshot_composes_global_and_stage_prompt_at_runtime() -> None:
    snapshot = StrategySnapshot(
        prompt_version="v3",
        risk_rules_version="v1",
        prompt_profile=AniuAgentPrompt(global_prompt="全局约束"),
        stage_settings={
            "Run": StageSettings(
                stage_id="Run",
                model_selected_model_id=1,
                temperature=0,
                top_p=1,
                prompt="执行提示词",
            )
        },
    )

    assert tuple(snapshot.stage_settings) == STAGE_IDS
    assert snapshot.stage_settings["Run"].prompt == "执行提示词"
    assert snapshot.settings_for_stage("Run").prompt == "全局约束\n\n执行提示词"


def test_stage_settings_accepts_zero_top_p() -> None:
    settings = StageSettings(
        stage_id="Run",
        model_selected_model_id=1,
        temperature=0,
        top_p=0,
        prompt="分析输入",
    )

    assert settings.top_p == 0


def test_mapping_ignores_non_domain_storage_fields() -> None:
    settings = StageSettings.from_mapping(
        {
            "stage_id": "Summary",
            "model_selected_model_id": 1,
            "temperature": 0,
            "top_p": 1,
            "prompt": "生成完整总结",
            "max_output_tokens": 1_000_000,
        }
    )

    assert "max_output_tokens" not in settings.as_dict()


def test_the_default_prompts_close_the_dual_track_loop() -> None:
    """A fresh clone's watch must have something to read.

    `DEFAULT_WATCH_PROMPT` opens by telling the watch to read this run's order
    plan, and the watch may touch only the orders that plan names. So if the
    run prompt never asks for a plan, every watch in a new install reads an
    empty one, is authorized to do nothing, and the whole feature is inert
    without a single error anywhere.
    """

    from backend.business.settings.prompt import (
        DEFAULT_RUN_PROMPT,
        DEFAULT_WATCH_PROMPT,
    )

    assert "挂单处置清单" in DEFAULT_WATCH_PROMPT
    assert "declare_order_plan" in DEFAULT_RUN_PROMPT
    # A plan can only speak about orders the run actually looked at.
    assert "未成交委托" in DEFAULT_RUN_PROMPT


def test_the_default_run_prompt_still_has_its_four_steps() -> None:
    """The additions are for compatibility; the instructions are not ours."""

    from backend.business.settings.prompt import DEFAULT_RUN_PROMPT

    for step in ("一是", "二是", "三是", "四是"):
        assert step in DEFAULT_RUN_PROMPT
    assert DEFAULT_RUN_PROMPT.startswith("你负责操作股票模拟账户进行交易")
    assert DEFAULT_RUN_PROMPT.endswith("实现账户收益最大化的最终目标。")


def test_every_configured_stage_receives_the_global_prompt() -> None:
    """Pins the count CLAUDE.md quotes, which went stale once already.

    The rule for what belongs in the global prompt is "only what holds for
    every stage", so the number of stages is load-bearing. `settings_for_stage`
    composes for whatever it is asked about, so a new stage joins silently —
    Watch did, and the doc kept saying three for a day.
    """

    from backend.business.runs import StrategySnapshot
    from backend.business.settings import AniuAgentPrompt, default_stage_settings

    profile = AniuAgentPrompt(global_prompt="全局：这句必须出现在每个阶段前面。")
    snapshot = StrategySnapshot(
        prompt_version="v1",
        risk_rules_version="risk-v1",
        prompt_profile=profile,
        stage_settings=default_stage_settings(profile),
    )

    stages = sorted(snapshot.stage_settings)
    assert stages == ["Dream", "Run", "Summary", "Watch"], (
        "阶段集合变了，CLAUDE.md 里「四个阶段」那段要跟着改"
    )
    for stage_id in stages:
        composed = snapshot.settings_for_stage(stage_id).prompt
        assert composed.startswith(profile.global_prompt), stage_id
