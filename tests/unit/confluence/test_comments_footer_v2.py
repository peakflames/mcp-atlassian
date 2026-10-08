"""Comment writes routed by deployment and auth type, tested at the HTTP session.

Cloud OAuth goes through the Atlassian API gateway, which is removing the v1
content endpoints, so ``add_comment`` and ``reply_to_comment`` post to the v2
``/api/v2/footer-comments`` endpoint. Server/Data Center and Cloud without
OAuth keep the v1 ``/rest/api/content/`` endpoint.

HTTP is faked only at ``requests.Session.request``; client construction is
stubbed (``ConfluenceClient.__init__`` is bypassed and a real ``Confluence``
client, ``ConfluenceConfig`` and preprocessor are attached), and the server
tests patch ``get_confluence_fetcher`` to return that fetcher. The v2 adapter
reaches the session through ``Session.post`` and the ``atlassian`` client calls
``Session.request`` directly, so path selection, request construction, the
preprocessor and the response mapping run unmodified.
"""

import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, Literal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import requests
from atlassian import Confluence
from fastmcp import Client, FastMCP
from fastmcp.client import FastMCPTransport
from fastmcp.client.client import CallToolResult
from fastmcp.exceptions import ToolError
from mcp.types import TextContent

from mcp_atlassian.confluence import ConfluenceFetcher
from mcp_atlassian.confluence.config import ConfluenceConfig
from mcp_atlassian.preprocessing.confluence import ConfluencePreprocessor
from mcp_atlassian.servers.context import MainAppContext
from mcp_atlassian.servers.main import AtlassianMCP
from mcp_atlassian.utils.oauth import OAuthConfig

GATEWAY_URL = "https://api.atlassian.com/ex/confluence/test-cloud-id"
CLOUD_SITE_URL = "https://example.atlassian.net/wiki"
SERVER_URL = "https://confluence.example.com"
V2_FOOTER_COMMENTS = f"{GATEWAY_URL}/api/v2/footer-comments"

AuthType = Literal["basic", "pat", "oauth"]

# 201 body of POST /wiki/api/v2/footer-comments (FooterCommentModel). The v2
# model has no ``author`` object; the creator is only ``version.authorId``.
V2_COMMENT: dict[str, Any] = {
    "id": "900",
    "status": "current",
    "title": "Re: Release notes",
    "pageId": "123",
    "version": {
        "number": 1,
        "createdAt": "2026-01-05T10:00:00.000Z",
        "authorId": "account-1",
        "minorEdit": False,
        "message": "",
    },
    "body": {
        "storage": {
            "representation": "storage",
            "value": "<p>Looks <strong>good</strong></p>",
        }
    },
    "_links": {"webui": "/spaces/ENG/pages/123?focusedCommentId=900"},
}
V2_REPLY: dict[str, Any] = {
    **{k: v for k, v in V2_COMMENT.items() if k != "pageId"},
    "id": "901",
    "parentCommentId": "900",
    "body": {"storage": {"representation": "storage", "value": "<p>Agreed</p>"}},
}

# Response of POST /rest/api/content/ (v1) for a page comment and a reply.
V1_COMMENT: dict[str, Any] = {
    "id": "800",
    "type": "comment",
    "status": "current",
    "title": "Re: Release notes",
    "container": {"id": "123", "type": "page", "title": "Release notes"},
    "version": {
        "number": 1,
        "by": {"accountId": "account-1", "displayName": "Test User"},
    },
    "extensions": {"location": "footer"},
    "body": {"storage": {"representation": "storage", "value": "<p>Agreed</p>"}},
}
V1_REPLY: dict[str, Any] = {
    **V1_COMMENT,
    "id": "801",
    "container": {"id": "700", "type": "comment", "title": "Re: Release notes"},
}

