"""Smoke tests for the API: app wiring, auth, and schema validation."""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from vantage_api.config import settings
from vantage_api.main import app
from vantage_api.schemas import MAX_BATCH_SPANS, SpanBatch


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_health_is_unauthenticated(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "version": "0.1.0"}


def test_openapi_exposes_expected_routes(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert "/traces/spans" in paths
    assert "/traces/" in paths
    assert "/traces/{trace_id}" in paths


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Basic xyz"}, {"Authorization": "Bearer wrong-key"}],
)
def test_traces_requires_valid_bearer_token(client, headers):
    assert client.get("/traces/", headers=headers).status_code == 401


def test_batch_size_is_bounded():
    span = dict(
        span_id="00000000-0000-0000-0000-000000000001",
        trace_id="00000000-0000-0000-0000-000000000002",
        name="n",
        start_time="2026-01-01T00:00:00Z",
    )
    SpanBatch(project="p", spans=[span] * MAX_BATCH_SPANS)
    with pytest.raises(ValidationError):
        SpanBatch(project="p", spans=[span] * (MAX_BATCH_SPANS + 1))
    with pytest.raises(ValidationError):
        SpanBatch(project="p", spans=[])


def test_sdk_and_api_wire_contracts_match():
    """The SDK re-declares the contract; drift between them is a protocol break."""
    pytest.importorskip("vantage")
    from vantage.models import SpanCreate
    from vantage_api.schemas import SpanIn

    assert set(SpanCreate.model_fields) == set(SpanIn.model_fields)
    for name, sdk_field in SpanCreate.model_fields.items():
        api_field = SpanIn.model_fields[name]
        assert sdk_field.annotation == api_field.annotation, name


@pytest.mark.parametrize("limit,expected", [(-5, 422), (0, 422), (1, 200), (200, 200), (201, 422)])
def test_list_traces_limit_is_bounded_both_ways(client, limit, expected):
    """A negative limit used to reach Postgres and 500 on 'LIMIT must not be negative'."""
    r = client.get(
        "/traces/",
        params={"project": "nonexistent", "limit": limit},
        headers={"Authorization": f"Bearer {settings.api_key}"},
    )
    assert r.status_code == expected
