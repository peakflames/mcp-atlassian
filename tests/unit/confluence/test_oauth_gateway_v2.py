"""Tests for Cloud OAuth gateway handling: attachment URLs, v2 labels, embeds."""

import json
import logging
from pathlib import Path
from typing import Any, TypeVar
from unittest.mock import MagicMock, Mock, patch

import pytest
import requests
from requests.exceptions import HTTPError

from mcp_atlassian.confluence.attachments import AttachmentsMixin
from mcp_atlassian.confluence.client import ConfluenceClient
from mcp_atlassian.confluence.labels import LabelsMixin
from mcp_atlassian.confluence.v2_adapter import ConfluenceV2Adapter
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