# (auth_type, is_cloud, site URL) combinations that must stay on v1.
V1_DEPLOYMENTS = [
    pytest.param("basic", False, SERVER_URL, id="server-dc-basic"),
    pytest.param("pat", False, SERVER_URL, id="server-dc-pat"),
    pytest.param("oauth", False, SERVER_URL, id="data-center-oauth"),
    pytest.param("basic", True, CLOUD_SITE_URL, id="cloud-api-token"),
]


def _json_response(
    method: str, url: str, status: int, payload: dict[str, Any]
) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response.reason = "OK" if status < 400 else "Forbidden"
    response.url = url
    response.headers["Content-Type"] = "application/json"
    response._content = json.dumps(payload).encode()
    response.request = requests.Request(method, url).prepare()
    return response


class _FakeHttp:
    """Answers ``Session.request`` by (method, URL suffix) and records calls."""

    def __init__(self, routes: dict[tuple[str, str], tuple[int, dict[str, Any]]]):
        self.routes = routes
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> requests.Response:
        # Session.post() calls request(method, url, ...) positionally; the
        # atlassian client passes method= and url= as keywords.
        method = args[0] if args else kwargs.pop("method")
        url = args[1] if len(args) > 1 else kwargs.pop("url")
        self.calls.append((method, url, kwargs))
        path = url.split("?", 1)[0]
        for (route_method, suffix), (status, payload) in self.routes.items():
            if method == route_method and path.endswith(suffix):
                return _json_response(method, url, status, payload)
        msg = f"Unexpected HTTP call: {method} {url}"
        raise AssertionError(msg)

    def only_call(self) -> tuple[str, str, dict[str, Any]]:
        assert len(self.calls) == 1, self.calls
        return self.calls[0]

    def posts(self) -> list[tuple[str, dict[str, Any]]]:
        """POSTs as (url, JSON payload), whichever way the payload was sent."""
        result = []
        for method, url, kwargs in self.calls:
            if method != "POST":
                continue
            payload = kwargs.get("json")
            if payload is None:
                payload = json.loads(kwargs["data"])
            result.append((url, payload))
        return result


def _config(auth_type: AuthType, *, is_cloud: bool, site_url: str) -> ConfluenceConfig:
    """Build a real config whose computed ``is_cloud`` matches the scenario."""
    oauth_config = None
    if auth_type == "oauth":
        oauth_config = OAuthConfig(
            client_id="client-id",
            client_secret="client-secret",
            redirect_uri="http://localhost",
            scope="write:comment:confluence",
            cloud_id="test-cloud-id" if is_cloud else None,
            base_url=None if is_cloud else site_url,
        )
    config = ConfluenceConfig(
        url=site_url, auth_type=auth_type, oauth_config=oauth_config
    )
    assert config.is_cloud is is_cloud
    return config


def _fetcher(
    http: _FakeHttp,
    *,
    auth_type: AuthType = "oauth",
    is_cloud: bool = True,
    site_url: str = CLOUD_SITE_URL,
) -> ConfluenceFetcher:
    """Build a fetcher with HTTP faked at ``Session.request``.

    ``ConfluenceClient.__init__`` is bypassed (it would configure real auth);
    a real ``Confluence`` client, ``ConfluenceConfig`` and preprocessor are
    attached instead.
    """
    session = requests.Session()
    session.request = MagicMock(side_effect=http)  # type: ignore[method-assign]
    gateway = auth_type == "oauth" and is_cloud
    with patch("mcp_atlassian.confluence.client.ConfluenceClient.__init__") as init:
        init.return_value = None
        fetcher = ConfluenceFetcher()
    fetcher.config = _config(auth_type, is_cloud=is_cloud, site_url=site_url)
    fetcher.confluence = Confluence(
        url=GATEWAY_URL if gateway else site_url, session=session, cloud=is_cloud
    )
    fetcher.preprocessor = ConfluencePreprocessor(base_url=site_url)
    return fetcher


