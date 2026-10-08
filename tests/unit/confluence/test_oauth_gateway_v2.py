"""Tests for Cloud OAuth gateway handling: attachment URLs, v2 labels, embeds."""

import json
import logging
from pathlib import Path
from typing import Any, TypeVar
from unittest.mock import MagicMock, Mock, patch

import pytest
import requests
from atlassian import Confluence
from requests.exceptions import HTTPError

from mcp_atlassian.confluence.attachments import AttachmentsMixin
from mcp_atlassian.confluence.client import ConfluenceClient
from mcp_atlassian.confluence.labels import LabelsMixin
from mcp_atlassian.confluence.pages import PagesMixin
from mcp_atlassian.confluence.v2_adapter import (
    ChildListTruncatedError,
    ConfluenceV2Adapter,
)
from mcp_atlassian.exceptions import MCPAtlassianAuthenticationError
from mcp_atlassian.utils.urls import resolve_relative_url

GATEWAY_URL = "https://api.atlassian.com/ex/confluence/test-cloud-id"
SITE_URL = "https://example.atlassian.net/wiki"
EMBED = {
    "id": "100",
    "title": "Doc",
    "embedUrl": "https://example.com/d",
    "spaceId": "555",
    "parentId": "9",
    "parentType": "folder",
}

MixinT = TypeVar("MixinT", bound=ConfluenceClient)


def _response(status_code: int, payload: dict[str, Any] | None = None) -> Mock:
    response = Mock()
    response.status_code = status_code
    response.json.return_value = payload or {}
    if status_code >= 400:
        error = HTTPError(f"{status_code} Client Error")
        error.response = response
        response.raise_for_status.side_effect = error
    else:
        response.raise_for_status.return_value = None
    return response


def _http_error(status_code: int) -> ValueError:
    """Build the ValueError the v2 adapter raises, wrapping an HTTPError."""
    cause = HTTPError(f"{status_code} Client Error")
    cause.response = _response(status_code)
    error = ValueError(f"Failed: {cause}")
    error.__cause__ = cause
    return error


def _mixin(
    cls: type[MixinT],
    *,
    auth_type: str = "oauth",
    is_cloud: bool = True,
    blocked: frozenset[str] = frozenset(),
) -> MixinT:
    with patch("mcp_atlassian.confluence.client.ConfluenceClient.__init__") as init:
        init.return_value = None
        mixin = cls()
    mixin.config = MagicMock(
        auth_type=auth_type,
        is_cloud=is_cloud,
        url=SITE_URL,
        spaces_blocked_set=blocked,
        spaces_readonly_set=frozenset(),
    )
    mixin.confluence = MagicMock()
    mixin.confluence.url = GATEWAY_URL if auth_type == "oauth" else SITE_URL
    mixin.confluence._session = MagicMock(spec=requests.Session)
    return mixin


class TestAttachmentBaseUrl:
    def test_cloud_oauth_uses_gateway_with_wiki_prefix(self) -> None:
        mixin = _mixin(AttachmentsMixin)
        assert mixin._attachment_base_url() == f"{GATEWAY_URL}/wiki"

    def test_gateway_url_already_has_wiki_prefix(self) -> None:
        mixin = _mixin(AttachmentsMixin)
        mixin.confluence.url = f"{GATEWAY_URL}/wiki/"
        assert mixin._attachment_base_url() == f"{GATEWAY_URL}/wiki"

    @pytest.mark.parametrize(
        ("auth_type", "is_cloud"),
        [("basic", True), ("pat", False), ("oauth", False)],
    )
    def test_non_gateway_uses_site_url(
        self,
        auth_type: str,
        is_cloud: bool,  # noqa: FBT001
    ) -> None:
        mixin = _mixin(AttachmentsMixin, auth_type=auth_type, is_cloud=is_cloud)
        assert mixin._attachment_base_url() == SITE_URL

    def test_full_download_url_under_gateway(self) -> None:
        mixin = _mixin(AttachmentsMixin)
        url = resolve_relative_url(
            "/download/attachments/123/report.pdf?version=2&api=v2",
            mixin._attachment_base_url(),
        )
        assert url == (
            f"{GATEWAY_URL}/wiki/download/attachments/123/report.pdf?version=2&api=v2"
        )

    def test_download_content_attachments_uses_gateway_url(
        self, tmp_path: Path
    ) -> None:
        mixin = _mixin(AttachmentsMixin)
        attachment = {
            "id": "att1",
            "type": "attachment",
            "title": "report.pdf",
            "_links": {"download": "/download/attachments/123/report.pdf?version=2"},
        }
        with (
            patch("mcp_atlassian.confluence.attachments.validate_safe_path"),
            patch.object(
                AttachmentsMixin,
                "get_content_attachments",
                return_value={"success": True, "attachments": [attachment]},
            ),
            patch.object(
                AttachmentsMixin, "download_attachment", return_value=True
            ) as download,
        ):
            mixin.download_content_attachments("123", str(tmp_path))

        assert download.call_args.args[0] == (
            f"{GATEWAY_URL}/wiki/download/attachments/123/report.pdf?version=2"
        )


