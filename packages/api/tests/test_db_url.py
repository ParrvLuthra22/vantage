"""Connection-URL handling, in particular the sslmode -> ssl translation.

Neon hands out libpq-style URLs ending in `?sslmode=require`. asyncpg has no
`sslmode` parameter, so without translation the very first connection dies with
`TypeError: connect() got an unexpected keyword argument 'sslmode'`.
"""

import pytest
from vantage_api.database import _resolve_url

NEON = "postgresql+asyncpg://u:p@ep-cool-frost-123456.us-east-2.aws.neon.tech/vantage"
LOCAL = "postgresql+asyncpg://vantage:vantage@localhost:5432/vantage"


def test_sslmode_is_renamed_to_ssl():
    url = _resolve_url(f"{NEON}?sslmode=require")
    assert "sslmode" not in url.query
    assert url.query["ssl"] == "require"


@pytest.mark.parametrize(
    "mode", ["disable", "allow", "prefer", "require", "verify-ca", "verify-full"]
)
def test_every_libpq_sslmode_value_carries_over(mode):
    """asyncpg's `ssl` accepts the same vocabulary, so this is a pure rename."""
    assert _resolve_url(f"{NEON}?sslmode={mode}").query["ssl"] == mode


def test_local_url_is_untouched():
    url = _resolve_url(LOCAL)
    assert url.query == {}
    assert url.host == "localhost"
    assert url.database == "vantage"


def test_explicit_ssl_wins_over_sslmode():
    """A caller who set `ssl` meant it; don't clobber it with the libpq alias."""
    url = _resolve_url(f"{LOCAL}?sslmode=require&ssl=verify-full")
    assert url.query["ssl"] == "verify-full"
    assert "sslmode" not in url.query


def test_other_query_params_survive():
    url = _resolve_url(f"{NEON}?sslmode=require&application_name=vantage-api")
    assert url.query["ssl"] == "require"
    assert url.query["application_name"] == "vantage-api"


def test_credentials_and_host_are_preserved():
    url = _resolve_url(f"{NEON}?sslmode=require")
    assert url.username == "u"
    assert url.password == "p"
    assert url.host.endswith(".neon.tech")
    assert url.drivername == "postgresql+asyncpg"
