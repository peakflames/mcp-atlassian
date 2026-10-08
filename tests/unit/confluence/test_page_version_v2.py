"""Page history and diff on the v2 API (Cloud OAuth gateway).

HTTP is faked only at ``requests.Session.request``. The fetcher is a real
ConfluenceFetcher built from a real Cloud OAuth ConfluenceConfig, and the
tools are called through a FastMCP client. The fake answers the v2 endpoints
the way Confluence Cloud does: items from ``/pages/{id}/versions`` carry a
``number`` but no ``id``, ``/pages/{id}?version=N`` returns the page as it
was at version N, and an unknown version is a 404.
"""

import json
import re
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from fastmcp import Client, FastMCP
from fastmcp.client import FastMCPTransport

from mcp_atlassian.confluence import ConfluenceFetcher
from mcp_atlassian.confluence.config import ConfluenceConfig
from mcp_atlassian.servers.confluence import get_page_diff, get_page_history
from mcp_atlassian.servers.context import MainAppContext
from mcp_atlassian.utils.oauth import BYOAccessTokenOAuthConfig

CLOUD_ID = "test-cloud-id"
GATEWAY_PREFIX = f"/ex/confluence/{CLOUD_ID}"
PAGE_ID = "100"
SPACE_ID = "9001"
SPACE_KEY = "ENG"
BODIES = {1: "<p>first line</p>", 2: "<p>first line</p><p>second line</p>"}
TITLES = {1: "Old title", 2: "New title"}


def _response(status: int, payload: Any = None) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response.reason = "OK" if status < 400 else "Error"
    response._content = json.dumps(payload if payload is not None else {}).encode()
    response.headers["Content-Type"] = "application/json"
    response.encoding = "utf-8"
    return response


def _page(version: int) -> dict[str, Any]:
    return {
        "id": PAGE_ID,
        "status": "current",
        "title": TITLES[version],
        "spaceId": SPACE_ID,
        "version": {"number": version, "message": "", "minorEdit": False},
        "body": {"storage": {"value": BODIES[version], "representation": "storage"}},
        "_links": {"webui": f"/spaces/{SPACE_KEY}/pages/{PAGE_ID}"},
    }


class FakeConfluence:
    """Answers the v2 endpoints behind the Cloud OAuth gateway."""

    def __init__(self) -> None:
        self.requests: list[str] = []

    def __call__(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        split = urlsplit(url)
        path = split.path
        assert path.startswith(GATEWAY_PREFIX), path
        path = path[len(GATEWAY_PREFIX) :]
        if path.startswith("/wiki/"):
            path = path[len("/wiki") :]
        query = parse_qs(split.query)
        for key, value in (kwargs.get("params") or {}).items():
            query[key] = [str(value)]
        self.requests.append(
            path + ("?version=" + query["version"][0] if "version" in query else "")
        )
        if method != "GET":
            return _response(405, {"message": "read-only fake"})
        if path == f"/api/v2/pages/{PAGE_ID}/versions":
            # Real shape: no "id" on version items.
            return _response(
                200,
                {
                    "results": [
                        {
                            "number": n,
                            "authorId": "a1",
                            "createdAt": "2026-01-01T00:00:00Z",
                            "message": "",
                            "minorEdit": False,
                            "page": {"id": PAGE_ID, "title": TITLES[n]},
                        }
                        for n in (2, 1)
                    ]
                },
            )
        if path == f"/api/v2/pages/{PAGE_ID}":
            version = int(query.get("version", ["2"])[0])
            if version not in BODIES:
                return _response(
                    404,
                    {"errors": [{"status": 404, "title": "Cannot find version"}]},
                )
            return _response(200, _page(version))
        if path == f"/api/v2/pages/{PAGE_ID}/properties":
            return _response(200, {"results": []})
        if path == f"/api/v2/spaces/{SPACE_ID}":
            return _response(200, {"id": SPACE_ID, "key": SPACE_KEY})
        if re.fullmatch(r"/api/v2/versions/.*", path):
            return _response(404, {"message": "no such endpoint"})
        return _response(404, {"message": f"no route for {path}"})


@pytest.fixture
def fake() -> Iterator[FakeConfluence]:
    fake = FakeConfluence()
    with patch.object(
        requests.Session,
        "request",
        autospec=True,
        side_effect=lambda _self, method, url, **kw: fake(method, url, **kw),
    ):
        yield fake


def _fetcher() -> ConfluenceFetcher:
    config = ConfluenceConfig(
        url="https://example.atlassian.net/wiki",
        auth_type="oauth",
        oauth_config=BYOAccessTokenOAuthConfig("test-access-token", CLOUD_ID),
    )
    fetcher = ConfluenceFetcher(config=config)
    assert fetcher._v2_adapter is not None
    return fetcher


@pytest.fixture
async def client(fake: FakeConfluence) -> AsyncIterator[Client]:
    fetcher = _fetcher()

    @asynccontextmanager
    async def lifespan(_app: FastMCP) -> AsyncIterator[dict[str, Any]]:
        yield {
            "app_lifespan_context": MainAppContext(
                full_confluence_config=fetcher.config, read_only=False
            )
        }

    mcp = FastMCP(name="PageVersionV2Test", lifespan=lifespan)
    mcp.add_tool(get_page_history)
    mcp.add_tool(get_page_diff)
    with patch(
        "mcp_atlassian.servers.confluence.get_confluence_fetcher",
        AsyncMock(return_value=fetcher),
    ):
        async with Client(transport=FastMCPTransport(mcp)) as connected:
            yield connected


def _json(result: Any) -> dict[str, Any]:
    return json.loads("".join(getattr(item, "text", "") for item in result.content))


@pytest.mark.anyio
@pytest.mark.parametrize("version", [1, 2])
async def test_get_page_history_returns_requested_version(
    client: Client, fake: FakeConfluence, version: int
) -> None:
    result = await client.call_tool(
        "get_page_history",
        {"page_id": PAGE_ID, "version": version, "convert_to_markdown": False},
    )

    page = _json(result)
    assert page["id"] == PAGE_ID
    assert page["title"] == TITLES[version]
    assert page["version"] == version
    assert page["space"]["key"] == SPACE_KEY
    assert BODIES[version] in page["content"]["value"]
    assert f"/api/v2/pages/{PAGE_ID}?version={version}" in fake.requests
    assert not any("/api/v2/versions/" in r for r in fake.requests)


@pytest.mark.anyio
async def test_get_page_history_unknown_version_is_an_error(
    client: Client, fake: FakeConfluence
) -> None:
    result = await client.call_tool(
        "get_page_history", {"page_id": PAGE_ID, "version": 7}
    )

    payload = _json(result)
    assert "404" in payload["error"]
    assert "content" not in payload
    assert f"/api/v2/pages/{PAGE_ID}?version=7" in fake.requests


@pytest.mark.anyio
async def test_get_page_diff_compares_the_two_versions(
    client: Client, fake: FakeConfluence
) -> None:
    result = await client.call_tool(
        "get_page_diff", {"page_id": PAGE_ID, "from_version": 1, "to_version": 2}
    )

    diff = _json(result)
    assert diff["from_version"] == 1
    assert diff["to_version"] == 2
    assert diff["title"] == TITLES[2]
    assert "+second line" in diff["diff"]
    assert f"/api/v2/pages/{PAGE_ID}?version=1" in fake.requests
    assert f"/api/v2/pages/{PAGE_ID}?version=2" in fake.requests