class TestV2Embeds:
    @pytest.fixture
    def adapter(self) -> ConfluenceV2Adapter:
        return ConfluenceV2Adapter(
            session=MagicMock(spec=requests.Session), base_url=GATEWAY_URL
        )

    def test_get_embed_success(self, adapter: ConfluenceV2Adapter) -> None:
        adapter.session.get.return_value = _response(200, EMBED)

        assert adapter.get_embed("100") == EMBED
        adapter.session.get.assert_called_once_with(f"{GATEWAY_URL}/api/v2/embeds/100")

    @pytest.mark.parametrize("status", [401, 403, 404])
    def test_get_embed_returns_none_on_error(
        self, adapter: ConfluenceV2Adapter, status: int
    ) -> None:
        adapter.session.get.return_value = _response(status)
        assert adapter.get_embed("100") is None

    def test_get_embed_returns_none_on_request_exception(
        self, adapter: ConfluenceV2Adapter
    ) -> None:
        adapter.session.get.side_effect = requests.ConnectionError("boom")
        assert adapter.get_embed("100") is None

    def test_get_embed_returns_none_on_non_json_200(
        self, adapter: ConfluenceV2Adapter
    ) -> None:
        response = _response(200)
        response.json.side_effect = json.JSONDecodeError("Expecting value", "<html>", 0)
        adapter.session.get.return_value = response

        assert adapter.get_embed("100") is None

    def test_get_space_key(self, adapter: ConfluenceV2Adapter) -> None:
        adapter.session.get.return_value = _response(200, {"id": "555", "key": "ENG"})

        assert adapter.get_space_key("555") == "ENG"
        assert adapter.session.get.call_args.args[0] == (
            f"{GATEWAY_URL}/api/v2/spaces/555"
        )

    @pytest.mark.parametrize("status", [401, 403, 404])
    def test_get_space_key_returns_none_on_error(
        self, adapter: ConfluenceV2Adapter, status: int
    ) -> None:
        adapter.session.get.return_value = _response(status)
        assert adapter.get_space_key("555") is None


