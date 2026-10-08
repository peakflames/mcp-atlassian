"""Per-space access control on page, label, attachment, and comment tools.

HTTP is faked only at ``requests.Session.request``. The fetcher is a real
ConfluenceFetcher built from a real ConfluenceConfig; the server's
``get_confluence_fetcher`` is patched to return it. Where a tool exists, it
is called through a FastMCP client. The fake answers both the v1 REST API
(basic auth, Server/Data Center or Cloud) and the v2 API behind the Cloud
OAuth gateway, where v1 content endpoints return 410.

The per-request credential tests at the end do not patch
``get_confluence_fetcher``: the request it sees is the one the real
``UserTokenMiddleware`` passes on.

Pages:
    100 lives in LEGAL (blocked), 200 lives in ENG (allowed), and the space
    of 300 cannot be determined. Comment 1xxx sits on page xxx. Folder 500
    lives in ENG and folder 600 in LEGAL; 700 is no known content.
"""

import base64
import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from fastmcp import Client, FastMCP
from fastmcp.client import FastMCPTransport
from fastmcp.exceptions import ToolError
from starlette.requests import Request

from mcp_atlassian.confluence import ConfluenceFetcher
from mcp_atlassian.confluence.config import ConfluenceConfig
from mcp_atlassian.servers.confluence import (
    add_comment,
    add_label,
    create_page,
    delete_page,
    download_attachment,
    download_content_attachments,
    get_attachments,
    get_comments,
    get_labels,
    get_page,
    get_page_children,
    get_page_diff,
    get_page_history,
    get_page_images,
    get_page_views,
    get_space_page_tree,
    move_page,
    reply_to_comment,
    update_page,
)
from mcp_atlassian.servers.context import MainAppContext
from mcp_atlassian.servers.main import UserTokenMiddleware
from mcp_atlassian.utils.access_control import (
    ProjectAccessError,
    check_confluence_content_space_access,
)
from mcp_atlassian.utils.oauth import BYOAccessTokenOAuthConfig, OAuthConfig

SERVER_URL = "https://confluence.example.com"
CLOUD_ID = "test-cloud-id"
GATEWAY_PREFIX = f"/ex/confluence/{CLOUD_ID}"

# page id -> (space id, space key or None when the key cannot be read)
PAGES: dict[str, tuple[str, str | None]] = {
    "100": ("9001", "LEGAL"),
    "200": ("9002", "ENG"),
    "300": ("9003", None),
    "400": ("9004", "OPS"),
}
# folder id -> (space id, space key)
FOLDERS: dict[str, tuple[str, str]] = {
    "500": ("9002", "ENG"),
    "600": ("9001", "LEGAL"),
}
# folder id -> status the v2 folder lookup fails with
FOLDER_ERRORS = {"8401": 401, "8403": 403, "8500": 500}
# comment id -> page id
COMMENTS = {f"1{page_id}": page_id for page_id in PAGES}
# space key the title search is asked for -> page returned. ENGX stands in
# for a key the server maps to a page that actually lives in LEGAL; ENGY for
# one mapped to a page whose space cannot be read.
TITLE_SEARCH = {"LEGAL": "100", "ENG": "200", "ENGX": "100", "ENGY": "300"}
SPACE_PAGES = {"LEGAL": "100", "ENG": "200"}
SECRET_BODY = "<p>secret body</p>"
SECRET_LABEL = "secret-label"
SECRET_CHILD = "Secret child page"
VIEW_COUNT = 42
IMAGE_BYTES = b"\x89PNG\r\n\x1a\n"

V1 = "v1"
V2 = "v2"
MODES = [V1, V2]
BLOCKED_PAGE = "100"
ALLOWED_PAGE = "200"
UNRESOLVABLE_PAGE = "300"
ALLOWED_FOLDER = "500"
BLOCKED_FOLDER = "600"
UNKNOWN_CONTENT = "700"

# Paths that return labels, attachment metadata, or attachment bytes.
DATA_PATH = re.compile(r"/label|/labels|/child/attachment|/attachments$|/download/")


def _response(
    status: int, payload: Any = None, *, content: bytes | None = None
) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response.reason = "OK" if status < 400 else "Error"
    if content is None:
        content = json.dumps(payload if payload is not None else {}).encode()
        response.headers["Content-Type"] = "application/json"
    response._content = content
    response._content_consumed = True  # lets iter_content() replay _content
    response.encoding = "utf-8"
    return response


def _attachment_v1(page_id: str) -> dict[str, Any]:
    space_key = PAGES[page_id][1]
    attachment: dict[str, Any] = {
        "id": f"att{page_id}",
        "type": "attachment",
        "title": "secret.png",
        "extensions": {"mediaType": "image/png", "fileSize": len(IMAGE_BYTES)},
        "_links": {"download": f"/download/attachments/{page_id}/secret.png"},
    }
    if space_key:
        attachment["space"] = {"key": space_key}
    return attachment


def _page_v1(page_id: str) -> dict[str, Any]:
    space_key = PAGES[page_id][1]
    page: dict[str, Any] = {
        "id": page_id,
        "type": "page",
        "status": "current",
        "title": f"Page {page_id}",
        "version": {"number": 1},
        "body": {"storage": {"value": SECRET_BODY}},
        "children": {"attachment": {"results": []}},
        "_links": {"webui": f"/pages/{page_id}"},
    }
    if space_key:
        page["space"] = {"key": space_key, "name": space_key}
    return page


