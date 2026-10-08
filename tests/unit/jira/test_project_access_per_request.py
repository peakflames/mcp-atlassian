"""Jira project access lists under per-request credentials.

``JIRA_PROJECTS_BLOCKED`` and ``JIRA_PROJECTS_READONLY`` are read from the
fetcher's config by the read check in ``get_issue`` and by the JQL exclusion
in ``search_issues``. These tests check that the lists reach that config for
every kind of per-request credential.

The tools run through a FastMCP client. ``get_jira_fetcher`` is not patched:
the request it sees is the one the real ``UserTokenMiddleware`` passes on, and
the fetcher it builds talks to a fake Jira behind ``requests.Session.request``.
"""

import base64
import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from fastmcp import Client, FastMCP
from fastmcp.client import FastMCPTransport
from fastmcp.exceptions import ToolError
from starlette.requests import Request

from mcp_atlassian.jira.config import JiraConfig
from mcp_atlassian.servers.context import MainAppContext
from mcp_atlassian.servers.jira import get_issue, search
from mcp_atlassian.servers.main import UserTokenMiddleware
from mcp_atlassian.utils.oauth import OAuthConfig

SERVER_URL = "https://jira.example.com"
CLOUD_ID = "test-cloud-id"
BLOCKED_ISSUE = "LEGAL-1"
SEARCH_JQL = "status = Open"

HEADER_PAT = "header-pat"  # X-Atlassian-Jira-Url + -Personal-Token
BASIC = "basic"  # Authorization: Basic
OAUTH = "oauth"  # Authorization: Bearer, server has Cloud OAuth configured
PAT_BEARER = "pat-bearer"  # Authorization: Bearer, server has no OAuth
PER_REQUEST_AUTHS = [HEADER_PAT, BASIC, OAUTH, PAT_BEARER]

REQUEST_HEADERS: dict[str, dict[str, str]] = {
    HEADER_PAT: {
        "X-Atlassian-Jira-Url": SERVER_URL,
        "X-Atlassian-Jira-Personal-Token": "test-pat-token",
    },
    BASIC: {
        "Authorization": "Basic "
        + base64.b64encode(b"user@example.com:test-api-token").decode()
    },
    OAUTH: {"Authorization": "Bearer test-oauth-token"},
    PAT_BEARER: {"Authorization": "Bearer test-pat-token"},
}

ISSUE_PATH = re.compile(r"/rest/api/\d/issue/([A-Z][A-Z0-9_]*-\d+)$")


def _response(status: int, payload: Any) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response.reason = "OK" if status < 400 else "Error"
    response.headers["Content-Type"] = "application/json"
    response._content = json.dumps(payload).encode()
    response.encoding = "utf-8"
    return response


def _issue(key: str) -> dict[str, Any]:
    return {
        "id": "10001",
        "key": key,
        "self": f"{SERVER_URL}/rest/api/2/issue/10001",
        "fields": {
            "summary": "Test issue",
            "status": {"name": "Open"},
            "issuetype": {"name": "Task"},
            "project": {"key": key.split("-")[0], "name": key.split("-")[0]},
        },
    }