class TestV2Labels:
    @pytest.fixture
    def adapter(self) -> ConfluenceV2Adapter:
        return ConfluenceV2Adapter(
            session=MagicMock(spec=requests.Session), base_url=GATEWAY_URL
        )

    def test_page_labels(self, adapter: ConfluenceV2Adapter) -> None:
        adapter.session.get.return_value = _response(
            200, {"results": [{"id": "1", "name": "alpha", "prefix": "global"}]}
        )

        result = adapter.get_content_labels("123")

        assert result == {
            "results": [
                {"id": "1", "name": "alpha", "prefix": "global", "label": "alpha"}
            ]
        }
        assert adapter.session.get.call_args.args[0] == (
            f"{GATEWAY_URL}/api/v2/pages/123/labels"
        )

    def test_falls_back_to_blogposts_on_404(self, adapter: ConfluenceV2Adapter) -> None:
        adapter.session.get.side_effect = [
            _response(404),
            _response(200, {"results": [{"id": "2", "name": "beta"}]}),
        ]

        result = adapter.get_content_labels("456")

        assert result["results"][0]["name"] == "beta"
        assert result["results"][0]["prefix"] == "global"
        urls = [call.args[0] for call in adapter.session.get.call_args_list]
        assert urls == [
            f"{GATEWAY_URL}/api/v2/pages/456/labels",
            f"{GATEWAY_URL}/api/v2/blogposts/456/labels",
        ]

    def test_attachment_labels(self, adapter: ConfluenceV2Adapter) -> None:
        adapter.session.get.return_value = _response(200, {"results": []})

        assert adapter.get_content_labels("att789") == {"results": []}
        assert adapter.session.get.call_args.args[0] == (
            f"{GATEWAY_URL}/api/v2/attachments/att789/labels"
        )

    def test_follows_cursor_pagination(self, adapter: ConfluenceV2Adapter) -> None:
        adapter.session.get.side_effect = [
            _response(
                200,
                {
                    "results": [{"id": "1", "name": "a"}],
                    "_links": {
                        "next": "/wiki/api/v2/pages/123/labels?limit=250&cursor=abc"
                    },
                },
            ),
            _response(200, {"results": [{"id": "2", "name": "b"}]}),
        ]

        result = adapter.get_content_labels("123")

        assert [label["name"] for label in result["results"]] == ["a", "b"]
        second_params = adapter.session.get.call_args_list[1].kwargs["params"]
        assert second_params == {"limit": 250, "cursor": "abc"}

    def test_warns_when_page_cap_reached(
        self, adapter: ConfluenceV2Adapter, caplog: pytest.LogCaptureFixture
    ) -> None:
        adapter.session.get.return_value = _response(
            200,
            {
                "results": [{"id": "1", "name": "a"}],
                "_links": {"next": "/wiki/api/v2/pages/123/labels?cursor=more"},
            },
        )

        with caplog.at_level(logging.WARNING, logger="mcp-atlassian"):
            results = adapter._get_all_results(
                f"{GATEWAY_URL}/api/v2/pages/123/labels", max_pages=2
            )

        assert len(results) == 2
        assert "remaining results were not fetched" in caplog.text

    def test_auth_error_propagates(self, adapter: ConfluenceV2Adapter) -> None:
        adapter.session.get.return_value = _response(401)
        with pytest.raises(HTTPError):
            adapter.get_content_labels("123")

    def test_not_found_raises_value_error(self, adapter: ConfluenceV2Adapter) -> None:
        adapter.session.get.return_value = _response(404)
        with pytest.raises(ValueError, match="Failed to get labels"):
            adapter.get_content_labels("123")


class TestLabelsMixinOAuth:
    def test_oauth_reads_labels_via_v2(self) -> None:
        mixin = _mixin(LabelsMixin)
        with patch.object(
            ConfluenceV2Adapter,
            "get_content_labels",
            return_value={"results": [{"id": "1", "name": "alpha", "label": "alpha"}]},
        ) as get_labels:
            labels = mixin.get_page_labels("123")

        get_labels.assert_called_once_with("123")
        mixin.confluence.get_page_labels.assert_not_called()
        assert [label.name for label in labels] == ["alpha"]

    def test_basic_auth_still_uses_v1(self) -> None:
        mixin = _mixin(LabelsMixin, auth_type="basic")
        mixin.confluence.get_page_labels.return_value = {"results": []}

        assert mixin.get_page_labels("123") == []
        mixin.confluence.get_page_labels.assert_called_once_with(page_id="123")

    def test_embed_id_reports_embed_url(self) -> None:
        mixin = _mixin(LabelsMixin)
        with (
            patch.object(
                ConfluenceV2Adapter,
                "get_content_labels",
                side_effect=_http_error(404),
            ),
            patch.object(ConfluenceV2Adapter, "get_embed", return_value=EMBED),
        ):
            with pytest.raises(Exception, match="https://example.com/d"):
                mixin.get_page_labels("100")

    def test_auth_error_skips_embed_lookup(self) -> None:
        mixin = _mixin(LabelsMixin)
        auth_error = HTTPError("401 Client Error")
        auth_error.response = _response(401)
        with (
            patch.object(
                ConfluenceV2Adapter, "get_content_labels", side_effect=auth_error
            ),
            patch.object(ConfluenceV2Adapter, "get_embed") as get_embed,
        ):
            with pytest.raises(Exception, match="Failed fetching labels"):
                mixin.get_page_labels("100")

        get_embed.assert_not_called()


