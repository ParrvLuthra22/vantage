"""Apply a bulk remap to orchestrator_v1 scenarios.

Idempotent: re-running produces no additional changes.
Safe: preserves fields not mentioned in the remap, plus comments and
formatting style on every file the remap doesn't touch.

Uses ruamel.yaml (round-trip mode), not plain PyYAML: the scenario suite's
YAML relies on inline `#` comments and specific style (block-literal `|`
multi-line strings, flow-style `active_apps: [...]` lists) that carry real
authored context, not just formatting preference. Plain `yaml.safe_load`/
`safe_dump` silently discards every comment and reformats every multi-line
string into a single escaped line on any file it touches -- a real
correctness problem for a tool whose entire premise is "the diff is the
audit trail". See scripts/remap_scenarios.yaml's history for what that
looked like in practice.

Usage:
    python scripts/apply_scenario_remap.py scripts/remap_scenarios.yaml
    python scripts/apply_scenario_remap.py scripts/remap_scenarios.yaml --dry-run
"""
import argparse
import io
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedSeq
from ruamel.yaml.scalarstring import DoubleQuotedScalarString, LiteralScalarString, ScalarString

SCENARIOS_DIR = Path("packages/eval_engine/suites/orchestrator_v1/scenarios")

#: Sequence-valued keys the suite always hand-writes in flow style
#: (active_apps: ["Calendar", "Mail"]), as opposed to hard_checks, which is
#: always block style (one check per line). Forced explicitly below rather
#: than relying on `preserve_quotes` passively keeping whatever a given
#: file's *current* on-disk style happens to be. NOT prior_turns: unlike
#: active_apps (plain app names), prior_turns holds natural-language
#: user/response text that can contain apostrophes/punctuation unsafe as
#: unquoted flow-mapping scalars (see PRIOR_TURNS_QUOTE_KEYS below).
_FLOW_STYLE_KEYS = {"active_apps"}

#: Inside a prior_turns entry ({user: ..., response: ...}), always quote
#: these fields' string values, even if they arrived unquoted. Natural
#: language turn text routinely contains apostrophes/question marks that
#: are only safe as YAML plain scalars by accident of which words happen
#: to appear — e.g. "What's my battery level?" broke strict YAML flow-
#: mapping parsing (a real failure hit while repairing this suite).
_PRIOR_TURNS_QUOTE_KEYS = {"user", "response"}

yaml = YAML()
yaml.preserve_quotes = True
yaml.indent(mapping=2, sequence=4, offset=2)
yaml.width = 1_000_000  # effectively disable line-wrapping of long scalars


def _normalize_styles(node):
    """Force the suite's canonical style regardless of whatever style a
    node currently carries (from hand-authoring or a prior tool's output):
    literal block (`|`) for any multi-line string, flow style for
    active_apps/prior_turns lists. Without this, ruamel's `preserve_quotes`
    faithfully reproduces whatever quoting/wrapping style is already on
    disk for a given node -- including an already-corrupted one. Strings
    are immutable, so reassignment happens here at the parent container
    level rather than by mutating the string itself.
    """
    if isinstance(node, dict):
        # No mapping in this schema is legitimately flow-style ({a: b}) --
        # only sequences (active_apps) ever are. Reset defensively in case
        # an earlier corrupted pass left a flow-style flag "stuck" on a
        # mapping node (see the prior_turns fix below for why that happens).
        fa = getattr(node, "fa", None)
        if fa is not None:
            fa.set_block_style()
        for k, v in list(node.items()):
            if k in _FLOW_STYLE_KEYS and isinstance(v, list):
                seq = CommentedSeq(v)
                seq.fa.set_flow_style()
                node[k] = seq
            elif k == "prior_turns" and isinstance(v, list) and v:
                # Non-empty prior_turns is a list of {user, response} dicts,
                # hand-written one turn per block-style line -- NOT flow
                # style (unlike active_apps). An earlier version of this
                # script forced it to flow style, which -- once written --
                # ruamel keeps "sticky" on later loads regardless of this
                # key being removed from _FLOW_STYLE_KEYS, since flow-vs-
                # block is a per-node property read from whatever's on
                # disk. Force it back to block style explicitly.
                seq = CommentedSeq(v)
                seq.fa.set_block_style()
                node[k] = seq
                _normalize_styles(v)
            elif isinstance(v, str) and "\n" in v:
                node[k] = LiteralScalarString(v)
            elif (
                k in _PRIOR_TURNS_QUOTE_KEYS
                and isinstance(v, str)
                and not isinstance(v, ScalarString)
            ):
                node[k] = DoubleQuotedScalarString(v)
            else:
                _normalize_styles(v)
    elif isinstance(node, list):
        for item in node:
            _normalize_styles(item)


