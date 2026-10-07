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
    """batch_create_issues must be rejected when any item is in a BLOCKED project.

    Calls the real tool function (not a stub) and binds the argument through
    the tool's own signature, so a parameter rename on the tool cannot leave
    the guard reading a name the tool no longer has.
    """
    import inspect
    import json

    from mcp_atlassian.servers.jira import batch_create_issues

    tool_fn = batch_create_issues.fn
    issues = [
        {"project_key": "SAFE", "summary": "ok", "issue_type": "Task"},
        {"project_key": "PRIV", "summary": "blocked", "issue_type": "Task"},
    ]
    jira_cfg = _make_jira_config(projects_blocked="PRIV")
    ctx = ContextWithAccess(jira_config=jira_cfg)
    bound = inspect.signature(tool_fn).bind(ctx, issues=json.dumps(issues))
    kwargs = {k: v for k, v in bound.arguments.items() if k != "ctx"}
    with pytest.raises(ToolError, match="blocked"):
        await tool_fn(ctx, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("project_key", [" PRIV", "PRIV ", " priv "])
async def test_check_write_access_strips_direct_project_key(project_key):
    """A direct project_key is compared after trimming whitespace.

    Calls the real create_issue tool function; the MCP schema pattern on
    ``project_key`` is not applied on this path, so the guard itself is tested.
    """
    from mcp_atlassian.servers.jira import create_issue

    ctx = ContextWithAccess(jira_config=_make_jira_config(projects_blocked="PRIV"))
    with pytest.raises(ToolError, match="blocked"):
        await create_issue.fn(
            ctx, project_key=project_key, summary="s", issue_type="Task"
        )


@pytest.mark.asyncio
async def test_check_write_access_rejects_non_key_direct_project_key():
    from mcp_atlassian.servers.jira import create_issue

    ctx = ContextWithAccess(jira_config=_make_jira_config(projects_blocked="PRIV"))
    with pytest.raises(ToolError, match="Cannot determine the Jira project"):
        await create_issue.fn(ctx, project_key="10000", summary="s", issue_type="Task")


# ---------------------------------------------------------------------------
# Guard kwarg names vs. real tool signatures (mechanical audit)
# ---------------------------------------------------------------------------


def _guard_kwarg_names() -> set[str]:
    """Every tool kwarg name ``check_write_access`` reads via ``kwargs.get``.

    Parsed from the decorator source so literal names, inline tuples and
    module-level tuple constants are all picked up.
    """
    import ast
    from pathlib import Path

    from mcp_atlassian.utils import decorators

    tree = ast.parse(Path(decorators.__file__).read_text(encoding="utf-8"))

    def str_elts(node: ast.AST) -> list[str]:
        if isinstance(node, ast.Tuple | ast.List):
            return [
                e.value
                for e in node.elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str)
            ]
        return []

    module_tuples: dict[str, list[str]] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    module_tuples[target.id] = str_elts(node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.value is not None:
                module_tuples[node.target.id] = str_elts(node.value)

    func = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "check_write_access"
    )
    loop_values: dict[str, list[str]] = {}
    for node in ast.walk(func):
        if isinstance(node, ast.For) and isinstance(node.target, ast.Name):
            values = str_elts(node.iter)
            if isinstance(node.iter, ast.Name):
                values = module_tuples.get(node.iter.id, [])
            loop_values.setdefault(node.target.id, []).extend(values)

    names: set[str] = set()
    for node in ast.walk(func):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "kwargs"
            and node.args
        ):
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                names.add(arg.value)
            elif isinstance(arg, ast.Name):
                assert loop_values.get(arg.id), (
                    f"cannot resolve guard kwarg variable {arg.id!r}"
                )
                names.update(loop_values[arg.id])
    assert names, "no kwargs.get(...) reads found in check_write_access"
    return names


def _guarded_tool_params(module_name: str) -> dict[str, set[str]]:
    """Parameter names of every tool in a server module using check_write_access.

    Decorators are read from the module source; parameter names come from the
    real tool objects' signatures.
    """
    import ast
    import importlib
    import inspect
    from pathlib import Path

    module = importlib.import_module(module_name)
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    guarded = [
        node.name
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef)
        and any(
            isinstance(d, ast.Name) and d.id == "check_write_access"
            for d in node.decorator_list
        )
    ]
    tools: dict[str, set[str]] = {}
    for name in guarded:
        tool = getattr(module, name)
        fn = getattr(tool, "fn", tool)
        tools[name] = set(inspect.signature(fn).parameters) - {"ctx"}
    return tools