class TestEmbedInfo:
    def test_returns_payload_for_embed(self) -> None:
        mixin = _mixin(AttachmentsMixin)
        with patch.object(ConfluenceV2Adapter, "get_embed", return_value=EMBED):
            info = mixin.get_embed_info("100", _http_error(404))

        assert info is not None
        assert info["success"] is False
        assert info["content_type"] == "embed"
        assert info["embed_url"] == "https://example.com/d"
        assert info["parent_type"] == "folder"

    def test_skips_attachment_ids_and_non_oauth(self) -> None:
        with patch.object(ConfluenceV2Adapter, "get_embed") as get_embed:
            assert (
                _mixin(AttachmentsMixin).get_embed_info("att1", _http_error(404))
                is None
            )
            assert (
                _mixin(AttachmentsMixin, auth_type="basic").get_embed_info(
                    "100", _http_error(404)
                )
                is None
            )
        get_embed.assert_not_called()

    @pytest.mark.parametrize(
        "error",
        [
            _http_error(401),
            _http_error(500),
            ValueError("Access to space 'SECRET' is blocked by configuration"),
            requests.Timeout("timed out"),
        ],
    )
    def test_non_404_errors_skip_lookup(self, error: Exception) -> None:
        mixin = _mixin(AttachmentsMixin)
        with patch.object(ConfluenceV2Adapter, "get_embed") as get_embed:
            assert mixin.get_embed_info("100", error) is None
        get_embed.assert_not_called()

    def test_blocked_space_is_withheld(self) -> None:
        mixin = _mixin(AttachmentsMixin, blocked=frozenset({"SECRET"}))
        with (
            patch.object(ConfluenceV2Adapter, "get_embed", return_value=EMBED),
            patch.object(
                ConfluenceV2Adapter, "get_space_key", return_value="secret"
            ) as get_space_key,
        ):
            assert mixin.get_embed_info("100", _http_error(404)) is None

        get_space_key.assert_called_once_with("555")

    def test_unresolvable_space_fails_closed_when_blocklist_set(self) -> None:
        mixin = _mixin(AttachmentsMixin, blocked=frozenset({"SECRET"}))
        with (
            patch.object(ConfluenceV2Adapter, "get_embed", return_value=EMBED),
            patch.object(ConfluenceV2Adapter, "get_space_key", return_value=None),
        ):
            assert mixin.get_embed_info("100", _http_error(404)) is None

    def test_allowed_space_returns_payload(self) -> None:
        mixin = _mixin(AttachmentsMixin, blocked=frozenset({"SECRET"}))
        with (
            patch.object(ConfluenceV2Adapter, "get_embed", return_value=EMBED),
            patch.object(ConfluenceV2Adapter, "get_space_key", return_value="ENG"),
        ):
            info = mixin.get_embed_info("100", _http_error(404))

        assert info is not None
        assert info["embed_url"] == "https://example.com/d"

    def test_no_blocklist_skips_space_lookup(self) -> None:
        mixin = _mixin(AttachmentsMixin)
        with (
            patch.object(ConfluenceV2Adapter, "get_embed", return_value=EMBED),
            patch.object(ConfluenceV2Adapter, "get_space_key") as get_space_key,
        ):
            assert mixin.get_embed_info("100", _http_error(404)) is not None

        get_space_key.assert_not_called()

    def test_get_content_attachments_reports_embed(self) -> None:
        mixin = _mixin(AttachmentsMixin)
        with (
            patch.object(
                ConfluenceV2Adapter,
                "get_page_attachments",
                side_effect=_http_error(404),
            ),
            patch.object(ConfluenceV2Adapter, "get_embed", return_value=EMBED),
        ):
            result = mixin.get_content_attachments("100")

        assert result["success"] is False
        assert result["content_type"] == "embed"
        assert result["embed_url"] == "https://example.com/d"


CHILDREN_URL = f"{GATEWAY_URL}/api/v2/pages/123/direct-children"
FOLDER_CHILDREN_URL = f"{GATEWAY_URL}/api/v2/folders/123/direct-children"
CHILDREN_PAGE_1 = {
    "results": [
        {
            "id": "201",
            "status": "current",
            "title": "Child A",
            "type": "page",
            "spaceId": "555",
            "childPosition": 0,
        },
        {
            "id": "202",
            "status": "current",
            "title": "Board",
            "type": "whiteboard",
            "spaceId": "555",
            "childPosition": 1,
        },
        {
            "id": "203",
            "status": "current",
            "title": "Folder B",
            "type": "folder",
            "spaceId": "555",
            "childPosition": 2,
        },
    ],
    "_links": {
        "next": "/wiki/api/v2/pages/123/direct-children?limit=25&cursor=abc",
        "base": "https://example.atlassian.net/wiki",
    },
}
CHILDREN_PAGE_2 = {
    "results": [
        {
            "id": "204",
            "status": "current",
            "title": "Child C",
            "type": "page",
            "spaceId": "555",
            "childPosition": 3,
        },
    ],
    "_links": {},
}
PAGE_DETAILS = {
    "results": [
        {
            "id": "201",
            "title": "Child A",
            "spaceId": "555",
            "version": {
                "number": 3,
                "createdAt": "2024-01-02T03:04:05.000Z",
                "message": "edit",
            },
            "body": {
                "storage": {"value": "<p>A body</p>", "representation": "storage"}
            },
            "_links": {"webui": "/spaces/ENG/pages/201/Child+A"},
        },
        {
            "id": "204",
            "title": "Child C",
            "spaceId": "555",
            "version": {"number": 1, "createdAt": "2024-02-03T04:05:06.000Z"},
            "body": {
                "storage": {"value": "<p>C body</p>", "representation": "storage"}
            },
        },
    ]
}
SPACE = {"id": "555", "key": "ENG", "name": "Engineering"}