class FakeConfluence:
    """Answers the v1 and v2 endpoints used by the tools under test."""

    def __init__(self) -> None:
        self.paths: list[str] = []
        self.requests: list[tuple[str, dict[str, list[str]]]] = []
        self.writes: list[str] = []

    def __call__(self, *args: Any, **kwargs: Any) -> requests.Response:
        method = kwargs.get("method") or args[0]
        url = kwargs.get("url") or args[1]
        split = urlsplit(url)
        path = split.path
        via_gateway = path.startswith(GATEWAY_PREFIX)
        if via_gateway:
            path = path[len(GATEWAY_PREFIX) :]
        elif path.startswith("/wiki/"):
            # Cloud site URLs serve the REST API under /wiki.
            path = path[len("/wiki") :]
        self.paths.append(path)
        query = parse_qs(split.query)
        for key, value in (kwargs.get("params") or {}).items():
            query[key] = [str(value)]
        self.requests.append((path, query))
        if method != "GET":
            # Accept any write so that a missing check shows up as a success.
            self.writes.append(f"{method} {path}")
            return _response(
                200,
                {
                    "id": "999",
                    "type": "comment",
                    "status": "current",
                    "title": "Re: comment",
                    "body": {"storage": {"value": "<p>reply</p>"}},
                    "version": {"number": 1},
                },
            )
        if path == "/rest/api/user/current":
            # Credential check made when a per-request fetcher is created.
            return _response(200, {"displayName": "Test User"})
        if (
            via_gateway
            and path.startswith("/rest/api/")
            and not path.startswith("/rest/api/analytics/")
        ):
            # The Cloud OAuth gateway no longer serves v1 content reads. View
            # statistics have no v2 endpoint and are read from v1 in all modes.
            return _response(410, {"message": "Gone"})
        return self._route(path, query)

    def data_paths(self) -> list[str]:
        return [p for p in self.paths if DATA_PATH.search(p)]

    def _route(self, path: str, query: dict[str, list[str]]) -> requests.Response:
        if path.endswith("/secret.png") and "/download/attachments/" in path:
            return _response(200, content=IMAGE_BYTES)

        # ---- v2 (Cloud OAuth gateway) ----
        if path == "/api/v2/spaces":
            wanted = query.get("keys", [""])[0]
            found = [
                {"id": space_id, "key": key}
                for space_id, key in PAGES.values()
                if key == wanted
            ]
            return _response(200, {"results": found})
        if m := re.fullmatch(r"/api/v2/spaces/(\d+)", path):
            for space_id, key in PAGES.values():
                if space_id == m[1]:
                    if key is None:
                        return _response(403, {"message": "forbidden"})
                    return _response(200, {"id": space_id, "key": key})
            return _response(404)
        folder = re.fullmatch(r"/api/v2/folders/(\d+)", path)
        if folder and folder[1] in FOLDER_ERRORS:
            return _response(FOLDER_ERRORS[folder[1]], {"message": "error"})
        if (m := re.fullmatch(r"/api/v2/folders/(\d+)", path)) and m[1] in FOLDERS:
            return _response(
                200, {"id": m[1], "type": "folder", "spaceId": FOLDERS[m[1]][0]}
            )
        if (m := re.fullmatch(r"/api/v2/pages/(\d+)", path)) and m[1] in PAGES:
            space_id, _ = PAGES[m[1]]
            return _response(
                200,
                {
                    "id": m[1],
                    "status": "current",
                    "title": f"Page {m[1]}",
                    "spaceId": space_id,
                    "version": {"number": 1},
                    "body": {"storage": {"value": SECRET_BODY}},
                },
            )
        if m := re.fullmatch(r"/api/v2/pages/(\d+)/labels", path):
            return _response(200, {"results": [{"id": "1", "name": SECRET_LABEL}]})
        if m := re.fullmatch(r"/api/v2/pages/(\d+)/attachments", path):
            return _response(
                200,
                {
                    "results": [
                        {
                            "id": f"att{m[1]}",
                            "title": "secret.png",
                            "mediaType": "image/png",
                            "fileSize": len(IMAGE_BYTES),
                            "_links": {
                                "download": f"/download/attachments/{m[1]}/secret.png"
                            },
                        }
                    ]
                },
            )
        if m := re.fullmatch(r"/api/v2/pages/(\d+)/versions", path):
            versions = [{"id": f"{m[1]}v{n}", "number": n} for n in (1, 2)]
            return _response(200, {"results": versions})
        if m := re.fullmatch(r"/api/v2/versions/(\d+)v(\d+)", path):
            return _response(
                200,
                {
                    "id": m[1],
                    "number": int(m[2]),
                    "status": "current",
                    "title": f"Page {m[1]}",
                    "spaceId": PAGES[m[1]][0],
                    "body": {"storage": {"value": SECRET_BODY}},
                },
            )
        if m := re.fullmatch(r"/api/v2/pages/(\d+)/direct-children", path):
            # Used when page children are listed through the v2 API.
            child = {
                "id": f"5{m[1]}",
                "type": "page",
                "status": "current",
                "title": SECRET_CHILD,
                "spaceId": PAGES[m[1]][0],
            }
            return _response(200, {"results": [child], "_links": {}})
        if path == "/api/v2/pages":
            return _response(200, {"results": []})
        if m := re.fullmatch(r"/api/v2/footer-comments/(\d+)", path):
            return _response(200, {"id": m[1], "pageId": COMMENTS[m[1]]})
        if m := re.fullmatch(r"/api/v2/attachments/att(\d+)", path):
            return _response(
                200,
                {
                    "id": f"att{m[1]}",
                    "pageId": m[1],
                    "title": "secret.png",
                    "mediaType": "image/png",
                    "fileSize": len(IMAGE_BYTES),
                    "_links": {"download": f"/download/attachments/{m[1]}/secret.png"},
                },
            )

        # ---- v1 REST API ----
        if (m := re.fullmatch(r"/rest/api/content/(\d+)", path)) and m[1] in COMMENTS:
            comment: dict[str, Any] = {"id": m[1], "type": "comment"}
            if space_key := PAGES[COMMENTS[m[1]]][1]:
                comment["space"] = {"key": space_key}
            return _response(200, comment)
        if (m := re.fullmatch(r"/rest/api/content/(\d+)", path)) and m[1] in FOLDERS:
            folder_key = FOLDERS[m[1]][1]
            return _response(
                200, {"id": m[1], "type": "folder", "space": {"key": folder_key}}
            )
        if (m := re.fullmatch(r"/rest/api/content/(\d+)", path)) and m[1] in PAGES:
            return _response(200, _page_v1(m[1]))
        if path == "/rest/api/content":
            space = query.get("spaceKey", [""])[0].strip().upper()
            if "title" in query:
                page_id = TITLE_SEARCH.get(space)
                results = [_page_v1(page_id)] if page_id else []
                return _response(200, {"results": results})
            page_id = SPACE_PAGES.get(space)
            tree = (
                [
                    {
                        "id": page_id,
                        "title": f"Page {page_id}",
                        "ancestors": [],
                        "extensions": {"position": 0},
                    }
                ]
                if page_id
                else []
            )
            return _response(200, {"results": tree, "_links": {}})
        if re.fullmatch(r"/rest/api/content/(\d+)/history", path):
            return _response(200, {"lastUpdated": {"number": 1}})
        if m := re.fullmatch(r"/rest/api/content/(\d+)/child/comment", path):
            return _response(
                200,
                {
                    "results": [
                        {
                            "id": f"1{m[1]}",
                            "type": "comment",
                            "body": {"view": {"value": "<p>secret comment</p>"}},
                            "version": {"number": 1},
                        }
                    ]
                },
            )
        if m := re.fullmatch(r"/rest/api/content/att(\d+)", path):
            return _response(200, _attachment_v1(m[1]))
        if m := re.fullmatch(r"/rest/api/content/(\d+)/label", path):
            return _response(
                200,
                {
                    "results": [{"id": "1", "name": SECRET_LABEL, "prefix": "global"}],
                    "size": 1,
                },
            )
        if m := re.fullmatch(r"/rest/api/content/(\d+)/child/attachment", path):
            return _response(200, {"results": [_attachment_v1(m[1])], "size": 1})
        if m := re.fullmatch(r"/rest/api/content/(\d+)/child/(page|folder)", path):
            children = []
            if m[2] == "page":
                children = [{**_page_v1(m[1]), "id": f"5{m[1]}", "title": SECRET_CHILD}]
            return _response(200, {"results": children, "size": len(children)})
        if re.fullmatch(r"/rest/api/analytics/content/(\d+)/views", path):
            return _response(200, {"count": VIEW_COUNT})

        return _response(404, {"message": f"no route for {path}"})


