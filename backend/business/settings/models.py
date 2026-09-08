"""Application settings entity."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, time

from backend.business.settings.prompt import (
    AniuAgentPrompt,
    normalize_optional_str,
    utc_now,
)
from backend.business.settings.stages import (
    STAGE_IDS,
    StageSettings,
    default_stage_settings,
    normalize_stage_settings,
)

DEFAULT_DREAM_SCHEDULE_TIME = "00:30"
DREAM_WINDOW_START = time(16, 0)
DREAM_WINDOW_END = time(8, 0)


def normalize_dream_schedule_time(value: str) -> str:
    """Validate the daily Dream execution time, and that it is out of session.

    The allowed window runs from 16:00 to 08:00 the next morning: after the
    A-share close, before the next session opens. A dream during trading hours
    would reflect on a day that is still happening and then mark it done, so
    the rest of that day's runs would never be read.
    """

    normalized = value.strip()
    try:
        parsed = datetime.strptime(normalized, "%H:%M")
    except ValueError as exc:
        raise ValueError("dream_schedule_time must use HH:MM format") from exc
    result = parsed.strftime("%H:%M")
    if result != normalized:
        raise ValueError("dream_schedule_time must use HH:MM format")
    if DREAM_WINDOW_END < parsed.time() < DREAM_WINDOW_START:
        raise ValueError(
            "dream_schedule_time must fall between 16:00 and 08:00"
        )
    return result


@dataclass(slots=True)
class AppSettings:
    """Persisted settings with one authoritative value for each runtime option."""

    mx_api_key: str | None = None
    prompt_profile: AniuAgentPrompt = field(default_factory=AniuAgentPrompt)
    stage_settings: dict[str, StageSettings] = field(default_factory=dict)
    dream_schedule_time: str = DEFAULT_DREAM_SCHEDULE_TIME
    revision: int = 0
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        self.mx_api_key = normalize_optional_str(self.mx_api_key)
        self.dream_schedule_time = normalize_dream_schedule_time(
            self.dream_schedule_time
        )
        raw_profile = self.prompt_profile
        if isinstance(raw_profile, AniuAgentPrompt):
            prompt_profile = raw_profile
        elif isinstance(raw_profile, Mapping) or raw_profile is None:
            prompt_profile = AniuAgentPrompt.from_mapping(raw_profile)
        else:
            raise ValueError("prompt_profile must be an object")
        self.prompt_profile = prompt_profile

        configured = normalize_stage_settings(self.stage_settings)
        defaults = default_stage_settings(prompt_profile)
        if "Dream" not in configured:
            run_settings = configured.get("Run")
            if (
                run_settings is not None
                and run_settings.model_selected_model_id is not None
            ):
                defaults["Dream"] = replace(
                    defaults["Dream"],
                    model_selected_model_id=run_settings.model_selected_model_id,
                )
        self.stage_settings = {
            stage_id: configured.get(stage_id, defaults[stage_id])
            for stage_id in STAGE_IDS
        }
        if self.revision < 0:
            raise ValueError("revision must be >= 0")