def _route(routes: dict[str, Any]) -> Any:
    """Build a session.get side effect that answers by URL (and cursor)."""

    def get(url: str, params: dict[str, Any] | None = None, **_: Any) -> Any:
        key = url
        if params and params.get("cursor"):
            key = f"{url}#cursor={params['cursor']}"
        value = routes[key]
        if isinstance(value, Mock | requests.Response):
            return value
        return _response(200, value)

    return get


def _v2_routes() -> dict[str, Any]:
    return {
        CHILDREN_URL: CHILDREN_PAGE_1,
        f"{CHILDREN_URL}#cursor=abc": CHILDREN_PAGE_2,
        f"{GATEWAY_URL}/api/v2/pages": PAGE_DETAILS,
        f"{GATEWAY_URL}/api/v2/spaces/555": SPACE,
    }


def _child(child_id: str, child_type: str) -> dict[str, Any]:
    return {
        "id": child_id,
        "status": "current",
        "title": f"Item {child_id}",
        "type": child_type,
        "spaceId": "555",
    }


def _paged_children(
    items: list[dict[str, Any]], children_url: str = CHILDREN_URL
) -> Any:
    """Fake v2 session.get that pages ``items`` by the requested limit.

    ``children_url`` serves the list; with a folder URL, the pages
    endpoint answers 404 as it does for a folder ID.
    """

    def get(url: str, params: dict[str, Any] | None = None, **_: Any) -> Any:
        params = params or {}
        if url == children_url:
            offset = int(params.get("cursor") or 0)
            size = int(params["limit"])
            payload: dict[str, Any] = {
                "results": items[offset : offset + size],
                "_links": {},
            }
            if offset + size < len(items):
                payload["_links"]["next"] = (
                    f"{children_url}?limit={size}&cursor={offset + size}"
                )
            return _response(200, payload)
        if url == CHILDREN_URL:
            return _url_error_response(404, url)
        if url == f"{GATEWAY_URL}/api/v2/pages":
            return _response(
                200,
                {
                    "results": [
                        {"id": i, "version": {"number": 1}} for i in params["id"]
                    ]
                },
            )
        if url == f"{GATEWAY_URL}/api/v2/spaces/555":
            return _response(200, SPACE)
        msg = f"unexpected request: {url}"
        raise AssertionError(msg)

    return get


def _url_error_response(status_code: int, url: str) -> requests.Response:
    """A real response whose raise_for_status message includes the URL."""
    response = requests.Response()
    response.status_code = status_code
    response.url = url
    response.reason = "Error"
    response._content = b"{}"
    return response