def _config(
    mode: str,
    *,
    blocked: str | None,
    readonly: str | None = None,
    url: str = SERVER_URL,
) -> ConfluenceConfig:
    """``url`` applies to v1 only; the OAuth config is always Cloud."""
    if mode == V1:
        return ConfluenceConfig(
            url=url,
            auth_type="basic",
            username="test-user",
            api_token="test-token",
            spaces_blocked=blocked,
            spaces_readonly=readonly,
        )
    return ConfluenceConfig(
        url=SERVER_URL,
        auth_type="oauth",
        oauth_config=BYOAccessTokenOAuthConfig("test-access-token", CLOUD_ID),
        spaces_blocked=blocked,
        spaces_readonly=readonly,
    )


def _fetcher(
    mode: str,
    *,
    blocked: str | None,
    readonly: str | None = None,
    url: str = SERVER_URL,
) -> ConfluenceFetcher:
    fetcher = ConfluenceFetcher(
        config=_config(mode, blocked=blocked, readonly=readonly, url=url)
    )
    assert (fetcher._v2_adapter is not None) is (mode == V2)
    return fetcher


@pytest.fixture
def fake() -> FakeConfluence:
    return FakeConfluence()


@pytest.fixture
def http(fake: FakeConfluence) -> Iterator[None]:
    """Route every requests.Session.request call to the fake Confluence."""
    with patch.object(
        requests.Session,
        "request",
        autospec=True,
        side_effect=lambda _self, *args, **kwargs: fake(*args, **kwargs),
    ):
        yield


def _server(server_config: ConfluenceConfig | None) -> FastMCP:
    """Build a server holding the tools under test.

    The lifespan context uses the same ``{"app_lifespan_context": ...}`` shape
    as the real server, so ``check_write_access`` runs on write tools.
    """

    @asynccontextmanager
    async def lifespan(_app: FastMCP) -> AsyncIterator[dict[str, Any]]:
        yield {
            "app_lifespan_context": MainAppContext(
                full_confluence_config=server_config, read_only=False
            )
        }

    mcp = FastMCP(name="SpaceAccessTest", lifespan=lifespan)
    for tool in (
        get_page,
        get_labels,
        get_attachments,
        download_attachment,
        download_content_attachments,
        get_page_images,
        get_comments,
        get_space_page_tree,
        get_page_children,
        get_page_history,
        get_page_diff,
        get_page_views,
        reply_to_comment,
        add_comment,
        add_label,
        create_page,
        update_page,
        delete_page,
        move_page,
    ):
        mcp.add_tool(tool)
    return mcp


@pytest.fixture
async def connect(http: None) -> AsyncIterator[Callable[..., Awaitable[Client]]]:
    """Yield ``connect(mode, blocked=..., readonly=...)`` -> connected client.

    ``server_config`` replaces the server's global config; by default it is
    the fetcher's own config. ``url`` is the v1 site URL.
    """
    async with AsyncExitStack() as stack:

        async def factory(
            mode: str,
            blocked: str | None = "LEGAL",
            readonly: str | None = None,
            *,
            server_config: ConfluenceConfig | None = None,
            url: str = SERVER_URL,
        ) -> Client:
            fetcher = _fetcher(mode, blocked=blocked, readonly=readonly, url=url)
            mcp = _server(server_config or fetcher.config)
            # Tools and check_write_access look the fetcher up separately.
            for target in (
                "mcp_atlassian.servers.confluence.get_confluence_fetcher",
                "mcp_atlassian.servers.dependencies.get_confluence_fetcher",
            ):
                stack.enter_context(patch(target, AsyncMock(return_value=fetcher)))
            client = Client(transport=FastMCPTransport(mcp))
            return await stack.enter_async_context(client)

        yield factory


def _text(result: Any) -> str:
    return "".join(getattr(item, "text", "") for item in result.content)


# ---------------------------------------------------------------------------
# get_labels / get_attachments / get_page x {blocked, unresolvable} x {v1, v2}
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
async def test_get_labels_blocked_space_denied(
    connect: Any, fake: FakeConfluence, mode: str
) -> None:
    client = await connect(mode)
    with pytest.raises(ToolError, match="LEGAL.*blocked"):
        await client.call_tool("get_labels", {"page_id": BLOCKED_PAGE})
    assert fake.data_paths() == []


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
async def test_get_labels_unresolvable_space_denied(
    connect: Any, fake: FakeConfluence, mode: str
) -> None:
    client = await connect(mode)
    with pytest.raises(ToolError, match="Could not determine the space"):
        await client.call_tool("get_labels", {"page_id": UNRESOLVABLE_PAGE})
    assert fake.data_paths() == []


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
async def test_get_attachments_blocked_space_denied(
    connect: Any, fake: FakeConfluence, mode: str
) -> None:
    client = await connect(mode)
    with pytest.raises(ToolError, match="LEGAL.*blocked"):
        await client.call_tool("get_attachments", {"content_id": BLOCKED_PAGE})
    assert fake.data_paths() == []


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
async def test_get_attachments_unresolvable_space_denied(
    connect: Any, fake: FakeConfluence, mode: str
) -> None:
    client = await connect(mode)
    with pytest.raises(ToolError, match="Could not determine the space"):
        await client.call_tool("get_attachments", {"content_id": UNRESOLVABLE_PAGE})
    assert fake.data_paths() == []


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
async def test_get_page_blocked_space_denied(
    connect: Any, fake: FakeConfluence, mode: str
) -> None:
    client = await connect(mode)
    result = await client.call_tool("get_page", {"page_id": BLOCKED_PAGE})
    text = _text(result)
    assert "secret body" not in text
    assert "blocked" in json.loads(text)["error"]


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
async def test_get_page_unresolvable_space_denied(
    connect: Any, fake: FakeConfluence, mode: str
) -> None:
    """The page's space cannot be determined."""
    client = await connect(mode)
    result = await client.call_tool("get_page", {"page_id": UNRESOLVABLE_PAGE})
    text = _text(result)
    assert "secret body" not in text
    assert "Could not determine the space" in json.loads(text)["error"]


