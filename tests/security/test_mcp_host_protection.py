"""The MCP endpoint must not fail open.

DNS-rebinding protection on a Streamable HTTP MCP endpoint is only meaningful
if it is on. The rule here is: loopback binding gets a localhost allow-list
automatically, a network binding must declare its hostnames, and a network
binding that declares none has the remote endpoint withdrawn rather than
served unprotected.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agentic_analytics.api.app import create_app
from agentic_analytics.config import Settings

TOOLS_LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
ACCEPT = {"Accept": "application/json, text/event-stream"}


def _client(**overrides: object) -> TestClient:
    return TestClient(create_app(Settings(provider_mode="fake", log_json=False, **overrides)))


def test_local_development_serves_mcp_without_configuration() -> None:
    """A loopback bind is only reachable locally, so it needs no allow-list."""
    with _client(bind_host="127.0.0.1") as client:
        response = client.post("/mcp", json=TOOLS_LIST, headers=ACCEPT)
    # A protocol-level error means the endpoint is live and handling the
    # request; 404, 405 or 503 would mean it is not.
    assert response.status_code not in (404, 405, 503), response.text


def test_a_network_binding_without_an_allow_list_withdraws_the_endpoint() -> None:
    """Fail closed: do not serve MCP publicly with Host validation off."""
    with _client(bind_host="0.0.0.0") as client:
        response = client.post("/mcp", json=TOOLS_LIST, headers=ACCEPT)
    assert response.status_code == 503
    body = response.json()
    assert body["error"] == "mcp_endpoint_disabled"
    assert "AAE_MCP_ALLOWED_HOSTS" in body["detail"]


def test_the_rest_of_the_api_still_works_when_mcp_is_withdrawn() -> None:
    """Withdrawing the remote transport must not break the application."""
    with _client(bind_host="0.0.0.0") as client:
        assert client.get("/api/health").json()["status"] == "ok"
        assert client.post("/api/datasets/demo").status_code == 200
        assert len(client.get("/api/recordings").json()["recordings"]) == 3


@pytest.mark.parametrize(
    "host,expected_ok",
    [
        ("demo.example.com", True),
        ("demo.example.com:443", True),
        ("evil.example.com", False),
        ("demo.example.com.evil.com", False),
        ("", False),
        ("demo.example.com\r\nX-Injected: 1", False),
    ],
)
def test_host_header_is_validated_against_the_allow_list(host: str, expected_ok: bool) -> None:
    with _client(bind_host="0.0.0.0", mcp_allowed_hosts="demo.example.com") as client:
        headers = dict(ACCEPT)
        try:
            response = client.post("/mcp", json=TOOLS_LIST, headers={**headers, "Host": host})
        except Exception:
            # A malformed header rejected by the HTTP stack is also a refusal.
            assert not expected_ok
            return
    if expected_ok:
        assert response.status_code != 421, response.text
    else:
        assert response.status_code == 421, (
            f"Host {host!r} was accepted with status {response.status_code}"
        )


def test_allow_list_accepts_several_hostnames() -> None:
    with _client(
        bind_host="0.0.0.0",
        mcp_allowed_hosts="a.example.com, b.example.com",
    ) as client:
        for host in ("a.example.com", "b.example.com"):
            response = client.post("/mcp", json=TOOLS_LIST, headers={**ACCEPT, "Host": host})
            assert response.status_code != 421, host
        rejected = client.post("/mcp", json=TOOLS_LIST, headers={**ACCEPT, "Host": "c.example.com"})
        assert rejected.status_code == 421


def test_the_binding_policy_is_readable_from_settings() -> None:
    assert Settings(bind_host="127.0.0.1").is_local_binding is True
    assert Settings(bind_host="localhost").is_local_binding is True
    assert Settings(bind_host="::1").is_local_binding is True
    assert Settings(bind_host="0.0.0.0").is_local_binding is False
    assert Settings(bind_host="10.0.0.5").is_local_binding is False
    assert Settings(mcp_allowed_hosts=" a.com , b.com ,").mcp_allowed_host_list == [
        "a.com",
        "b.com",
    ]


@pytest.mark.parametrize(
    ("bind_host", "allowed_hosts", "expect_enabled"),
    [
        ("127.0.0.1", "", True),  # local development
        ("0.0.0.0", "", False),  # network binding, no allow-list: withdrawn
        ("0.0.0.0", "example.onrender.com", True),  # declared hostnames
    ],
)
def test_config_reports_the_same_mcp_policy_the_endpoint_enforces(
    bind_host: str, allowed_hosts: str, expect_enabled: bool
) -> None:
    """`/api/config` must not disagree with `/mcp`.

    An external checker cannot infer this from the address it dialled: a
    container published on a loopback port binds to 0.0.0.0 inside, so the
    URL says nothing about the policy. It therefore has to be able to ask --
    and the answer has to be true.
    """
    settings = Settings(bind_host=bind_host, mcp_allowed_hosts=allowed_hosts)
    with TestClient(create_app(settings)) as client:
        reported = client.get("/api/config").json()["mcp_remote_enabled"]
        status = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        ).status_code

    assert reported is expect_enabled
    # 503 is the withdrawn transport. Anything else means it is being served,
    # including the 421 a wrong Host gets once protection is on.
    assert (status != 503) is expect_enabled, f"reported {reported}, endpoint gave {status}"
