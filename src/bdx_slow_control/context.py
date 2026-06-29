"""Shared build context for modular IOC groups."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .runtime import RuntimeSettings


@dataclass
class PrototypeContext:
    """State shared by IOC groups in the aggregated prototype process."""

    runtime: RuntimeSettings
    all_off_callbacks: list[Callable[[], None]] = field(default_factory=list)

    def register_all_off(self, callback: Callable[[], None]) -> None:
        if callback not in self.all_off_callbacks:
            self.all_off_callbacks.append(callback)