@pytest.mark.anyio
async def test_get_page_by_title_in_blocked_space_denied(
    connect: Any, fake: FakeConfluence
) -> None:
    client = await connect(V1)
    with pytest.raises(ToolError, match="'legal' is blocked"):
        await client.call_tool("get_page", {"title": "Any", "space_key": "legal"})
    assert fake.paths == []


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("space_key", ["  legal ", "LeGaL\t", " LEGAL"])
async def test_get_page_by_title_padded_or_case_variant_key_denied(
    connect: Any, fake: FakeConfluence, mode: str, space_key: str
) -> None:
    client = await connect(mode)
    with pytest.raises(ToolError, match="is blocked"):
        await client.call_tool("get_page", {"title": "Any", "space_key": space_key})
    assert fake.paths == []


@pytest.mark.anyio
async def test_get_page_by_title_checks_returned_page_space(
    connect: Any, fake: FakeConfluence
) -> None:
    """The page returned for an unlisted key is checked against its own space."""
    client = await connect(V1)
    with pytest.raises(ToolError, match="'LEGAL' is blocked"):
        await client.call_tool("get_page", {"title": "Any", "space_key": "ENGX"})


@pytest.mark.anyio
async def test_get_page_by_title_unknown_returned_space_denied(
    connect: Any, fake: FakeConfluence
) -> None:
    """A page returned without a readable space is not served."""
    client = await connect(V1)
    with pytest.raises(ToolError, match="Could not determine the space of content"):
        await client.call_tool("get_page", {"title": "Any", "space_key": "ENGY"})


@pytest.mark.anyio
async def test_get_page_by_title_allowed_space_served(
    connect: Any, fake: FakeConfluence
) -> None:
    client = await connect(V1)
    page = json.loads(
        _text(await client.call_tool("get_page", {"title": "Any", "space_key": "ENG"}))
    )
    assert "secret body" in page["metadata"]["content"]["value"]


# ---------------------------------------------------------------------------
# get_space_page_tree and get_comments
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("space_key", ["LEGAL", " legal "])
async def test_space_page_tree_blocked_space_denied(
    connect: Any, fake: FakeConfluence, mode: str, space_key: str
) -> None:
    client = await connect(mode)
    with pytest.raises(ToolError, match="is blocked"):
        await client.call_tool("get_space_page_tree", {"space_key": space_key})
    assert fake.paths == []


@pytest.mark.anyio
async def test_space_page_tree_allowed_space_served(
    connect: Any, fake: FakeConfluence
) -> None:
    client = await connect(V1)
    tree = json.loads(
        _text(await client.call_tool("get_space_page_tree", {"space_key": "ENG"}))
    )
    assert [page["id"] for page in tree["pages"]] == [ALLOWED_PAGE]


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    ("page_id", "message"),
    [
        (BLOCKED_PAGE, "'LEGAL' is blocked"),
        (UNRESOLVABLE_PAGE, "Could not determine the space"),
    ],
)
async def test_get_comments_denied(
    connect: Any, fake: FakeConfluence, mode: str, page_id: str, message: str
) -> None:
    client = await connect(mode)
    with pytest.raises(ToolError, match=message):
        await client.call_tool("get_comments", {"page_id": page_id})
    assert [p for p in fake.paths if "/comment" in p] == []


@pytest.mark.anyio
async def test_get_comments_allowed_space_served(
    connect: Any, fake: FakeConfluence
) -> None:
    client = await connect(V1)
    comments = json.loads(
        _text(await client.call_tool("get_comments", {"page_id": ALLOWED_PAGE}))
    )
    assert len(comments) == 1


# ---------------------------------------------------------------------------
# Other attachment read tools
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    ("page_id", "message"),
    [(BLOCKED_PAGE, "blocked"), (UNRESOLVABLE_PAGE, "Could not determine the space")],
)
async def test_download_attachment_denied(
    connect: Any, fake: FakeConfluence, mode: str, page_id: str, message: str
) -> None:
    client = await connect(mode)
    result = await client.call_tool(
        "download_attachment", {"attachment_id": f"att{page_id}"}
    )
    payload = json.loads(_text(result))
    assert payload["success"] is False
    assert message in payload["error"]
    assert fake.data_paths() == []


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("tool", ["download_content_attachments", "get_page_images"])
@pytest.mark.parametrize(
    ("page_id", "message"),
    [
        (BLOCKED_PAGE, "LEGAL.*blocked"),
        (UNRESOLVABLE_PAGE, "Could not determine the space"),
    ],
)
async def test_attachment_download_tools_denied(
    connect: Any,
    fake: FakeConfluence,
    mode: str,
    tool: str,
    page_id: str,
    message: str,
) -> None:
    client = await connect(mode)
    with pytest.raises(ToolError, match=message):
        await client.call_tool(tool, {"content_id": page_id})
    assert fake.data_paths() == []


# ---------------------------------------------------------------------------
# Positive controls and the no-block-list case
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
async def test_allowed_space_still_served(
    connect: Any, fake: FakeConfluence, mode: str
) -> None:
    client = await connect(mode)

    labels = json.loads(
        _text(await client.call_tool("get_labels", {"page_id": ALLOWED_PAGE}))
    )
    assert [label["name"] for label in labels] == [SECRET_LABEL]

    attachments = json.loads(
        _text(await client.call_tool("get_attachments", {"content_id": ALLOWED_PAGE}))
    )
    assert attachments["success"] is True
    assert attachments["attachments"][0]["title"] == "secret.png"

    page = json.loads(
        _text(await client.call_tool("get_page", {"page_id": ALLOWED_PAGE}))
    )
    assert "secret body" in page["metadata"]["content"]["value"]

    download = await client.call_tool(
        "download_attachment", {"attachment_id": f"att{ALLOWED_PAGE}"}
    )
    assert download.content[0].type == "resource"

    images = await client.call_tool("get_page_images", {"content_id": ALLOWED_PAGE})
    assert json.loads(images.content[0].text)["downloaded"] == 1


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
async def test_no_block_list_makes_no_space_lookups(
    connect: Any, fake: FakeConfluence, mode: str
) -> None:
    """Without CONFLUENCE_SPACES_BLOCKED, unresolvable spaces are still served."""
    client = await connect(mode, blocked=None)

    labels = json.loads(
        _text(await client.call_tool("get_labels", {"page_id": UNRESOLVABLE_PAGE}))
    )
    assert [label["name"] for label in labels] == [SECRET_LABEL]
    attachments = json.loads(
        _text(
            await client.call_tool("get_attachments", {"content_id": UNRESOLVABLE_PAGE})
        )
    )
    assert attachments["success"] is True
    assert fake.paths == [
        f"/rest/api/content/{UNRESOLVABLE_PAGE}/label"
        if mode == V1
        else f"/api/v2/pages/{UNRESOLVABLE_PAGE}/labels",
        f"/rest/api/content/{UNRESOLVABLE_PAGE}/child/attachment"
        if mode == V1
        else f"/api/v2/pages/{UNRESOLVABLE_PAGE}/attachments",
    ]


