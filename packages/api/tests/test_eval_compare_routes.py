"""Tests for the eval run comparison / set-baseline routes.

Same conventions as test_api_smoke.py: a real TestClient against the app, no
seeded data. Nothing here commits a row — the 404 paths return before any
write, and the response-mapping test patches the loader so no query runs.
The mutation's happy path (set_baseline actually flipping is_baseline) has no
isolated-DB fixture to run against, so it's covered by hand against a live
API rather than here.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import get_args
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from vantage_api.config import settings
from vantage_api.main import app

AUTH = {"Authorization": f"Bearer {settings.api_key}"}
ZERO = "00000000-0000-0000-0000-000000000000"
ONE = "00000000-0000-0000-0000-000000000001"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_openapi_exposes_comparison_and_set_baseline_routes(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert "get" in paths["/evals/runs/{current_id}/vs/{baseline_id}"]
    assert "post" in paths["/evals/runs/{run_id}/set-baseline"]


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Basic xyz"}, {"Authorization": "Bearer wrong-key"}],
)
def test_comparison_and_set_baseline_require_valid_bearer_token(client, headers):
    assert client.get(f"/evals/runs/{ZERO}/vs/{ONE}", headers=headers).status_code == 401
    assert client.post(f"/evals/runs/{ZERO}/set-baseline", headers=headers).status_code == 401


def test_malformed_run_ids_are_rejected_not_queried(client):
    assert client.get("/evals/runs/nope/vs/also-nope", headers=AUTH).status_code == 422
    assert client.post("/evals/runs/nope/set-baseline", headers=AUTH).status_code == 422


def test_unknown_runs_are_404(client):
    assert client.get(f"/evals/runs/{ZERO}/vs/{ONE}", headers=AUTH).status_code == 404
    assert client.post(f"/evals/runs/{ZERO}/set-baseline", headers=AUTH).status_code == 404


def _fake_db_run(run_id, outcomes: dict[str, bool]):
    """Just the attributes vantage_eval's db_run_to_suite_run reads off an ORM
    EvalRun — enough to drive the route without a database."""
    passed = sum(outcomes.values())
    return SimpleNamespace(
        run_id=run_id,
        agent_version="abc123",
        judge_model="none",
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        finished_at=None,
        summary={
            "total": len(outcomes),
            "passed": passed,
            "failed": len(outcomes) - passed,
            "pass_rate": passed / len(outcomes),
            "total_scenarios": len(outcomes),
        },
        results=[
            SimpleNamespace(
                scenario=SimpleNamespace(external_id=ext_id),
                latency_ms=10,
                deterministic_scores={},
                llm_judge_score=None,
                llm_judge_reasoning=None,
                latency_within_budget=None,
                passed=ok,
            )
            for ext_id, ok in outcomes.items()
        ],
    )


def test_compare_runs_classifies_and_serializes_changes(client, monkeypatch):
    baseline_id, current_id = uuid4(), uuid4()
    runs = {
        baseline_id: _fake_db_run(
            baseline_id, {"kept": True, "broke": True, "fixed": False, "gone": True}
        ),
        current_id: _fake_db_run(
            current_id, {"kept": True, "broke": False, "fixed": True, "added": True}
        ),
    }

    async def fake_load_run(_session, run_id):
        return runs.get(run_id)

    monkeypatch.setattr("vantage_eval.regression.loader.load_run", fake_load_run)

    r = client.get(f"/evals/runs/{current_id}/vs/{baseline_id}", headers=AUTH)
    assert r.status_code == 200
    body = r.json()

    assert body["baseline_run_id"] == str(baseline_id)
    assert body["current_run_id"] == str(current_id)
    assert body["baseline_pass_rate"] == pytest.approx(0.75)
    assert body["current_pass_rate"] == pytest.approx(0.75)
    assert body["pass_rate_delta"] == pytest.approx(0.0)

    change_types = {c["external_id"]: c["change_type"] for c in body["changes"]}
    assert change_types == {
        "kept": "stable_pass",
        "broke": "regression",
        "fixed": "improvement",
        "gone": "removed",
        "added": "new",
    }


def test_change_type_literal_matches_regression_detector():
    """schemas.py re-declares ChangeType instead of importing it from
    vantage_eval (keeping the wire-contract module dependency-free — see the
    comment there). Drift would let the detector emit a value the response
    schema rejects, surfacing as a 500 at serialization time."""
    pytest.importorskip("vantage_eval")
    from vantage_api.schemas import ChangeType as SchemaChangeType
    from vantage_eval.regression.detector import ChangeType as DetectorChangeType

    assert set(get_args(SchemaChangeType)) == set(get_args(DetectorChangeType))
