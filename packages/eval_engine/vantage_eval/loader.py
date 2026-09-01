"""Load and validate suite + scenario YAML files."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError

from vantage_eval.models import Rubric, Scenario


@dataclass
class Suite:
    """A loaded suite with its scenarios."""
    name: str
    description: str
    agent_target: str
    scenarios: list[Scenario]
    root_path: Path


class SuiteLoadError(Exception):
    """Raised when a suite fails to load or validate."""


def load_suite(suite_path: Path | str) -> Suite:
    """
    Load a suite from disk. `suite_path` points at a directory containing
    suite.yaml and a scenarios/ subdirectory.
    """
    suite_path = Path(suite_path)
    if not suite_path.is_dir():
        raise SuiteLoadError(f"Suite path {suite_path} is not a directory")

    manifest_path = suite_path / "suite.yaml"
    scenarios_dir = suite_path / "scenarios"

    if not manifest_path.exists():
        raise SuiteLoadError(f"Missing suite.yaml at {manifest_path}")
    if not scenarios_dir.is_dir():
        raise SuiteLoadError(f"Missing scenarios/ directory at {scenarios_dir}")

    with manifest_path.open() as f:
        manifest = yaml.safe_load(f)

    required_manifest_keys = {"name", "description", "agent_target"}
    missing = required_manifest_keys - set(manifest.keys())
    if missing:
        raise SuiteLoadError(f"suite.yaml missing keys: {missing}")

    scenarios: list[Scenario] = []
    seen_ids: set[str] = set()

    for scenario_file in sorted(scenarios_dir.glob("*.yaml")):
        try:
            scenario = _load_scenario(scenario_file)
        except (yaml.YAMLError, ValidationError) as e:
            raise SuiteLoadError(f"Failed to load {scenario_file.name}: {e}") from e

        if scenario.external_id in seen_ids:
            raise SuiteLoadError(f"Duplicate scenario id: {scenario.external_id}")
        seen_ids.add(scenario.external_id)
        scenarios.append(scenario)

    if not scenarios:
        raise SuiteLoadError(f"No scenarios found in {scenarios_dir}")

    return Suite(
        name=manifest["name"],
        description=manifest["description"],
        agent_target=manifest["agent_target"],
        scenarios=scenarios,
        root_path=suite_path,
    )


def _load_scenario(path: Path) -> Scenario:
    """Parse one scenario YAML file into a Scenario model."""
    with path.open() as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise SuiteLoadError(f"{path.name} must contain a YAML mapping at the root")

    # The YAML uses 'id' but our Pydantic model uses 'external_id'
    if "id" in raw:
        raw["external_id"] = raw.pop("id")

    rubric_raw = raw.pop("rubric", {})
    scenario = Scenario(**raw, rubric=Rubric(**rubric_raw))
    return scenario