# ---------------------------------------------------------------------------
# Page children, page history, version diffs and view statistics
# ---------------------------------------------------------------------------

# Page view statistics are Cloud-only, so v1 runs against a Cloud site URL.
CLOUD_SITE_URL = "https://test.atlassian.net"

PAGE_READ_TOOLS: dict[str, Callable[[str], dict[str, Any]]] = {
    "get_page_children": lambda page_id: {"parent_id": page_id},
    "get_page_history": lambda page_id: {"page_id": page_id, "version": 1},
    "get_page_diff": lambda page_id: {
        "page_id": page_id,
        "from_version": 1,
        "to_version": 2,
    },
    "get_page_views": lambda page_id: {"page_id": page_id},
}


def _site_url(tool: str) -> str:
    return CLOUD_SITE_URL if tool == "get_page_views" else SERVER_URL


def _is_content_lookup(path: str, query: dict[str, list[str]]) -> bool:
    """Whether a request is the access check reading a content item's space."""
    if re.fullmatch(r"/api/v2/(pages|blogposts|embeds|folders)/\d+", path):
        return True
    return bool(re.fullmatch(r"/rest/api/content/\d+", path)) and query.get(
        "expand"
    ) == ["space"]


def _non_lookup_requests(fake: FakeConfluence) -> list[str]:
    """Requests other than the ones the access check makes to find a space."""
    return [
        path
        for path, query in fake.requests
        if not (path.startswith("/api/v2/spaces") or _is_content_lookup(path, query))
    ]


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("tool", list(PAGE_READ_TOOLS))
@pytest.mark.parametrize(
    ("page_id", "message"),
    [
        (BLOCKED_PAGE, "'LEGAL' is blocked"),
        (UNRESOLVABLE_PAGE, "Could not determine the space"),
    ],
    ids=["blocked", "unresolvable"],
)
async def test_page_read_tools_denied(
    connect: Any,
    fake: FakeConfluence,
    mode: str,
    tool: str,
    page_id: str,
    message: str,
) -> None:
    """Denied before any child, version, or view-statistics request is sent."""
    client = await connect(mode, blocked="LEGAL", url=_site_url(tool))
    text = _text(await client.call_tool(tool, PAGE_READ_TOOLS[tool](page_id)))
    assert message in json.loads(text)["error"]
    assert "secret" not in text.lower()
    assert _non_lookup_requests(fake) == []


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("tool", list(PAGE_READ_TOOLS))
async def test_page_read_tools_allowed_and_readonly_space_served(
    connect: Any, fake: FakeConfluence, mode: str, tool: str
) -> None:
    """ENG is read-only, which does not restrict reads."""
    client = await connect(mode, blocked="LEGAL", readonly="ENG", url=_site_url(tool))
    result = json.loads(
        _text(await client.call_tool(tool, PAGE_READ_TOOLS[tool](ALLOWED_PAGE)))
    )
    assert "error" not in result
    if tool == "get_page_children":
        assert any("child" in path for path in _non_lookup_requests(fake))
        if mode == V1:
            assert [c["title"] for c in result["results"]] == [SECRET_CHILD]
    elif tool == "get_page_history":
        assert "secret body" in result["content"]["value"]
    elif tool == "get_page_diff":
        assert result["page_id"] == ALLOWED_PAGE
        assert result["title"] == f"Page {ALLOWED_PAGE}"
    else:
        assert result["total_views"] == VIEW_COUNT


_HISTORY_V1 = ["/rest/api/content/100", "/rest/api/content/100/property"]


def _history_v2(version: int) -> list[str]:
    return [
        "/api/v2/pages/100/versions",
        f"/api/v2/versions/100v{version}",
        "/api/v2/spaces/9001",  # space key of the returned version
        "/api/v2/pages/100/properties",
    ]


# Requests each tool makes for page 100 when no block list is configured:
# the tool's own requests only, with no space lookup added by the check.
# The v2 child listing is not pinned; only the absence of lookups is checked.
PAGE_READ_REQUESTS: dict[str, dict[str, list[str]]] = {
    V1: {
        "get_page_children": [
            "/rest/api/content/100/child/page",
            "/rest/api/content/100/child/folder",
        ],
        "get_page_history": _HISTORY_V1,
        "get_page_diff": _HISTORY_V1 + _HISTORY_V1,
        "get_page_views": [
            "/rest/api/content/100",  # page title
            "/rest/api/analytics/content/100/views",
        ],
    },
    V2: {
        "get_page_history": _history_v2(1),
        "get_page_diff": _history_v2(1) + _history_v2(2),
        "get_page_views": [
            "/rest/api/content/100",  # page title (410 behind the gateway)
            "/rest/api/analytics/content/100/views",
        ],
    },
}


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("tool", list(PAGE_READ_TOOLS))
@pytest.mark.parametrize("readonly", [None, "LEGAL"], ids=["no-lists", "readonly"])
async def test_page_read_tools_without_block_list_make_no_space_lookups(
    connect: Any, fake: FakeConfluence, mode: str, tool: str, readonly: str | None
) -> None:
    """Without a block list, reads are served and the check sends nothing."""
    client = await connect(mode, blocked=None, readonly=readonly, url=_site_url(tool))
    text = _text(await client.call_tool(tool, PAGE_READ_TOOLS[tool](BLOCKED_PAGE)))
    assert "CONFLUENCE_SPACES" not in text
    assert fake.requests
    assert not any(_is_content_lookup(*request) for request in fake.requests)
    if tool in PAGE_READ_REQUESTS[mode]:
        assert fake.paths == PAGE_READ_REQUESTS[mode][tool]


@pytest.mark.usefixtures("http")
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    ("blocked", "message"),
    [("LEGAL", None), ("ENG", "'ENG' is blocked")],
    ids=["other-space-blocked", "folder-space-blocked"],
)
def test_folder_children_check_uses_folder_space(
    fake: FakeConfluence, mode: str, blocked: str, message: str | None
) -> None:
    """The check get_page_children runs resolves a folder ID to its space."""
    fetcher = _fetcher(mode, blocked=blocked)
    if message is None:
        fetcher.check_content_access(ALLOWED_FOLDER)
    else:
        with pytest.raises(ProjectAccessError, match=message):
            fetcher.check_content_access(ALLOWED_FOLDER)


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
async def test_get_page_children_of_folder_in_blocked_space_denied(
    connect: Any, fake: FakeConfluence, mode: str
) -> None:
    client = await connect(mode, blocked="ENG")
    text = _text(
        await client.call_tool("get_page_children", {"parent_id": ALLOWED_FOLDER})
    )
    assert "'ENG' is blocked" in json.loads(text)["error"]
    assert _non_lookup_requests(fake) == []


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

