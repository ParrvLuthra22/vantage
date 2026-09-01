"""Load eval suites and scenarios from YAML on disk.

A suite is a directory: `suite.yaml` holds the EvalSuite fields (name,
description, agent_target), and `scenarios/*.yaml` each hold one Scenario.
Kept separate from models.py so the engine's runtime types don't carry any
knowledge of the filesystem layout they were loaded from.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from vantage_eval.models import Rubric, Scenario


def load_scenario(path: Path) -> Scenario:
    """Parse one scenario YAML file into a Scenario."""
    data: dict[str, Any] = yaml.safe_load(path.read_text())
    rubric_data = data.pop("rubric", {})
    return Scenario(rubric=Rubric(**rubric_data), **data)


def load_suite(suite_dir: Path) -> tuple[dict[str, Any], list[Scenario]]:
    """Load a suite's `suite.yaml` config and every scenario under `scenarios/`.

    Scenarios are sorted by filename so suite runs are reproducible.
    """
    suite_config: dict[str, Any] = yaml.safe_load((suite_dir / "suite.yaml").read_text())
    scenarios_dir = suite_dir / "scenarios"
    scenario_paths = sorted(scenarios_dir.glob("*.yaml")) if scenarios_dir.exists() else []
    scenarios = [load_scenario(p) for p in scenario_paths]
    return suite_config, scenarios
