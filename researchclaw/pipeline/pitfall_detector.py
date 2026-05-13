"""Simple detectors for common experiment-quality pitfalls."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any


class PitfallType(str, Enum):
    """Pitfall categories reported by the detector."""

    DATA_LEAKAGE = "data_leakage"
    LOSS_DIRECTION = "loss_direction"
    UNFAIR_COMPARISON = "unfair_comparison"


@dataclass(frozen=True)
class Pitfall:
    """A detected experiment pitfall."""

    type: PitfallType
    severity: str
    description: str
    evidence: str = ""


class PitfallDetector:
    """Detect lightweight static/result pitfalls without executing code."""

    def detect_all(
        self,
        *,
        code: str,
        results: dict[str, Any],
        experiment_config: dict[str, Any],
    ) -> list[Pitfall]:
        pitfalls: list[Pitfall] = []
        pitfalls.extend(self._detect_data_leakage(code))
        pitfalls.extend(self._detect_loss_direction(code))
        pitfalls.extend(self._detect_unfair_comparison(experiment_config))
        _ = results
        return pitfalls

    def _detect_data_leakage(self, code: str) -> list[Pitfall]:
        patterns = (
            r"val(?:idation)?_data\s*=\s*train_data",
            r"test_data\s*=\s*train_data",
            r"train_idx\s*=\s*val_idx",
            r"validation_idx\s*=\s*train_idx",
        )
        for pattern in patterns:
            match = re.search(pattern, code, re.IGNORECASE)
            if match:
                return [
                    Pitfall(
                        type=PitfallType.DATA_LEAKAGE,
                        severity="critical",
                        description="Training and validation/test data appear to overlap.",
                        evidence=match.group(0),
                    )
                ]
        return []

    def _detect_loss_direction(self, code: str) -> list[Pitfall]:
        if re.search(r"loss\s*\+=\s*reward", code) or re.search(
            r"minimize\([^)]*reward", code, re.IGNORECASE
        ):
            return [
                Pitfall(
                    type=PitfallType.LOSS_DIRECTION,
                    severity="high",
                    description="Loss expression may reward the behavior it should penalize.",
                )
            ]
        return []

    def _detect_unfair_comparison(
        self, experiment_config: dict[str, Any]
    ) -> list[Pitfall]:
        capacities = experiment_config.get("model_capacities", {})
        if not isinstance(capacities, dict) or len(capacities) < 2:
            return []
        values = [float(v) for v in capacities.values() if isinstance(v, (int, float))]
        if len(values) < 2 or min(values) <= 0:
            return []
        if max(values) / min(values) >= 3:
            return [
                Pitfall(
                    type=PitfallType.UNFAIR_COMPARISON,
                    severity="medium",
                    description="Compared models have substantially different capacity.",
                )
            ]
        return []
