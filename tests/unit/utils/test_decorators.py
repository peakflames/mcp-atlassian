from unittest.mock import MagicMock

import pytest
from fastmcp.exceptions import ToolError
from requests.exceptions import HTTPError

from mcp_atlassian.exceptions import MCPAtlassianAuthenticationError
from mcp_atlassian.utils.decorators import (
    check_write_access,
    handle_auth_errors,
    handle_tool_errors,
)


class DummyContext:
    def __init__(self, read_only):
        self.request_context = MagicMock()
        self.request_context.lifespan_context = {
            "app_lifespan_context": MagicMock(read_only=read_only)
        }


@pytest.mark.asyncio
async def test_check_write_access_blocks_in_read_only():
    @check_write_access
    async def dummy_tool(ctx, x):
        return x * 2

    ctx = DummyContext(read_only=True)
    with pytest.raises(ToolError) as exc:
        await dummy_tool(ctx, 3)
    assert "read-only mode" in str(exc.value)


@pytest.mark.asyncio
async def test_check_write_access_allows_in_writable():
    @check_write_access
    async def dummy_tool(ctx, x):
        return x * 2

    ctx = DummyContext(read_only=False)
    result = await dummy_tool(ctx, 4)
    assert result == 8


@pytest.mark.asyncio
async def test_handle_tool_errors_wraps_exception_as_tool_error():
    @handle_tool_errors
    async def failing_tool():
        raise ValueError("something went wrong")

    with pytest.raises(ToolError) as exc:
        await failing_tool()
    assert "something went wrong" in str(exc.value)


@pytest.mark.asyncio
async def test_handle_tool_errors_passes_through_tool_error():
    @handle_tool_errors
    async def tool_with_tool_error():
        raise ToolError("explicit tool error")

    with pytest.raises(ToolError) as exc:
        await tool_with_tool_error()
    assert "explicit tool error" in str(exc.value)


@pytest.mark.asyncio
async def test_handle_tool_errors_preserves_return_value():
    @handle_tool_errors
    async def good_tool():
        return "success"

    result = await good_tool()
    assert result == "success"


# --- handle_auth_errors tests ---


def _make_http_error(status_code: int) -> HTTPError:
    """Create an HTTPError with a mocked response."""
    response = MagicMock()
    response.status_code = status_code
    err = HTTPError(response=response)
    return err


class _FakeService:
    """Dummy class to test the self-bound decorator."""

    @handle_auth_errors("Test API")
    def do_work(self, value: str) -> str:
        return f"ok:{value}"

    @handle_auth_errors("Test API")
    def raise_http_error(self, status_code: int) -> None:
        raise _make_http_error(status_code)

    @handle_auth_errors("Test API")
    def raise_value_error(self) -> None:
        raise ValueError("bad input")


def test_handle_auth_errors_returns_value():
    svc = _FakeService()
    assert svc.do_work("hello") == "ok:hello"


@pytest.mark.parametrize("status_code", [401, 403])
def test_handle_auth_errors_catches_auth_errors(
    status_code: int,
) -> None:
    svc = _FakeService()
    with pytest.raises(MCPAtlassianAuthenticationError) as exc:
        svc.raise_http_error(status_code)
    assert "Authentication failed" in str(exc.value)
    assert str(status_code) in str(exc.value)


def test_handle_auth_errors_passes_through_404():
    svc = _FakeService()
    with pytest.raises(HTTPError) as exc:
        svc.raise_http_error(404)
    assert exc.value.response.status_code == 404


def test_handle_auth_errors_passes_through_non_http_error():
    svc = _FakeService()
    with pytest.raises(ValueError, match="bad input"):
        svc.raise_value_error()


def test_handle_auth_errors_passes_through_no_response():
    """HTTPError with response=None should re-raise."""

    class Svc:
        @handle_auth_errors("Test API")
        def fail(self) -> None:
            raise HTTPError(response=None)

    with pytest.raises(HTTPError):
        Svc().fail()


# ---------------------------------------------------------------------------
# Per-project / per-space write access checks in check_write_access
# ---------------------------------------------------------------------------


def _make_jira_config(projects_blocked=None, projects_readonly=None):
    from mcp_atlassian.jira.config import JiraConfig

    return JiraConfig(
        url="https://test.atlassian.net",
        auth_type="basic",
        username="u",
        api_token="t",
        projects_blocked=projects_blocked,
        projects_readonly=projects_readonly,
    )


