"""Lightweight plot-facing types shared by GUI and headless backends."""

from __future__ import annotations

from enum import Enum


class PlotLayoutContext(str, Enum):
    """Supported placements for the shared realtime plot surface."""

    AUTOMATIC = "automatic"
    MANUAL = "manual"
