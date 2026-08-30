# SPDX-License-Identifier: MIT
"""Computer vision on frames uploaded by the robot."""

from __future__ import annotations

from ..config import VisionConfig
from .detector import Detection, NullDetector, TfliteDetector, describe


def build_vision(cfg: VisionConfig):
    if not cfg.enabled or cfg.backend.strip().lower() in {"none", ""}:
        return NullDetector()
    if cfg.backend.strip().lower() in {"tflite", "tensorflow-lite"}:
        return TfliteDetector(cfg)
    raise ValueError(f"unknown WALLE_VISION_BACKEND {cfg.backend!r}; expected 'tflite' or 'none'")


__all__ = ["Detection", "NullDetector", "TfliteDetector", "build_vision", "describe"]