def _make_conf_config(spaces_blocked=None, spaces_readonly=None):
    from mcp_atlassian.confluence.config import ConfluenceConfig

    return ConfluenceConfig(
        url="https://test.atlassian.net/wiki",
        auth_type="basic",
        username="u",
        api_token="t",
        spaces_blocked=spaces_blocked,
        spaces_readonly=spaces_readonly,
    )


class ContextWithAccess:
    """Context that exposes jira/confluence config on the lifespan app context."""

    def __init__(self, *, read_only=False, jira_config=None, conf_config=None):
        app_ctx = MagicMock()
        app_ctx.read_only = read_only
        app_ctx.full_jira_config = jira_config
        app_ctx.full_confluence_config = conf_config
        self.request_context = MagicMock()
        self.request_context.lifespan_context = {"app_lifespan_context": app_ctx}


@pytest.mark.asyncio
async def test_check_write_access_rejects_blocked_project():
    """Writes to a BLOCKED project must raise ToolError."""

    @check_write_access
    async def create_issue(ctx, issue_key):
        return "ok"

    jira_cfg = _make_jira_config(projects_blocked="PRIV")
    ctx = ContextWithAccess(jira_config=jira_cfg)
    with pytest.raises(ToolError, match="blocked"):
        await create_issue(ctx, issue_key="PRIV-1")


@pytest.mark.asyncio
async def test_check_write_access_rejects_readonly_on_write():
    """Writes to a READONLY project must raise ToolError."""

    @check_write_access
    async def update_issue(ctx, issue_key):
        return "ok"

    jira_cfg = _make_jira_config(projects_readonly="LEGACY")
    ctx = ContextWithAccess(jira_config=jira_cfg)
    with pytest.raises(ToolError, match="read-only"):
        await update_issue(ctx, issue_key="LEGACY-5")


@pytest.mark.asyncio
async def test_check_write_access_allows_non_restricted_project():
    """Writes to an unrestricted project must succeed."""

    @check_write_access
    async def create_issue(ctx, project_key):
        return "created"

    jira_cfg = _make_jira_config(projects_blocked="PRIV", projects_readonly="RO")
    ctx = ContextWithAccess(jira_config=jira_cfg)
    result = await create_issue(ctx, project_key="SAFE")
    assert result == "created"


@pytest.mark.asyncio
async def test_check_write_access_rejects_blocked_confluence_space():
    """Writes to a BLOCKED Confluence space must raise ToolError."""

    @check_write_access
    async def create_page(ctx, space_key, title):
        return "ok"

    conf_cfg = _make_conf_config(spaces_blocked="LEGAL")
    ctx = ContextWithAccess(conf_config=conf_cfg)
    with pytest.raises(ToolError, match="blocked"):
        await create_page(ctx, space_key="LEGAL", title="Test")


@pytest.mark.asyncio
async def test_check_write_access_rejects_readonly_confluence_space():
    """Writes to a READONLY Confluence space must raise ToolError."""

    @check_write_access
    async def update_page(ctx, space_key):
        return "ok"

    conf_cfg = _make_conf_config(spaces_readonly="LEGACY")
    ctx = ContextWithAccess(conf_config=conf_cfg)
    with pytest.raises(ToolError, match="read-only"):
        await update_page(ctx, space_key="LEGACY")


@pytest.mark.asyncio
async def test_check_write_access_batch_rejects_blocked_project():
    """batch_create_issues must be rejected when any item is in a BLOCKED project."""

    @check_write_access
    async def batch_create_issues(ctx, issues_data):
        return "ok"

    import json

    issues = [
        {"project_key": "SAFE", "summary": "ok"},
        {"project_key": "PRIV", "summary": "blocked"},
    ]
    jira_cfg = _make_jira_config(projects_blocked="PRIV")
    ctx = ContextWithAccess(jira_config=jira_cfg)
    with pytest.raises(ToolError, match="blocked"):
        await batch_create_issues(ctx, issues_data=json.dumps(issues))


@pytest.mark.asyncio
async def test_check_write_access_no_config_allows_all():
    """When no jira/confluence config is present, writes are unrestricted."""

    @check_write_access
    async def create_issue(ctx, issue_key):
        return "ok"

    ctx = ContextWithAccess()  # no jira_config, no conf_config
    result = await create_issue(ctx, issue_key="ANYTHING-1")
    assert result == "ok"
