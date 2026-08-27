"""Lazy registry for supported open-source scene generators."""

from __future__ import annotations

import importlib
from typing import Callable


SCENE_ENTRY_POINTS = {
    "ball_plate": "peridigm_preprocess.scenes.ball_plate:generate",
    "kw_fracture": "peridigm_preprocess.scenes.kw_fracture:generate",
    "mug_fall": "peridigm_preprocess.scenes.mug_fall:generate",
    "cloth_fall": "peridigm_preprocess.scenes.cloth_fall:generate",
}


def get_generator(scenario: str) -> Callable:
    try:
        entry_point = SCENE_ENTRY_POINTS[scenario]
    except KeyError as exc:
        raise ValueError(f"Unknown scene generator: {scenario}") from exc
    module_name, function_name = entry_point.split(":", 1)
    module = importlib.import_module(module_name)
    return getattr(module, function_name)