# (page id, blocked list, read-only list, expected error text)
DENIED_WRITE_CASES = [
    pytest.param(BLOCKED_PAGE, "LEGAL", None, "'LEGAL' is blocked", id="blocked"),
    pytest.param(
        UNRESOLVABLE_PAGE,
        "LEGAL",
        None,
        "CONFLUENCE_SPACES_BLOCKED is set",
        id="unresolvable-with-blocklist",
    ),
    pytest.param(ALLOWED_PAGE, None, "ENG", "'ENG' is read-only", id="readonly"),
    pytest.param(
        UNRESOLVABLE_PAGE,
        None,
        "ENG",
        "CONFLUENCE_SPACES_READONLY is set",
        id="unresolvable-with-readonly-only",
    ),
]

CONTENT_WRITES: dict[str, Callable[[ConfluenceFetcher, str, Path], Any]] = {
    "add_page_label": lambda f, page_id, _p: f.add_page_label(page_id, "x"),
    "upload_attachment": lambda f, page_id, p: f.upload_attachment(page_id, str(p)),
    "upload_attachments": lambda f, page_id, p: f.upload_attachments(page_id, [str(p)]),
    "delete_attachment": lambda f, page_id, _p: f.delete_attachment(f"att{page_id}"),
    "reply_to_comment": lambda f, page_id, _p: f.reply_to_comment(f"1{page_id}", "hi"),
}


@pytest.mark.usefixtures("http")
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("operation", list(CONTENT_WRITES))
@pytest.mark.parametrize(
    ("page_id", "blocked", "readonly", "message"), DENIED_WRITE_CASES
)
def test_content_level_writes_denied(
    fake: FakeConfluence,
    tmp_path: Path,
    mode: str,
    operation: str,
    page_id: str,
    blocked: str | None,
    readonly: str | None,
    message: str,
) -> None:
    """Fetcher-level writes are checked even when called without a tool."""
    upload = tmp_path / "file.txt"
    upload.write_text("x")
    fetcher = _fetcher(mode, blocked=blocked, readonly=readonly)

    with pytest.raises(ProjectAccessError, match=message):
        CONTENT_WRITES[operation](fetcher, page_id, upload)

    assert fake.writes == []
    assert fake.data_paths() == []


PAGE_WRITE_TOOLS: dict[str, Callable[[str], dict[str, Any]]] = {
    "update_page": lambda page_id: {"page_id": page_id, "title": "t", "content": "c"},
    "delete_page": lambda page_id: {"page_id": page_id},
    "move_page": lambda page_id: {"page_id": page_id, "target_parent_id": "200"},
    "add_comment": lambda page_id: {"page_id": page_id, "body": "hi"},
    "add_label": lambda page_id: {"page_id": page_id, "name": "x"},
}


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("tool", list(PAGE_WRITE_TOOLS))
@pytest.mark.parametrize(
    ("page_id", "blocked", "readonly", "message"), DENIED_WRITE_CASES
)
async def test_page_write_tools_denied(
    connect: Any,
    fake: FakeConfluence,
    mode: str,
    tool: str,
    page_id: str,
    blocked: str | None,
    readonly: str | None,
    message: str,
) -> None:
    """Write tools that take a page_id are checked by check_write_access."""
    client = await connect(mode, blocked=blocked, readonly=readonly)
    with pytest.raises(ToolError, match=message):
        await client.call_tool(tool, PAGE_WRITE_TOOLS[tool](page_id))
    assert fake.writes == []


# Page 400 lives in OPS, which is in neither list, so only the parent decides.
OPS_PAGE = "400"
PARENT_TOOLS: dict[str, Callable[[str], dict[str, Any]]] = {
    "create_page": lambda parent: {
        "space_key": "OPS",
        "title": "t",
        "content": "c",
        "parent_id": parent,
    },
    "update_page": lambda parent: {
        "page_id": OPS_PAGE,
        "title": "t",
        "content": "c",
        "parent_id": parent,
    },
    "move_page": lambda parent: {"page_id": OPS_PAGE, "target_parent_id": parent},
}


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("tool", list(PARENT_TOOLS))
@pytest.mark.parametrize(
    ("parent_id", "blocked", "readonly", "message"), DENIED_WRITE_CASES
)
async def test_parent_space_write_denied(
    connect: Any,
    fake: FakeConfluence,
    mode: str,
    tool: str,
    parent_id: str,
    blocked: str | None,
    readonly: str | None,
    message: str,
) -> None:
    """A new parent counts as a write into the parent's space."""
    client = await connect(mode, blocked=blocked, readonly=readonly)
    with pytest.raises(ToolError, match=message):
        await client.call_tool(tool, PARENT_TOOLS[tool](parent_id))
    assert fake.writes == []


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("tool", list(PARENT_TOOLS))
async def test_parent_in_allowed_space_proceeds(
    connect: Any, fake: FakeConfluence, mode: str, tool: str
) -> None:
    client = await connect(mode, blocked="LEGAL", readonly="LEGACY")
    try:
        await client.call_tool(tool, PARENT_TOOLS[tool](ALLOWED_PAGE))
    except ToolError as exc:
        # The fake's write response is minimal; only the access check matters.
        assert "CONFLUENCE_SPACES" not in str(exc)
    assert len(fake.writes) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("tool", ["delete_page", "add_comment"])
async def test_page_write_tools_allowed_space(
    connect: Any, fake: FakeConfluence, mode: str, tool: str
) -> None:
    client = await connect(mode, blocked="LEGAL", readonly="LEGACY")
    await client.call_tool(tool, PAGE_WRITE_TOOLS[tool](ALLOWED_PAGE))
    assert len(fake.writes) == 1


# ---------------------------------------------------------------------------
# Folders resolve to their own space.
# ---------------------------------------------------------------------------

V2_CONTENT_TYPES = ("pages", "blogposts", "embeds", "folders")