class TestCloudOAuthUsesV2FooterComments:
    def test_add_comment_posts_page_id_and_storage_body(self) -> None:
        http = _FakeHttp({("POST", "/api/v2/footer-comments"): (201, V2_COMMENT)})

        _fetcher(http).add_comment("123", "Looks **good**")

        method, url, kwargs = http.only_call()
        assert (method, url) == ("POST", V2_FOOTER_COMMENTS)
        payload = kwargs["json"]
        assert set(payload) == {"pageId", "body"}
        assert payload["pageId"] == "123"
        assert payload["body"]["representation"] == "storage"
        assert "<strong>good</strong>" in payload["body"]["value"]

    def test_reply_posts_parent_comment_id_only(self) -> None:
        http = _FakeHttp({("POST", "/api/v2/footer-comments"): (201, V2_REPLY)})

        _fetcher(http).reply_to_comment("900", "<p>Agreed</p>")

        method, url, kwargs = http.only_call()
        assert (method, url) == ("POST", V2_FOOTER_COMMENTS)
        assert kwargs["json"] == {
            "parentCommentId": "900",
            "body": {"representation": "storage", "value": "<p>Agreed</p>"},
        }

    def test_add_comment_maps_v2_response_to_comment_model(self) -> None:
        http = _FakeHttp({("POST", "/api/v2/footer-comments"): (201, V2_COMMENT)})

        comment = _fetcher(http).add_comment("123", "Looks **good**")

        assert comment is not None
        assert comment.id == "900"
        assert comment.type == "comment"
        assert comment.title == "Re: Release notes"
        assert comment.body.strip() == "Looks **good**"
        assert comment.location == "footer"
        assert comment.parent_comment_id is None

    def test_reply_maps_parent_comment_id(self) -> None:
        http = _FakeHttp({("POST", "/api/v2/footer-comments"): (201, V2_REPLY)})

        comment = _fetcher(http).reply_to_comment("900", "Agreed")

        assert comment is not None
        simplified = comment.to_simplified_dict()
        assert simplified["id"] == "901"
        assert simplified["parent_comment_id"] == "900"
        assert simplified["location"] == "footer"
        assert simplified["body"].strip() == "Agreed"

    def test_http_error_returns_none_without_raising(self) -> None:
        http = _FakeHttp(
            {("POST", "/api/v2/footer-comments"): (403, {"message": "Forbidden"})}
        )

        assert _fetcher(http).add_comment("123", "text") is None
        assert _fetcher(http).reply_to_comment("900", "text") is None


class TestOtherDeploymentsStayOnV1:
    @pytest.mark.parametrize(("auth_type", "is_cloud", "site_url"), V1_DEPLOYMENTS)
    def test_add_comment_uses_v1_content_endpoint(
        self,
        auth_type: AuthType,
        is_cloud: bool,  # noqa: FBT001
        site_url: str,
    ) -> None:
        http = _FakeHttp(
            {
                ("GET", "/rest/api/content/123"): (
                    200,
                    {"id": "123", "space": {"key": "ENG"}},
                ),
                ("POST", "/rest/api/content"): (200, V1_COMMENT),
            }
        )
        fetcher = _fetcher(
            http, auth_type=auth_type, is_cloud=is_cloud, site_url=site_url
        )

        comment = fetcher.add_comment("123", "<p>Agreed</p>")

        assert all("/api/v2/" not in url for _, url, _ in http.calls)
        [(url, payload)] = http.posts()
        assert url == f"{site_url}/rest/api/content"
        assert payload["type"] == "comment"
        assert payload["container"]["id"] == "123"
        assert payload["container"]["type"] == "page"
        assert payload["body"]["storage"] == {
            "value": "<p>Agreed</p>",
            "representation": "storage",
        }
        assert comment is not None
        assert comment.id == "800"
        assert comment.author is not None
        assert comment.author.display_name == "Test User"

    @pytest.mark.parametrize(("auth_type", "is_cloud", "site_url"), V1_DEPLOYMENTS)
    def test_reply_uses_v1_content_endpoint(
        self,
        auth_type: AuthType,
        is_cloud: bool,  # noqa: FBT001
        site_url: str,
    ) -> None:
        http = _FakeHttp({("POST", "/rest/api/content"): (200, V1_REPLY)})
        fetcher = _fetcher(
            http, auth_type=auth_type, is_cloud=is_cloud, site_url=site_url
        )

        comment = fetcher.reply_to_comment("700", "<p>Agreed</p>")

        [(url, payload)] = http.posts()
        assert url == f"{site_url}/rest/api/content"
        assert payload["container"] == {"id": "700", "type": "comment"}
        assert payload["body"]["storage"]["representation"] == "storage"
        assert comment is not None
        assert comment.parent_comment_id == "700"


