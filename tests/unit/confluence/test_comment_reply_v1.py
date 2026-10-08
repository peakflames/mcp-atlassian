"""v1 comment replies (basic auth / PAT), with HTTP faked at requests.Session.request.

The fetcher is a real ConfluenceFetcher built from a real ConfluenceConfig.
On Cloud a v1 reply posted with the parent comment as its container is
created but is not attached to the page, so the reply must name the page as
its container and the parent comment as its ancestor. Server/Data Center
requests are unchanged.
"""

import json
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from mcp_atlassian.confluence import ConfluenceFetcher
from mcp_atlassian.confluence.config import ConfluenceConfig
from mcp_atlassian.models.confluence import ConfluenceComment

CLOUD_URL = "https://example.atlassian.net/wiki"
SERVER_URL = "https://confluence.example.com"
PAGE_ID = "123"
PARENT_ID = "700"
REPLY_ID = "701"


def _response(status: int, payload: Any) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response.reason = "OK" if status < 400 else "Error"
    response._content = json.dumps(payload).encode()
    response.headers["Content-Type"] = "application/json"
    response.encoding = "utf-8"
    return response


class FakeConfluence:
    def __init__(
        self,
        *,
        parent_container: dict[str, Any] | None,
        echo_ancestors: bool = True,
        empty_reply: bool = False,
    ) -> None:
        self.parent_container = parent_container
        self.echo_ancestors = echo_ancestors
        self.empty_reply = empty_reply
        self.calls: list[tuple[str, str, dict[str, list[str]], Any]] = []

    def __call__(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        split = urlsplit(url)
        path = split.path.removeprefix("/wiki").rstrip("/")
        query = parse_qs(split.query)
        for key, value in (kwargs.get("params") or {}).items():
            query[key] = [str(value)]
        body = kwargs.get("data") or kwargs.get("json")
        if isinstance(body, str | bytes):
            body = json.loads(body)
        self.calls.append((method, path, query, body))
        if method == "GET" and path == f"/rest/api/content/{PARENT_ID}":
            comment: dict[str, Any] = {"id": PARENT_ID, "type": "comment"}
            if self.parent_container is not None:
                comment["container"] = self.parent_container
            return _response(200, comment)
        if method == "POST" and path == "/rest/api/content":
            if self.empty_reply:
                return _response(200, {})
            reply: dict[str, Any] = {
                "id": REPLY_ID,
                "type": "comment",
                "status": "current",
                "title": "Re: Release notes",
                "container": body["container"],
                "body": {"storage": {"value": "<p>reply</p>"}},
                "version": {"number": 1},
            }
            if body.get("ancestors") and self.echo_ancestors:
                reply["ancestors"] = [
                    {"id": a["id"], "type": "comment"} for a in body["ancestors"]
                ]
            return _response(200, reply)
        return _response(404, {"message": f"no route for {method} {path}"})

    def posts(self) -> list[Any]:
        return [body for method, _, _, body in self.calls if method == "POST"]


def _fetcher(url: str) -> ConfluenceFetcher:
    config = ConfluenceConfig(
        url=url, auth_type="basic", username="test-user", api_token="test-token"
    )
    fetcher = ConfluenceFetcher(config=config)
    assert fetcher._v2_adapter is None
    return fetcher


@pytest.fixture
def http() -> Iterator[Any]:
    def install(fake: FakeConfluence) -> None:
        patcher = patch.object(
            requests.Session,
            "request",
            autospec=True,
            side_effect=lambda _self, method, url, **kw: fake(method, url, **kw),
        )
        patcher.start()
        patches.append(patcher)

    patches: list[Any] = []
    yield install
    for patcher in patches:
        patcher.stop()


@pytest.mark.parametrize("container_type", ["page", "blogpost"])
def test_cloud_reply_uses_page_container_and_parent_ancestor(
    http: Any, container_type: str
) -> None:
    fake = FakeConfluence(
        parent_container={"id": PAGE_ID, "type": container_type, "title": "Notes"}
    )
    http(fake)

    reply = _fetcher(CLOUD_URL).reply_to_comment(PARENT_ID, "<p>reply</p>")

    assert fake.posts() == [
        {
            "type": "comment",
            "container": {"id": PAGE_ID, "type": container_type},
            "ancestors": [{"id": PARENT_ID}],
            "body": {"storage": {"value": "<p>reply</p>", "representation": "storage"}},
        }
    ]
    lookup = [c for c in fake.calls if c[0] == "GET"]
    assert len(lookup) == 1
    assert lookup[0][2]["expand"] == ["container"]
    assert reply is not None
    assert reply.id == REPLY_ID
    assert reply.parent_comment_id == PARENT_ID


@pytest.mark.parametrize(
    "parent_container",
    [
        None,
        # The parent is itself a reply left without a page (by the old v1
        # request); posting under it would create another such reply.
        {"id": "699", "type": "comment"},
        {"id": PAGE_ID},
        {"type": "page"},
    ],
    ids=["no-container", "comment-container", "missing-type", "missing-id"],
)
def test_cloud_reply_without_page_container_posts_nothing(
    http: Any, parent_container: dict[str, Any] | None
) -> None:
    fake = FakeConfluence(parent_container=parent_container)
    http(fake)

    reply = _fetcher(CLOUD_URL).reply_to_comment(PARENT_ID, "<p>reply</p>")

    assert reply is None
    assert fake.posts() == []


def test_cloud_reply_response_without_ancestors_keeps_parent(http: Any) -> None:
    fake = FakeConfluence(
        parent_container={"id": PAGE_ID, "type": "page"}, echo_ancestors=False
    )
    http(fake)

    reply = _fetcher(CLOUD_URL).reply_to_comment(PARENT_ID, "<p>reply</p>")

    assert reply is not None
    assert reply.id == REPLY_ID
    assert reply.parent_comment_id == PARENT_ID


def test_cloud_reply_empty_response_returns_none(http: Any) -> None:
    fake = FakeConfluence(
        parent_container={"id": PAGE_ID, "type": "page"}, empty_reply=True
    )
    http(fake)

    assert _fetcher(CLOUD_URL).reply_to_comment(PARENT_ID, "<p>reply</p>") is None
    assert len(fake.posts()) == 1


def test_server_reply_request_is_unchanged(http: Any) -> None:
    fake = FakeConfluence(parent_container={"id": PAGE_ID, "type": "page"})
    http(fake)

    reply = _fetcher(SERVER_URL).reply_to_comment(PARENT_ID, "<p>reply</p>")

    assert [c[0] for c in fake.calls] == ["POST"]
    assert fake.posts() == [
        {
            "type": "comment",
            "container": {"id": PARENT_ID, "type": "comment"},
            "body": {"storage": {"value": "<p>reply</p>", "representation": "storage"}},
        }
    ]
    assert reply is not None
    assert reply.parent_comment_id == PARENT_ID


def test_model_reads_parent_from_last_comment_ancestor() -> None:
    comment = ConfluenceComment.from_api_response(
        {
            "id": REPLY_ID,
            "type": "comment",
            "container": {"id": PAGE_ID, "type": "page"},
            "ancestors": [
                {"id": "699", "type": "comment"},
                {"id": PARENT_ID, "type": "comment"},
            ],
        }
    )
    assert comment.parent_comment_id == PARENT_ID


def test_model_ignores_non_comment_ancestor() -> None:
    comment = ConfluenceComment.from_api_response(
        {
            "id": REPLY_ID,
            "type": "comment",
            "container": {"id": PAGE_ID, "type": "page"},
            "ancestors": [{"id": PAGE_ID, "type": "page"}],
        }
    )
    assert comment.parent_comment_id is None