@pytest.mark.usefixtures("http")
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    ("content_id", "space_key"),
    [(ALLOWED_FOLDER, "ENG"), (BLOCKED_FOLDER, "LEGAL"), (UNKNOWN_CONTENT, None)],
    ids=["allowed-folder", "blocked-folder", "unknown-content"],
)
def test_folder_resolves_to_its_space(
    fake: FakeConfluence, mode: str, content_id: str, space_key: str | None
) -> None:
    fetcher = _fetcher(mode, blocked="LEGAL")
    assert fetcher.resolve_content_space_key(content_id) == space_key
    if mode == V2:
        assert fake.paths[: len(V2_CONTENT_TYPES)] == [
            f"/api/v2/{content_type}/{content_id}" for content_type in V2_CONTENT_TYPES
        ]


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("tool", list(PARENT_TOOLS))
@pytest.mark.parametrize(
    ("parent_id", "blocked", "readonly", "message"),
    [
        pytest.param(
            BLOCKED_FOLDER, "LEGAL", None, "'LEGAL' is blocked", id="blocked-folder"
        ),
        pytest.param(
            ALLOWED_FOLDER, None, "ENG", "'ENG' is read-only", id="readonly-folder"
        ),
        pytest.param(
            UNKNOWN_CONTENT,
            "LEGAL",
            None,
            "CONFLUENCE_SPACES_BLOCKED is set",
            id="unknown-content",
        ),
    ],
)
async def test_folder_parent_write_denied(
    connect: Any,
    fake: FakeConfluence,
    mode: str,
    tool: str,
    parent_id: str,
    blocked: str | None,
    readonly: str | None,
    message: str,
) -> None:
    """A folder as the new parent counts as a write into the folder's space."""
    client = await connect(mode, blocked=blocked, readonly=readonly)
    with pytest.raises(ToolError, match=message):
        await client.call_tool(tool, PARENT_TOOLS[tool](parent_id))
    assert fake.writes == []


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("tool", list(PARENT_TOOLS))
async def test_folder_parent_in_allowed_space_proceeds(
    connect: Any, fake: FakeConfluence, mode: str, tool: str
) -> None:
    client = await connect(mode, blocked="LEGAL", readonly="LEGACY")
    try:
        await client.call_tool(tool, PARENT_TOOLS[tool](ALLOWED_FOLDER))
    except ToolError as exc:
        # The fake's write response is minimal; only the access check matters.
        assert "CONFLUENCE_SPACES" not in str(exc)
    assert len(fake.writes) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("tool", list(PARENT_TOOLS))
@pytest.mark.parametrize("folder_id", list(FOLDER_ERRORS))
async def test_folder_lookup_error_denies_parent_write(
    connect: Any, fake: FakeConfluence, tool: str, folder_id: str
) -> None:
    """A failed v2 folder lookup leaves the space unknown, so the write is denied."""
    client = await connect(V2, blocked="LEGAL")
    with pytest.raises(ToolError, match="CONFLUENCE_SPACES_BLOCKED is set"):
        await client.call_tool(tool, PARENT_TOOLS[tool](folder_id))
    assert f"/api/v2/folders/{folder_id}" in fake.paths
    assert fake.writes == []


@pytest.mark.usefixtures("http")
@pytest.mark.parametrize("folder_id", list(FOLDER_ERRORS))
def test_folder_lookup_error_denies_read_check(
    fake: FakeConfluence, folder_id: str
) -> None:
    """The read check used before listing a folder's children fails closed."""
    fetcher = _fetcher(V2, blocked="LEGAL")
    with pytest.raises(ProjectAccessError, match="CONFLUENCE_SPACES_BLOCKED is set"):
        fetcher.check_content_access(folder_id)
    assert f"/api/v2/folders/{folder_id}" in fake.paths
    assert fake.writes == []


# ---------------------------------------------------------------------------
# reply_to_comment checks the space of the comment's page or blog post.
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    ("comment_id", "blocked", "readonly", "message"),
    [
        (f"1{BLOCKED_PAGE}", "LEGAL", None, "'LEGAL' is blocked"),
        (f"1{UNRESOLVABLE_PAGE}", "LEGAL", None, "Could not determine the space"),
        (f"1{ALLOWED_PAGE}", None, "ENG", "'ENG' is read-only"),
    ],
    ids=["blocked", "unresolvable", "readonly"],
)
async def test_reply_to_comment_denied(
    connect: Any,
    fake: FakeConfluence,
    mode: str,
    comment_id: str,
    blocked: str | None,
    readonly: str | None,
    message: str,
) -> None:
    client = await connect(mode, blocked=blocked, readonly=readonly)
    result = await client.call_tool(
        "reply_to_comment", {"comment_id": comment_id, "body": "hi"}
    )
    payload = json.loads(_text(result))
    assert payload["success"] is False
    assert message in payload["error"]
    assert fake.writes == []


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
async def test_reply_to_comment_allowed_space(
    connect: Any, fake: FakeConfluence, mode: str
) -> None:
    client = await connect(mode, blocked="LEGAL", readonly="LEGACY")
    result = await client.call_tool(
        "reply_to_comment", {"comment_id": f"1{ALLOWED_PAGE}", "body": "hi"}
    )
    assert json.loads(_text(result))["success"] is True
    assert len(fake.writes) == 1


# ---------------------------------------------------------------------------
# The write guard checks the server's lists as well as the fetcher's.
# ---------------------------------------------------------------------------

# (page id, blocked list, read-only list, expected error text)
LISTED_SPACE_CASES = [
    pytest.param(BLOCKED_PAGE, "LEGAL", None, "'LEGAL' is blocked", id="blocked"),
    pytest.param(ALLOWED_PAGE, None, "ENG", "'ENG' is read-only", id="readonly"),
]


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("tool", ["delete_page", "add_comment", "add_label"])
@pytest.mark.parametrize(
    ("page_id", "blocked", "readonly", "message"), LISTED_SPACE_CASES
)
async def test_write_guard_uses_server_lists_when_fetcher_has_none(
    connect: Any,
    fake: FakeConfluence,
    mode: str,
    tool: str,
    page_id: str,
    blocked: str | None,
    readonly: str | None,
    message: str,
) -> None:
    """A fetcher config without the lists does not weaken check_write_access."""
    client = await connect(
        mode,
        blocked=None,
        server_config=_config(mode, blocked=blocked, readonly=readonly),
    )
    with pytest.raises(ToolError, match=message):
        await client.call_tool(tool, PAGE_WRITE_TOOLS[tool](page_id))
    assert fake.writes == []


# ---------------------------------------------------------------------------
# Per-request credentials. get_confluence_fetcher is not patched: it builds a
# per-request fetcher from the request state set by UserTokenMiddleware.
# ---------------------------------------------------------------------------

HEADER_PAT = "header-pat"  # X-Atlassian-Confluence-Url + -Personal-Token
BASIC = "basic"  # Authorization: Basic
OAUTH = "oauth"  # Authorization: Bearer, server has Cloud OAuth configured
PAT_BEARER = "pat-bearer"  # Authorization: Bearer, server has no OAuth
PER_REQUEST_AUTHS = [HEADER_PAT, BASIC, OAUTH, PAT_BEARER]

REQUEST_HEADERS: dict[str, dict[str, str]] = {
    HEADER_PAT: {
        "X-Atlassian-Confluence-Url": SERVER_URL,
        "X-Atlassian-Confluence-Personal-Token": "test-pat-token",
    },
    BASIC: {
        "Authorization": "Basic "
        + base64.b64encode(b"user@example.com:test-api-token").decode()
    },
    OAUTH: {"Authorization": "Bearer test-oauth-token"},
    PAT_BEARER: {"Authorization": "Bearer test-pat-token"},
}