def load_yaml(path: Path):
    with path.open() as f:
        data = yaml.load(f)
    _normalize_styles(data)
    return data


def serialize(data) -> str:
    buf = io.StringIO()
    yaml.dump(data, buf)
    return buf.getvalue()


def dump_yaml(path: Path, data) -> None:
    with path.open("w") as f:
        yaml.dump(data, f)


def deep_merge(base, overrides: dict):
    """Recursively merge overrides into base, mutating and returning base.

    Mutates in place (rather than building a new dict) so ruamel's
    comment/anchor metadata attached to the existing CommentedMap survives.
    """
    for k, v in overrides.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def apply_remap(remap: dict, dry_run: bool = False) -> list[str]:
    """Apply the remap to the scenarios directory. Returns list of changed files."""
    changed: list[str] = []

    scenario_specs = remap.get("scenarios", {}) or {}
    category_budgets = remap.get("category_latency_budgets_ms", {}) or {}
    known_failing_specs = remap.get("known_failing_mark", {}) or {}

    for scenario_file in sorted(SCENARIOS_DIR.glob("*.yaml")):
        scenario = load_yaml(scenario_file)
        original = serialize(scenario)
        ext_id = scenario.get("id")
        category = scenario.get("category")

        # Category-wide latency budget floor
        if category in category_budgets:
            current_budget = scenario.get("rubric", {}).get("latency_budget_ms")
            new_budget = category_budgets[category]
            # Only bump if new is bigger (don't lower an explicit override)
            if current_budget is None or new_budget > current_budget:
                scenario.setdefault("rubric", {})["latency_budget_ms"] = new_budget

        # Per-scenario override
        if ext_id in scenario_specs:
            spec = scenario_specs[ext_id]

            # Expected fields (deep-merged: only listed keys change)
            if "expected" in spec:
                deep_merge(scenario.setdefault("expected", {}), spec["expected"])

            # Replace hard_checks entirely (not merge — you might want to remove old checks)
            if "rubric_hard_checks_replace" in spec:
                scenario.setdefault("rubric", {})["hard_checks"] = list(
                    spec["rubric_hard_checks_replace"]
                )

            # Replace the judge prompt entirely (a multi-line prose field — merging
            # sub-strings doesn't make sense, only a full swap does)
            if "rubric_llm_judge_prompt_replace" in spec:
                scenario.setdefault("rubric", {})["llm_judge_prompt"] = spec[
                    "rubric_llm_judge_prompt_replace"
                ]

            # Append to notes, skipping if this exact text is already present (idempotency)
            if "notes_append" in spec:
                current_notes = scenario.get("notes", "") or ""
                append = spec["notes_append"]
                if append.strip() not in current_notes:
                    scenario["notes"] = current_notes + append

        # Known-failing marking
        if ext_id in known_failing_specs:
            scenario["known_failing"] = True
            scenario["known_failing_reason"] = known_failing_specs[ext_id]["reason"]

        new = serialize(scenario)
        if new != original:
            changed.append(str(scenario_file))
            if not dry_run:
                dump_yaml(scenario_file, scenario)

    return changed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("remap_file", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    remap = load_yaml(args.remap_file)
    changed = apply_remap(remap, dry_run=args.dry_run)

    prefix = "[DRY RUN] Would update" if args.dry_run else "Updated"
    print(f"{prefix} {len(changed)} scenario file(s):")
    for f in changed:
        print(f"  {f}")


if __name__ == "__main__":
    main()
