"""Tests for Cloud OAuth gateway handling: attachment URLs, v2 labels, embeds."""

from unittest.mock import MagicMock, Mock, patch

import pytest
import requests
from requests.exceptions import HTTPError

from mcp_atlassian.confluence.attachments import AttachmentsMixin
from mcp_atlassian.confluence.labels import LabelsMixin
from mcp_atlassian.confluence.v2_adapter import ConfluenceV2Adapter

GATEWAY_URL = "https://api.atlassian.com/ex/confluence/test-cloud-id"
SITE_URL = "https://example.atlassian.net/wiki"


def _response(status_code: int, payload: dict | None = None) -> Mock:
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


def _mixin(cls, *, auth_type: str = "oauth", is_cloud: bool = True):
    with patch("mcp_atlassian.confluence.client.ConfluenceClient.__init__") as init:
        init.return_value = None
        mixin = cls()
    mixin.config = MagicMock(auth_type=auth_type, is_cloud=is_cloud, url=SITE_URL)
    mixin.confluence = MagicMock()
    mixin.confluence.url = GATEWAY_URL if auth_type == "oauth" else SITE_URL
    mixin.confluence._session = MagicMock(spec=requests.Session)
    return mixin


class TestAttachmentBaseUrl:
    def test_cloud_oauth_uses_gateway_with_wiki_prefix(self):
        mixin = _mixin(AttachmentsMixin)
        assert mixin._attachment_base_url() == f"{GATEWAY_URL}/wiki"

    def test_gateway_url_already_has_wiki_prefix(self):
        mixin = _mixin(AttachmentsMixin)
        mixin.confluence.url = f"{GATEWAY_URL}/wiki/"
        assert mixin._attachment_base_url() == f"{GATEWAY_URL}/wiki"

    @pytest.mark.parametrize(
        ("auth_type", "is_cloud"),
        [("basic", True), ("pat", False), ("oauth", False)],
    )
    def test_non_gateway_uses_site_url(self, auth_type, is_cloud):
        mixin = _mixin(AttachmentsMixin, auth_type=auth_type, is_cloud=is_cloud)
        assert mixin._attachment_base_url() == SITE_URL


class TestV2Embeds:
    @pytest.fixture
    def adapter(self):
        return ConfluenceV2Adapter(
            session=MagicMock(spec=requests.Session), base_url=GATEWAY_URL
        )

    def test_get_embed_success(self, adapter):
        payload = {"id": "100", "title": "Doc", "embedUrl": "https://example.com/d"}
        adapter.session.get.return_value = _response(200, payload)

        assert adapter.get_embed("100") == payload
        adapter.session.get.assert_called_once_with(f"{GATEWAY_URL}/api/v2/embeds/100")

    @pytest.mark.parametrize("status", [401, 403, 404])
    def test_get_embed_returns_none_on_error(self, adapter, status):
        adapter.session.get.return_value = _response(status)
        assert adapter.get_embed("100") is None

    def test_get_embed_returns_none_on_request_exception(self, adapter):
        adapter.session.get.side_effect = requests.ConnectionError("boom")
        assert adapter.get_embed("100") is None


class TestV2Labels:
    @pytest.fixture
    def adapter(self):
        return ConfluenceV2Adapter(
            session=MagicMock(spec=requests.Session), base_url=GATEWAY_URL
        )

    def test_page_labels(self, adapter):
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

    def test_falls_back_to_blogposts_on_404(self, adapter):
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

    def test_attachment_labels(self, adapter):
        adapter.session.get.return_value = _response(200, {"results": []})

        assert adapter.get_content_labels("att789") == {"results": []}
        assert adapter.session.get.call_args.args[0] == (
            f"{GATEWAY_URL}/api/v2/attachments/att789/labels"
        )

    def test_follows_cursor_pagination(self, adapter):
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

    def test_auth_error_propagates(self, adapter):
        adapter.session.get.return_value = _response(401)
        with pytest.raises(HTTPError):
            adapter.get_content_labels("123")

    def test_not_found_raises_value_error(self, adapter):
        adapter.session.get.return_value = _response(404)
        with pytest.raises(ValueError, match="Failed to get labels"):
            adapter.get_content_labels("123")


class TestLabelsMixinOAuth:
    def test_oauth_reads_labels_via_v2(self):
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

    def test_basic_auth_still_uses_v1(self):
        mixin = _mixin(LabelsMixin, auth_type="basic")
        mixin.confluence.get_page_labels.return_value = {"results": []}

        assert mixin.get_page_labels("123") == []
        mixin.confluence.get_page_labels.assert_called_once_with(page_id="123")

    def test_embed_id_reports_embed_url(self):
        mixin = _mixin(LabelsMixin)
        with (
            patch.object(
                ConfluenceV2Adapter,
                "get_content_labels",
                side_effect=ValueError("not found"),
            ),
            patch.object(
                ConfluenceV2Adapter,
                "get_embed",
                return_value={"title": "Doc", "embedUrl": "https://example.com/d"},
            ),
        ):
            with pytest.raises(Exception, match="https://example.com/d"):
                mixin.get_page_labels("100")


class TestEmbedInfo:
    def test_returns_payload_for_embed(self):
        mixin = _mixin(AttachmentsMixin)
        embed = {
            "title": "Doc",
            "embedUrl": "https://example.com/d",
            "parentId": "9",
            "parentType": "folder",
        }
        with patch.object(ConfluenceV2Adapter, "get_embed", return_value=embed):
            info = mixin.get_embed_info("100")

        assert info["success"] is False
        assert info["content_type"] == "embed"
        assert info["embed_url"] == "https://example.com/d"
        assert info["parent_type"] == "folder"

    def test_skips_attachment_ids_and_non_oauth(self):
        with patch.object(ConfluenceV2Adapter, "get_embed") as get_embed:
            assert _mixin(AttachmentsMixin).get_embed_info("att1") is None
            assert (
                _mixin(AttachmentsMixin, auth_type="basic").get_embed_info("100")
                is None
            )
        get_embed.assert_not_called()

    def test_get_content_attachments_reports_embed(self):
        mixin = _mixin(AttachmentsMixin)
        with (
            patch.object(
                ConfluenceV2Adapter,
                "get_page_attachments",
                side_effect=ValueError("404 Not Found"),
            ),
            patch.object(
                ConfluenceV2Adapter,
                "get_embed",
                return_value={"title": "Doc", "embedUrl": "https://example.com/d"},
            ),
        ):
            result = mixin.get_content_attachments("100")

        assert result["success"] is False
        assert result["content_type"] == "embed"
        assert result["embed_url"] == "https://example.com/d"