# Which guarded tools each guard kwarg is meant for. A new guard kwarg must be
# added here, and every listed tool must really have that parameter.
_GUARD_KWARG_TARGETS: dict[str, tuple[str, set[str]]] = {
    "project_key": (
        "jira",
        {"create_issue", "create_version", "batch_create_versions"},
    ),
    "issue_key": (
        "jira",
        {
            "add_watcher",
            "remove_watcher",
            "update_issue",
            "delete_issue",
            "add_comment",
            "edit_comment",
            "add_worklog",
            "link_to_epic",
            "create_remote_issue_link",
            "transition_issue",
            "update_proforma_form_answers",
        },
    ),
    "inward_issue_key": ("jira", {"create_issue_link"}),
    "outward_issue_key": ("jira", {"create_issue_link"}),
    "epic_key": ("jira", {"link_to_epic"}),
    "issue_keys": ("jira", {"add_issues_to_sprint"}),
    "fields": ("jira", {"update_issue", "transition_issue"}),
    "additional_fields": ("jira", {"create_issue", "update_issue"}),
    "issues": ("jira", {"batch_create_issues"}),
    "space_key": ("confluence", {"create_page"}),
    "target_space_key": ("confluence", {"move_page"}),
    "page_id": ("confluence", {"update_page", "delete_page", "move_page"}),
}

# ID parameters on Jira write tools that name something inside the issue the
# tool already identifies by issue_key, or a user; no separate project.
_JIRA_NON_PROJECT_ID_PARAMS = {"comment_id", "form_id", "transition_id", "account_id"}

# Project resolved only via an API lookup; not checked by the guard.
_JIRA_LOOKUP_ONLY_PARAMS_KNOWN_GAP = {"board_id", "sprint_id", "link_id"}


def _all_guarded_tools() -> dict[str, dict[str, set[str]]]:
    return {
        "jira": _guarded_tool_params("mcp_atlassian.servers.jira"),
        "confluence": _guarded_tool_params("mcp_atlassian.servers.confluence"),
    }


def test_guarded_tool_discovery_finds_write_tools():
    tools = _all_guarded_tools()
    assert {"batch_create_issues", "link_to_epic", "add_issues_to_sprint"} <= set(
        tools["jira"]
    )
    assert "create_page" in tools["confluence"]


def test_every_guard_kwarg_exists_on_a_guarded_tool():
    """Each name the guard reads must be a real parameter of some guarded tool."""
    tools = _all_guarded_tools()
    all_params = set().union(*tools["jira"].values(), *tools["confluence"].values())
    missing = sorted(_guard_kwarg_names() - all_params)
    assert not missing, f"guard reads kwargs no guarded tool has: {missing}"


def test_guard_kwargs_exist_on_their_intended_tools():
    tools = _all_guarded_tools()
    guard_names = _guard_kwarg_names()
    assert guard_names == set(_GUARD_KWARG_TARGETS), (
        "guard kwarg names and _GUARD_KWARG_TARGETS differ: "
        f"{sorted(guard_names ^ set(_GUARD_KWARG_TARGETS))}"
    )
    for kwarg, (service, tool_names) in _GUARD_KWARG_TARGETS.items():
        for tool_name in tool_names:
            assert tool_name in tools[service], f"{tool_name} is not a guarded tool"
            assert kwarg in tools[service][tool_name], (
                f"guard reads {kwarg!r} but {service} tool {tool_name!r} "
                f"has parameters {sorted(tools[service][tool_name])}"
            )


def test_jira_write_tool_key_params_are_read_by_guard():
    """Any key-like parameter on a Jira write tool must be read by the guard."""
    guard_names = _guard_kwarg_names()
    unguarded: list[str] = []
    for tool_name, params in _all_guarded_tools()["jira"].items():
        for param in params:
            key_like = param.endswith(("_key", "_keys")) or param in {
                "issues",
                "fields",
                "additional_fields",
            }
            if key_like and param not in guard_names:
                unguarded.append(f"{tool_name}.{param}")
            classified = (
                _JIRA_NON_PROJECT_ID_PARAMS | _JIRA_LOOKUP_ONLY_PARAMS_KNOWN_GAP
            )
            if param.endswith("_id") and param not in classified:
                unguarded.append(f"{tool_name}.{param} (unclassified id)")
    assert not unguarded, f"key-bearing parameters not read by the guard: {unguarded}"


def test_jira_id_param_sets_match_write_tools():
    """Every classified ID parameter still exists on some Jira write tool."""
    all_params = set().union(*_all_guarded_tools()["jira"].values())
    stale = sorted(
        (_JIRA_NON_PROJECT_ID_PARAMS | _JIRA_LOOKUP_ONLY_PARAMS_KNOWN_GAP) - all_params
    )
    assert not stale, f"classified ID parameters no write tool has: {stale}"


@pytest.mark.asyncio
async def test_check_write_access_no_config_allows_all():
    """When no jira/confluence config is present, writes are unrestricted."""

    @check_write_access
    async def create_issue(ctx, issue_key):
        return "ok"

    ctx = ContextWithAccess()  # no jira_config, no conf_config
    result = await create_issue(ctx, issue_key="ANYTHING-1")
    assert result == "ok"
