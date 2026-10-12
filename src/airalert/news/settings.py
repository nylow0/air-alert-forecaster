"""Atomic local settings, with exact channel and discovered-model validation."""

import json
import os
import threading
from collections.abc import Callable
from pathlib import Path

from airalert.news.channels import defaults
from airalert.news.policy import SETTINGS_PATH
from airalert.schemas import NewsModels, Settings


def default_settings() -> Settings:
    return Settings(
        models=NewsModels(
            extract_model="gpt-6-luna",
            extract_effort="high",
            evaluate_model="gpt-6.1-sol",
            evaluate_effort="high",
        ),
        channels=defaults(),
    )


class SettingsStore:
    def __init__(self, path: Path = SETTINGS_PATH) -> None:
        self.path = path
        self._lock = threading.RLock()
        self.on_change: Callable[[], None] | None = None

    def load(self) -> Settings:
        with self._lock:
            if not self.path.exists():
                return default_settings()
            return self.validate(
                Settings.model_validate_json(self.path.read_text(encoding="utf-8"))
            )

    @staticmethod
    def validate(value: Settings, models: dict[str, set[str]] | None = None) -> Settings:
        if not 120 <= value.poll_seconds <= 3600:
            raise ValueError("poll_seconds must be between 120 and 3600")
        expected = {c.handle: c for c in defaults()}
        if (
            len(value.channels) != len(expected)
            or {c.handle for c in value.channels} != expected.keys()
        ):
            raise ValueError("Settings must contain each of the 19 verified channels exactly once")
        # Only the enabled bit is user-configurable; source identity stays verified.
        channels = [
            expected[c.handle].model_copy(update={"enabled": c.enabled}) for c in value.channels
        ]
        if models is not None:
            for model, effort in (
                (value.models.extract_model, value.models.extract_effort),
                (value.models.evaluate_model, value.models.evaluate_effort),
            ):
                if model not in models or effort not in models[model]:
                    raise ValueError(
                        "Selected model or reasoning effort is not in the Codex catalog"
                    )
        return value.model_copy(update={"channels": channels})

    def save(self, value: Settings, models: dict[str, set[str]] | None = None) -> Settings:
        value = self.validate(value, models)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(".tmp")
            temp.write_text(
                json.dumps(value.model_dump(mode="json"), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temp, self.path)
        if self.on_change is not None:
            self.on_change()
        return value