class TestPageChildrenV2:
    """Cloud OAuth: get_page_children goes through the v2 adapter."""

    def test_lists_pages_and_folders_across_cursor_pages(self) -> None:
        mixin = _mixin(PagesMixin)
        session = mixin.confluence._session
        session.get.side_effect = _route(_v2_routes())

        children = mixin.get_page_children("123", limit=10, expand="version")

        mixin.confluence.get_page_child_by_type.assert_not_called()
        assert [c.id for c in children] == ["201", "203", "204"]
        assert [c.type for c in children] == ["page", "folder", "page"]
        assert children[0].title == "Child A"
        assert children[0].version is not None
        assert children[0].version.number == 3
        assert children[0].version.when == "2024-01-02T03:04:05.000Z"
        assert children[0].version.message == "edit"
        assert children[1].version is None  # folders carry no version on v2
        assert children[2].version is not None
        assert children[2].version.number == 1
        assert children[0].space is not None
        assert children[0].space.key == "ENG"
        assert children[0].space.name == "Engineering"
        assert children[0].content == ""

        calls = session.get.call_args_list
        assert calls[0].args[0] == CHILDREN_URL
        assert calls[0].kwargs["params"] == {"limit": 250}
        assert calls[1].args[0] == CHILDREN_URL
        assert calls[1].kwargs["params"] == {"limit": 250, "cursor": "abc"}
        assert calls[2].args[0] == f"{GATEWAY_URL}/api/v2/pages"
        assert calls[2].kwargs["params"] == {"id": ["201", "204"], "limit": 2}
        assert calls[3].args[0] == f"{GATEWAY_URL}/api/v2/spaces/555"
        assert len(calls) == 4

    def test_simplified_dict_matches_v1_shape(self) -> None:
        mixin = _mixin(PagesMixin)
        mixin.confluence._session.get.side_effect = _route(_v2_routes())

        children = mixin.get_page_children("123", limit=1)

        assert len(children) == 1
        child = children[0].to_simplified_dict()
        assert child == {
            "id": "201",
            "title": "Child A",
            "type": "page",
            "created": "",
            "updated": "",
            "url": f"{SITE_URL}/pages/viewpage.action?pageId=201",
            "space": {"key": "ENG", "name": "Engineering"},
            "version": 3,
            "attachments": [],
        }

    def test_start_and_limit_skip_matching_items(self) -> None:
        mixin = _mixin(PagesMixin)
        session = mixin.confluence._session
        session.get.side_effect = _route(_v2_routes())

        children = mixin.get_page_children("123", start=1, limit=1)

        assert [c.id for c in children] == ["203"]
        # The limit was reached on the first list page: no cursor request.
        urls = [call.args[0] for call in session.get.call_args_list]
        assert urls.count(CHILDREN_URL) == 1
        # The list page size does not depend on start/limit.
        assert session.get.call_args_list[0].kwargs["params"] == {"limit": 250}

    def test_include_folders_false_returns_pages_only(self) -> None:
        mixin = _mixin(PagesMixin)
        mixin.confluence._session.get.side_effect = _route(_v2_routes())

        children = mixin.get_page_children("123", include_folders=False)

        assert [c.id for c in children] == ["201", "204"]

    def test_include_content_reads_storage_body(self) -> None:
        mixin = _mixin(PagesMixin)
        session = mixin.confluence._session
        session.get.side_effect = _route(_v2_routes())

        children = mixin.get_page_children(
            "123",
            expand="version,body.storage",
            convert_to_markdown=False,
            include_folders=False,
        )

        assert [c.content for c in children] == ["<p>A body</p>", "<p>C body</p>"]
        bulk_params = session.get.call_args_list[2].kwargs["params"]
        assert bulk_params["body-format"] == "storage"

    def test_no_version_or_body_skips_page_lookup(self) -> None:
        mixin = _mixin(PagesMixin)
        session = mixin.confluence._session
        session.get.side_effect = _route(_v2_routes())

        children = mixin.get_page_children("123", expand="ancestors")

        assert [c.id for c in children] == ["201", "203", "204"]
        urls = [call.args[0] for call in session.get.call_args_list]
        assert CHILDREN_URL in urls
        assert f"{GATEWAY_URL}/api/v2/pages" not in urls
        assert all(c.version is None for c in children)

    def test_folder_parent_lists_children_across_cursor_pages(self) -> None:
        mixin = _mixin(PagesMixin)
        session = mixin.confluence._session
        routes = _v2_routes()
        del routes[f"{CHILDREN_URL}#cursor=abc"]
        routes[CHILDREN_URL] = _url_error_response(404, CHILDREN_URL)
        routes[FOLDER_CHILDREN_URL] = {
            **CHILDREN_PAGE_1,
            "_links": {
                "next": "/wiki/api/v2/folders/123/direct-children?limit=250&cursor=abc"
            },
        }
        routes[f"{FOLDER_CHILDREN_URL}#cursor=abc"] = CHILDREN_PAGE_2
        session.get.side_effect = _route(routes)

        children = mixin.get_page_children("123", limit=10, expand="version")

        assert [c.id for c in children] == ["201", "203", "204"]
        assert [c.type for c in children] == ["page", "folder", "page"]
        assert children[0].version is not None
        assert children[0].version.number == 3
        assert children[0].space is not None
        assert children[0].space.key == "ENG"
        calls = session.get.call_args_list
        assert [call.args[0] for call in calls[:3]] == [
            CHILDREN_URL,
            FOLDER_CHILDREN_URL,
            FOLDER_CHILDREN_URL,
        ]
        assert calls[2].kwargs["params"] == {"limit": 250, "cursor": "abc"}

    def test_folder_parent_applies_type_filter_start_and_limit(self) -> None:
        items = [
            _child("601", "page"),
            _child("602", "folder"),
            _child("603", "page"),
            _child("604", "folder"),
            _child("605", "page"),
            _child("606", "page"),
        ]
        mixin = _mixin(PagesMixin)
        mixin.confluence._session.get.side_effect = _paged_children(
            items, children_url=FOLDER_CHILDREN_URL
        )

        children = mixin.get_page_children(
            "123", start=1, limit=2, include_folders=False
        )

        assert [c.id for c in children] == ["603", "605"]

    def test_page_parent_makes_no_folder_request(self) -> None:
        mixin = _mixin(PagesMixin)
        session = mixin.confluence._session
        session.get.side_effect = _route(_v2_routes())

        mixin.get_page_children("123")

        urls = [call.args[0] for call in session.get.call_args_list]
        assert FOLDER_CHILDREN_URL not in urls

    def test_non_404_error_is_not_retried_as_folder(self) -> None:
        mixin = _mixin(PagesMixin)
        session = mixin.confluence._session
        session.get.side_effect = _route(
            {CHILDREN_URL: _url_error_response(500, CHILDREN_URL)}
        )

        with pytest.raises(ValueError, match="HTTP 500"):
            mixin.get_page_children("123")

        assert session.get.call_count == 1

    def test_not_found_raises_clean_message(self) -> None:
        mixin = _mixin(PagesMixin)
        session = mixin.confluence._session
        session.get.side_effect = _route(
            {
                CHILDREN_URL: _url_error_response(404, CHILDREN_URL),
                FOLDER_CHILDREN_URL: _url_error_response(404, FOLDER_CHILDREN_URL),
            }
        )

        with pytest.raises(ValueError) as excinfo:
            mixin.get_page_children("123")

        assert str(excinfo.value) == "Page not found or not accessible: 123"
        urls = [call.args[0] for call in session.get.call_args_list]
        assert urls == [CHILDREN_URL, FOLDER_CHILDREN_URL]

    def test_http_error_message_omits_gateway_url(self) -> None:
        mixin = _mixin(PagesMixin)
        mixin.confluence._session.get.side_effect = _route(
            {CHILDREN_URL: _url_error_response(500, CHILDREN_URL)}
        )

        with pytest.raises(ValueError) as excinfo:
            mixin.get_page_children("123")

        message = str(excinfo.value)
        assert message == "Failed to get children of page '123': HTTP 500"
        assert "api.atlassian.com" not in message
        assert "test-cloud-id" not in message

    def test_limit_caps_items_within_one_list_page(self) -> None:
        items = [_child(str(300 + i), "page") for i in range(5)]
        mixin = _mixin(PagesMixin)
        mixin.confluence._session.get.side_effect = _paged_children(items)

        children = mixin.get_page_children("123", limit=3)

        assert [c.id for c in children] == ["300", "301", "302"]

    def test_finds_page_behind_many_skipped_folders(self) -> None:
        items = [_child(str(400 + i), "folder") for i in range(25)]
        items.append(_child("500", "page"))
        mixin = _mixin(PagesMixin)
        session = mixin.confluence._session
        session.get.side_effect = _paged_children(items)

        children = mixin.get_page_children("123", limit=1, include_folders=False)

        assert [c.id for c in children] == ["500"]
        urls = [call.args[0] for call in session.get.call_args_list]
        assert urls.count(CHILDREN_URL) == 1

    def test_request_cap_raises_instead_of_partial_result(self) -> None:
        items = [_child(str(1000 + i), "folder") for i in range(600)]
        items.append(_child("2000", "page"))
        adapter = ConfluenceV2Adapter(
            session=MagicMock(spec=requests.Session), base_url=GATEWAY_URL
        )
        adapter.session.get.side_effect = _paged_children(items)

        with pytest.raises(ChildListTruncatedError, match="stopped after 2 requests"):
            adapter.get_page_children(
                "123", limit=1, include_folders=False, max_pages=2
            )

        assert adapter.session.get.call_count == 2

    def test_page_lookup_is_chunked_at_250_ids(self) -> None:
        items = [_child(str(3000 + i), "page") for i in range(300)]
        adapter = ConfluenceV2Adapter(
            session=MagicMock(spec=requests.Session), base_url=GATEWAY_URL
        )
        adapter.session.get.side_effect = _paged_children(items)

        children = adapter.get_page_children("123", limit=300)

        assert len(children) == 300
        assert all(c["version"]["number"] == 1 for c in children)
        lookups = [
            call.kwargs["params"]
            for call in adapter.session.get.call_args_list
            if call.args[0] == f"{GATEWAY_URL}/api/v2/pages"
        ]
        assert [len(p["id"]) for p in lookups] == [250, 50]
        assert [p["limit"] for p in lookups] == [250, 50]

    def test_warns_when_page_lookup_omits_ids(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        mixin = _mixin(PagesMixin)
        routes = _v2_routes()
        routes[f"{GATEWAY_URL}/api/v2/pages"] = {
            "results": [PAGE_DETAILS["results"][0]]
        }
        mixin.confluence._session.get.side_effect = _route(routes)

        with caplog.at_level(logging.WARNING, logger="mcp-atlassian"):
            children = mixin.get_page_children("123", include_folders=False)

        assert [c.id for c in children] == ["201", "204"]
        assert children[1].version is None
        assert "1 child page(s) of page '123' were not returned" in caplog.text

    def test_auth_error_raises_authentication_error(self) -> None:
        mixin = _mixin(PagesMixin)
        mixin.confluence._session.get.side_effect = _route(
            {CHILDREN_URL: _response(401)}
        )

        with pytest.raises(MCPAtlassianAuthenticationError):
            mixin.get_page_children("123")

    def test_page_lookup_failure_raises(self) -> None:
        mixin = _mixin(PagesMixin)
        routes = _v2_routes()
        routes[f"{GATEWAY_URL}/api/v2/pages"] = _response(500)
        mixin.confluence._session.get.side_effect = _route(routes)

        with pytest.raises(ValueError, match="Failed to get children of page '123'"):
            mixin.get_page_children("123")


def _http_response(status_code: int, payload: Any) -> requests.Response:
    response = requests.Response()
    response.status_code = status_code
    response._content = json.dumps(payload).encode()
    response.headers["Content-Type"] = "application/json"
    return response


class TestPageChildrenV1:
    """Server/DC and non-OAuth Cloud keep using the v1 child endpoints.

    Uses the real atlassian-python-api client with only the HTTP session
    mocked.
    """

    @pytest.fixture
    def mixin(self) -> PagesMixin:
        mixin = _mixin(PagesMixin, auth_type="basic")
        session = MagicMock(spec=requests.Session)
        mixin.confluence = Confluence(url=SITE_URL, session=session)
        return mixin

    def test_basic_auth_uses_v1_child_endpoints(self, mixin: PagesMixin) -> None:
        session = mixin.confluence._session
        session.request.side_effect = [
            _http_response(
                200,
                {
                    "results": [
                        {
                            "id": "201",
                            "type": "page",
                            "title": "Child A",
                            "version": {"number": 3},
                            "_expandable": {"space": "/rest/api/space/ENG"},
                        }
                    ]
                },
            ),
            _http_response(
                200,
                {"results": [{"id": "203", "type": "folder", "title": "Folder B"}]},
            ),
        ]

        children = mixin.get_page_children("123", limit=10)

        assert [c.id for c in children] == ["201", "203"]
        assert children[0].version is not None
        assert children[0].version.number == 3
        assert children[0].space is not None
        assert children[0].space.key == "ENG"
        urls = [call.kwargs["url"] for call in session.request.call_args_list]
        assert urls[0].startswith(f"{SITE_URL}/rest/api/content/123/child/page?")
        assert urls[1].startswith(f"{SITE_URL}/rest/api/content/123/child/folder?")
        session.get.assert_not_called()

    def test_v1_page_error_raises(self, mixin: PagesMixin) -> None:
        mixin.confluence._session.request.return_value = _http_response(
            500, {"message": "Internal error"}
        )

        with pytest.raises(Exception, match="Internal error|500"):
            mixin.get_page_children("123")

    def test_v1_auth_error_raises_authentication_error(self, mixin: PagesMixin) -> None:
        mixin.confluence._session.request.return_value = _http_response(
            401, {"message": "Unauthorized"}
        )

        with pytest.raises(MCPAtlassianAuthenticationError):
            mixin.get_page_children("123")