def _server_config(
    auth: str, *, blocked: str | None, readonly: str | None
) -> ConfluenceConfig:
    """The server's global config for the deployment each auth mode implies."""
    if auth == OAUTH:
        return ConfluenceConfig(
            url=SERVER_URL,
            auth_type="oauth",
            oauth_config=OAuthConfig(
                client_id="test-client-id",
                client_secret="test-client-secret",
                redirect_uri="https://localhost/callback",
                scope="read:confluence-content.all",
                cloud_id=CLOUD_ID,
            ),
            spaces_blocked=blocked,
            spaces_readonly=readonly,
        )
    return _config(V1, blocked=blocked, readonly=readonly)


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
) -> AsyncIterator[Callable[..., Awaitable[Client]]]:
    """Yield ``connect_as(auth, blocked=..., readonly=...)`` -> connected client.

    ``with_server_config=False`` models a deployment with no global
    Confluence config, where only header-based credentials work.
    """
    # Lets the middleware's URL check pass without a DNS lookup.
    monkeypatch.setenv("MCP_ALLOWED_URL_DOMAINS", urlsplit(SERVER_URL).hostname)
    monkeypatch.delenv("IGNORE_HEADER_AUTH", raising=False)
    async with AsyncExitStack() as stack:

        async def factory(
            auth: str,
            blocked: str | None = "LEGAL",
            readonly: str | None = None,
            *,
            with_server_config: bool = True,
        ) -> Client:
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
            return await stack.enter_async_context(client)

        yield factory


PER_REQUEST_WRITES: dict[str, tuple[str, Callable[[str], dict[str, Any]]]] = {
    "delete_page": ("delete_page", PAGE_WRITE_TOOLS["delete_page"]),
    "add_comment": ("add_comment", PAGE_WRITE_TOOLS["add_comment"]),
    "add_label": ("add_label", PAGE_WRITE_TOOLS["add_label"]),
    # The page itself lives in OPS; only the new parent is listed.
    "update_page-parent_id": ("update_page", PARENT_TOOLS["update_page"]),
    "move_page-target_parent_id": ("move_page", PARENT_TOOLS["move_page"]),
}


@pytest.mark.anyio
@pytest.mark.parametrize("auth", PER_REQUEST_AUTHS)
@pytest.mark.parametrize("case", list(PER_REQUEST_WRITES))
@pytest.mark.parametrize(
    ("page_id", "blocked", "readonly", "message"), LISTED_SPACE_CASES
)
async def test_per_request_auth_writes_denied(
    connect_as: Any,
    fake: FakeConfluence,
    auth: str,
    case: str,
    page_id: str,
    blocked: str | None,
    readonly: str | None,
    message: str,
) -> None:
    """Server space lists apply whatever credentials the request carries."""
    client = await connect_as(auth, blocked=blocked, readonly=readonly)
    tool, arguments = PER_REQUEST_WRITES[case]
    with pytest.raises(ToolError, match=message):
        await client.call_tool(tool, arguments(page_id))
    assert fake.writes == []


@pytest.mark.anyio
@pytest.mark.parametrize("auth", PER_REQUEST_AUTHS)
async def test_per_request_auth_get_labels_blocked_denied(
    connect_as: Any, fake: FakeConfluence, auth: str
) -> None:
    client = await connect_as(auth, blocked="LEGAL")
    with pytest.raises(ToolError, match="'LEGAL' is blocked"):
        await client.call_tool("get_labels", {"page_id": BLOCKED_PAGE})
    assert fake.data_paths() == []


@pytest.mark.anyio
@pytest.mark.parametrize("auth", PER_REQUEST_AUTHS)
async def test_per_request_auth_get_labels_readonly_allowed(
    connect_as: Any, fake: FakeConfluence, auth: str
) -> None:
    client = await connect_as(auth, blocked="LEGAL", readonly="ENG")
    labels = json.loads(
        _text(await client.call_tool("get_labels", {"page_id": ALLOWED_PAGE}))
    )
    assert [label["name"] for label in labels] == [SECRET_LABEL]


@pytest.mark.anyio
@pytest.mark.parametrize("auth", PER_REQUEST_AUTHS)
@pytest.mark.parametrize("tool", ["delete_page", "add_label"])
async def test_per_request_auth_unlisted_space_write_proceeds(
    connect_as: Any, fake: FakeConfluence, auth: str, tool: str
) -> None:
    client = await connect_as(auth, blocked="LEGAL", readonly="ENG")
    await client.call_tool(tool, PAGE_WRITE_TOOLS[tool](OPS_PAGE))
    assert len(fake.writes) == 1


@pytest.mark.anyio
async def test_header_pat_without_lists_makes_no_space_lookups(
    connect_as: Any, fake: FakeConfluence
) -> None:
    """With no lists configured the guard adds no requests."""
    client = await connect_as(HEADER_PAT, blocked=None)
    await client.call_tool("delete_page", {"page_id": UNRESOLVABLE_PAGE})
    assert fake.paths == [
        "/rest/api/user/current",
        f"/rest/api/content/{UNRESOLVABLE_PAGE}",
    ]
    assert fake.writes == [f"DELETE /rest/api/content/{UNRESOLVABLE_PAGE}"]


@pytest.mark.anyio
async def test_header_pat_without_server_config_has_no_lists(
    connect_as: Any, fake: FakeConfluence
) -> None:
    """With no global Confluence config there are no lists to enforce."""
    client = await connect_as(HEADER_PAT, with_server_config=False)
    await client.call_tool("delete_page", {"page_id": BLOCKED_PAGE})
    assert fake.writes == [f"DELETE /rest/api/content/{BLOCKED_PAGE}"]


# ---------------------------------------------------------------------------
# check_confluence_content_space_access
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("space_key", ["", " ", " \t "])
@pytest.mark.parametrize(
    ("blocked", "readonly", "operation", "message"),
    [
        ("LEGAL", None, "read", "CONFLUENCE_SPACES_BLOCKED is set"),
        (None, "ENG", "write", "CONFLUENCE_SPACES_READONLY is set"),
    ],
    ids=["blocked-read", "readonly-write"],
)
def test_blank_space_key_treated_as_unknown(
    space_key: str,
    blocked: str | None,
    readonly: str | None,
    operation: str,
    message: str,
) -> None:
    """A blank or whitespace-only key cannot be matched, so access fails closed."""
    config = _config(V1, blocked=blocked, readonly=readonly)
    with pytest.raises(ProjectAccessError, match=message):
        check_confluence_content_space_access(
            config, space_key, content_id="1", write=operation == "write"
        )