class FakeJira:
    """Answers the Jira REST calls the tools under test make.

    Server/Data Center requests go to ``SERVER_URL``; Cloud OAuth requests go
    through the API gateway, so routing uses the path after any gateway
    prefix.
    """

    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []
        self.jql_sent: list[str] = []

    def __call__(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        path = urlsplit(url).path
        path = re.sub(r"^/ex/jira/[^/]+", "", path)
        self.requests.append((method.upper(), path))

        if re.fullmatch(r"/rest/api/\d/myself", path):
            # Credential check made when a per-request fetcher is created.
            return _response(200, {"accountId": "test-account", "name": "user"})
        if m := ISSUE_PATH.fullmatch(path):
            return _response(200, _issue(m[1]))
        if re.fullmatch(r"/rest/api/\d/field", path):
            return _response(200, [])
        if re.fullmatch(r"/rest/api/2/search", path):
            query = parse_qs(urlsplit(url).query)
            query.update({k: [v] for k, v in (kwargs.get("params") or {}).items()})
            self.jql_sent.append(query["jql"][0])
            return _response(
                200, {"issues": [], "total": 0, "startAt": 0, "maxResults": 10}
            )
        if path == "/rest/api/3/search/jql":
            self.jql_sent.append(kwargs["json"]["jql"])
            return _response(200, {"issues": []})
        return _response(404, {"errorMessages": [f"no route for {path}"]})

    def issue_reads(self) -> list[tuple[str, str]]:
        return [r for r in self.requests if ISSUE_PATH.fullmatch(r[1])]


@pytest.fixture
def fake() -> FakeJira:
    return FakeJira()


@pytest.fixture
def http(fake: FakeJira) -> Iterator[None]:
    """Route every requests.Session.request call to the fake Jira."""
    with patch.object(
        requests.Session,
        "request",
        autospec=True,
        side_effect=lambda _self, *args, **kwargs: fake(*args, **kwargs),
    ):
        yield


def _server_config(
    auth: str, *, blocked: str | None, readonly: str | None
) -> JiraConfig:
    """The server's global Jira config for the deployment each auth mode implies."""
    if auth == OAUTH:
        return JiraConfig(
            url=SERVER_URL,
            auth_type="oauth",
            oauth_config=OAuthConfig(
                client_id="test-client-id",
                client_secret="test-client-secret",
                redirect_uri="https://localhost/callback",
                scope="read:jira-work",
                cloud_id=CLOUD_ID,
            ),
            projects_blocked=blocked,
            projects_readonly=readonly,
        )
    return JiraConfig(
        url=SERVER_URL,
        auth_type="basic",
        username="test-user",
        api_token="test-token",
        projects_blocked=blocked,
        projects_readonly=readonly,
    )


def _server(server_config: JiraConfig | None) -> FastMCP:
    """Build a server holding the tools under test.

    The lifespan context uses the same ``{"app_lifespan_context": ...}`` shape
    as the real server.
    """

    @asynccontextmanager
    async def lifespan(_app: FastMCP) -> AsyncIterator[dict[str, Any]]:
        yield {
            "app_lifespan_context": MainAppContext(
                full_jira_config=server_config, read_only=False
            )
        }

    mcp = FastMCP(name="ProjectAccessTest", lifespan=lifespan)
    mcp.add_tool(get_issue)
    mcp.add_tool(search)
    return mcp


async def _middleware_request(headers: dict[str, str]) -> Request:
    """Run UserTokenMiddleware over an MCP POST and return the request it forwards."""
    forwarded: dict[str, Any] = {}

    async def app(scope: Any, _receive: Any, _send: Any) -> None:
        forwarded["scope"] = scope

    mcp_server = MagicMock()
    mcp_server.get_streamable_http_path.return_value = "/mcp"
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/mcp",
        "headers": [
            (name.lower().encode("latin-1"), value.encode("latin-1"))
            for name, value in headers.items()
        ],
    }
    await UserTokenMiddleware(app, mcp_server_ref=mcp_server)(
        scope, AsyncMock(), AsyncMock()
    )
    return Request(forwarded["scope"])


@pytest.fixture
async def connect_as(
    http: None, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Callable[..., Awaitable[tuple[Client, Request]]]]:
    """Yield ``connect_as(auth, blocked=..., readonly=...)`` -> (client, request).

    ``with_server_config=False`` models a deployment with no global Jira
    config, where only header-based credentials work.
    """
    # Lets the middleware's URL check pass without a DNS lookup.
    monkeypatch.setenv("MCP_ALLOWED_URL_DOMAINS", "jira.example.com")
    monkeypatch.delenv("IGNORE_HEADER_AUTH", raising=False)
    async with AsyncExitStack() as stack:

        async def factory(
            auth: str,
            blocked: str | None = "LEGAL",
            readonly: str | None = None,
            *,
            with_server_config: bool = True,
        ) -> tuple[Client, Request]:
            server_config = (
                _server_config(auth, blocked=blocked, readonly=readonly)
                if with_server_config
                else None
            )
            request = await _middleware_request(REQUEST_HEADERS[auth])
            stack.enter_context(
                patch(
                    "mcp_atlassian.servers.dependencies.get_http_request",
                    return_value=request,
                )
            )
            client = Client(transport=FastMCPTransport(_server(server_config)))
            return await stack.enter_async_context(client), request

        yield factory