def _comment_server(*, read_only: bool) -> AtlassianMCP:
    from mcp_atlassian.servers.confluence import add_comment, reply_to_comment

    config = _config("oauth", is_cloud=True, site_url=CLOUD_SITE_URL)

    @asynccontextmanager
    async def lifespan(app: FastMCP) -> AsyncGenerator[dict[str, MainAppContext], None]:
        # Same shape as the production lifespan in servers/main.py.
        yield {
            "app_lifespan_context": MainAppContext(
                full_confluence_config=config, read_only=read_only
            )
        }

    server = AtlassianMCP("TestComments", lifespan=lifespan)
    tools = FastMCP(name="TestCommentTools")
    tools.add_tool(add_comment)
    tools.add_tool(reply_to_comment)
    server.mount(tools, prefix="confluence")
    return server


@asynccontextmanager
async def _client(
    fetcher: ConfluenceFetcher, *, read_only: bool = False
) -> AsyncGenerator[Client, None]:
    with patch(
        "mcp_atlassian.servers.confluence.get_confluence_fetcher",
        AsyncMock(return_value=fetcher),
    ):
        async with Client(
            transport=FastMCPTransport(_comment_server(read_only=read_only))
        ) as client:
            yield client


def _tool_text(result: CallToolResult) -> str:
    [content] = result.content
    assert isinstance(content, TextContent)
    return content.text


class TestCommentToolsUnderCloudOAuth:
    @pytest.mark.anyio
    async def test_reply_tool_returns_mapped_v2_comment(self) -> None:
        http = _FakeHttp({("POST", "/api/v2/footer-comments"): (201, V2_REPLY)})

        async with _client(_fetcher(http)) as client:
            result = await client.call_tool(
                "confluence_reply_to_comment", {"comment_id": "900", "body": "Agreed"}
            )

        data = json.loads(_tool_text(result))
        assert data["success"] is True
        assert data["comment"]["id"] == "901"
        assert data["comment"]["parent_comment_id"] == "900"
        assert data["comment"]["location"] == "footer"
        method, url, _ = http.only_call()
        assert (method, url) == ("POST", V2_FOOTER_COMMENTS)

    @pytest.mark.anyio
    async def test_add_tool_reports_v2_http_error_as_json(self) -> None:
        http = _FakeHttp(
            {("POST", "/api/v2/footer-comments"): (403, {"message": "Forbidden"})}
        )

        async with _client(_fetcher(http)) as client:
            result = await client.call_tool(
                "confluence_add_comment", {"page_id": "123", "body": "text"}
            )

        assert result.is_error is False
        text = _tool_text(result)
        assert "Traceback" not in text
        assert json.loads(text)["success"] is False

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("tool", "arguments"),
        [
            ("confluence_add_comment", {"page_id": "123", "body": "text"}),
            ("confluence_reply_to_comment", {"comment_id": "900", "body": "text"}),
        ],
    )
    async def test_read_only_mode_blocks_before_any_http_call(
        self, tool: str, arguments: dict[str, str]
    ) -> None:
        http = _FakeHttp({})

        async with _client(_fetcher(http), read_only=True) as client:
            with pytest.raises(ToolError, match="read-only mode"):
                await client.call_tool(tool, arguments)

        assert http.calls == []