def _text(result: Any) -> str:
    return str(result.content[0].text)


@pytest.mark.anyio
@pytest.mark.parametrize("auth", PER_REQUEST_AUTHS)
async def test_get_issue_in_blocked_project_denied(
    connect_as: Any, fake: FakeJira, auth: str
) -> None:
    """The issue is not fetched, whatever credentials the request carries."""
    client, _ = await connect_as(auth, blocked="LEGAL")
    with pytest.raises(ToolError, match="'LEGAL' is blocked"):
        await client.call_tool("get_issue", {"issue_key": BLOCKED_ISSUE})
    assert fake.issue_reads() == []


@pytest.mark.anyio
@pytest.mark.parametrize("auth", PER_REQUEST_AUTHS)
async def test_search_excludes_blocked_project(
    connect_as: Any, fake: FakeJira, auth: str
) -> None:
    """The JQL sent to Jira excludes the blocked project."""
    client, _ = await connect_as(auth, blocked="LEGAL")
    await client.call_tool("search", {"jql": SEARCH_JQL})
    assert fake.jql_sent == [f"({SEARCH_JQL}) AND project != LEGAL"]


@pytest.mark.anyio
@pytest.mark.parametrize("auth", PER_REQUEST_AUTHS)
async def test_readonly_project_reads_allowed(
    connect_as: Any, fake: FakeJira, auth: str
) -> None:
    """A read-only project can be read, and the list reaches the fetcher."""
    client, request = await connect_as(auth, blocked=None, readonly="LEGAL")
    result = await client.call_tool("get_issue", {"issue_key": BLOCKED_ISSUE})
    assert json.loads(_text(result))["key"] == BLOCKED_ISSUE
    await client.call_tool("search", {"jql": SEARCH_JQL})
    assert fake.jql_sent == [SEARCH_JQL]
    assert request.state.jira_fetcher.config.projects_readonly_set == {"LEGAL"}


@pytest.mark.anyio
async def test_header_pat_without_server_config_has_no_lists(
    connect_as: Any, fake: FakeJira
) -> None:
    """With no global Jira config there are no lists, and requests proceed."""
    client, request = await connect_as(HEADER_PAT, with_server_config=False)
    result = await client.call_tool("get_issue", {"issue_key": BLOCKED_ISSUE})
    assert json.loads(_text(result))["key"] == BLOCKED_ISSUE
    await client.call_tool("search", {"jql": SEARCH_JQL})
    assert fake.jql_sent == [SEARCH_JQL]
    config = request.state.jira_fetcher.config
    assert config.projects_blocked is None
    assert config.projects_readonly is None


@pytest.mark.anyio
@pytest.mark.parametrize("auth", PER_REQUEST_AUTHS)
async def test_no_lists_makes_no_extra_requests(
    connect_as: Any, fake: FakeJira, auth: str
) -> None:
    """With no lists configured, the requests sent are the same as before."""
    client, _ = await connect_as(auth, blocked=None, readonly=None)
    await client.call_tool("get_issue", {"issue_key": BLOCKED_ISSUE})
    await client.call_tool("search", {"jql": SEARCH_JQL})
    search_path = "/rest/api/3/search/jql" if auth == OAUTH else "/rest/api/2/search"
    search_method = "POST" if auth == OAUTH else "GET"
    assert fake.requests == [
        ("GET", "/rest/api/2/myself"),
        ("GET", f"/rest/api/2/issue/{BLOCKED_ISSUE}"),
        ("GET", "/rest/api/2/field"),
        (search_method, search_path),
    ]
    assert fake.jql_sent == [SEARCH_JQL]
