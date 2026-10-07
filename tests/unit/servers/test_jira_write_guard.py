"""Per-project Jira write guard, exercised through the real server tools.

Each test calls a real tool from ``mcp_atlassian.servers.jira`` through a
FastMCP client. The Jira fetcher, client and ``check_write_access`` all run
for real; HTTP is faked only at ``requests.Session.request`` so the tests can
assert exactly which requests reach Jira.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import patch

import pytest
import requests
from fastmcp import Client, FastMCP
from fastmcp.client import FastMCPTransport
from fastmcp.exceptions import ToolError

from mcp_atlassian.jira.config import JiraConfig
from mcp_atlassian.servers import jira as jira_tools
from mcp_atlassian.servers.context import MainAppContext

BASE_URL = "https://test.atlassian.net"
WRITE_METHODS = {"POST", "PUT", "DELETE", "PATCH"}

# Project "PRIV" is blocked, "RO" is read-only, "SAFE" is unrestricted.
BLOCKED = "PRIV"
READONLY = "RO"


def _make_config(
    projects_blocked: str | None = BLOCKED, projects_readonly: str | None = READONLY
) -> JiraConfig:
    return JiraConfig(
        url=BASE_URL,
        auth_type="basic",
        username="u",
        api_token="t",
        projects_blocked=projects_blocked,
        projects_readonly=projects_readonly,
    )


def _issue_payload(key: str) -> dict[str, Any]:
    project = key.split("-", 1)[0]
    return {
        "id": str(abs(hash(key)) % 100000),
        "key": key,
        "self": f"{BASE_URL}/rest/api/2/issue/{key}",
        "fields": {
            "summary": f"Summary {key}",
            "issuetype": {"name": "Epic" if key.endswith("-500") else "Task"},
            "project": {"key": project, "name": project},
            "status": {"name": "To Do"},
        },
    }


def _response(method: str, url: str, status: int, payload: Any) -> requests.Response:
    resp = requests.Response()
    resp.status_code = status
    resp.url = url
    resp._content = b"" if payload is None else json.dumps(payload).encode()
    resp.headers["Content-Type"] = "application/json"
    resp.request = requests.Request(method, url).prepare()
    return resp


class FakeJiraHTTP:
    """Records every request and returns minimal Jira-shaped responses."""

    def __init__(self, field_defs: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.field_defs = field_defs or []

    def __call__(
        self, _session: requests.Session, method: str, url: str, **kwargs: Any
    ) -> requests.Response:
        method = method.upper()
        path = url.split("://", 1)[-1].split("/", 1)[-1].split("?", 1)[0]
        body = kwargs.get("json") if kwargs.get("json") is not None else None
        if body is None and kwargs.get("data"):
            try:
                body = json.loads(kwargs["data"])
            except (TypeError, ValueError):
                body = kwargs["data"]
        self.calls.append((method, "/" + path, body))

        if method == "GET" and path.endswith("rest/api/2/field"):
            return _response(method, url, 200, self.field_defs)
        if method == "POST" and path.endswith("rest/api/2/issue"):
            project = (body or {}).get("fields", {}).get("project", {})
            key = f"{project.get('key', 'X')}-1" if isinstance(project, dict) else "X-1"
            return _response(method, url, 201, {"id": "10001", "key": key})
        if method == "POST" and path.endswith("rest/api/2/issue/bulk"):
            created = [
                {"id": str(i), "key": f"{u['fields']['project']['key']}-{i + 1}"}
                for i, u in enumerate((body or {}).get("issueUpdates", []))
            ]
            return _response(method, url, 201, {"issues": created, "errors": []})
        match = re.search(r"rest/api/2/issue/([A-Za-z0-9_]+-\d+)$", path)
        if match and method == "GET":
            return _response(method, url, 200, _issue_payload(match.group(1)))
        if method in WRITE_METHODS:
            return _response(method, url, 204, None)
        return _response(method, url, 200, {})

    @property
    def writes(self) -> list[tuple[str, str, Any]]:
        return [c for c in self.calls if c[0] in WRITE_METHODS]

    def sequence(self) -> list[tuple[str, str]]:
        return [(m, p) for m, p, _ in self.calls]


def _make_server(config: JiraConfig) -> FastMCP:
    @asynccontextmanager
    async def lifespan(_app: FastMCP) -> AsyncGenerator[dict[str, Any], None]:
        # Same shape as the production lifespan in servers/main.py
        yield {
            "app_lifespan_context": MainAppContext(
                full_jira_config=config, read_only=False
            )
        }

    server = FastMCP("JiraWriteGuardTest", lifespan=lifespan)
    for tool in (
        jira_tools.batch_create_issues,
        jira_tools.link_to_epic,
        jira_tools.add_issues_to_sprint,
        jira_tools.create_issue,
        jira_tools.update_issue,
        jira_tools.transition_issue,
        jira_tools.update_proforma_form_answers,
    ):
        server.add_tool(tool)
    return server


async def _call(
    config: JiraConfig,
    tool: str,
    args: dict[str, Any],
    field_defs: list[dict[str, Any]] | None = None,
) -> tuple[FakeJiraHTTP, Any, Exception | None]:
    fake = FakeJiraHTTP(field_defs)
    result: Any = None
    error: Exception | None = None
    with patch.object(requests.Session, "request", autospec=True, side_effect=fake):
        async with Client(transport=FastMCPTransport(_make_server(config))) as client:
            try:
                result = await client.call_tool(tool, args)
            except ToolError as exc:
                error = exc
    return fake, result, error


def _assert_denied(fake: FakeJiraHTTP, error: Exception | None, match: str) -> None:
    assert error is not None, "expected the write to be denied"
    assert re.search(match, str(error)), str(error)
    assert fake.writes == [], f"write request sent despite denial: {fake.writes}"
    assert fake.calls == [], f"request sent despite denial: {fake.calls}"


# ---------------------------------------------------------------------------
# batch_create_issues
# ---------------------------------------------------------------------------


def _batch(*items: dict[str, Any]) -> dict[str, Any]:
    return {"issues": json.dumps(list(items))}


def _item(project_key: str, **extra: Any) -> dict[str, Any]:
    return {"project_key": project_key, "summary": "s", "issue_type": "Task", **extra}


@pytest.mark.anyio
async def test_batch_create_denied_when_one_item_in_blocked_project():
    fake, _, error = await _call(
        _make_config(),
        "batch_create_issues",
        _batch(_item("SAFE"), _item(BLOCKED)),
    )
    _assert_denied(fake, error, "blocked")


@pytest.mark.anyio
async def test_batch_create_denied_when_one_item_in_readonly_project():
    fake, _, error = await _call(
        _make_config(),
        "batch_create_issues",
        _batch(_item("SAFE"), _item(READONLY)),
    )
    _assert_denied(fake, error, "read-only")


@pytest.mark.anyio
async def test_batch_create_denied_when_item_overrides_project_field():
    fake, _, error = await _call(
        _make_config(),
        "batch_create_issues",
        _batch(_item("SAFE", project={"key": BLOCKED})),
    )
    _assert_denied(fake, error, "blocked")


@pytest.mark.anyio
async def test_batch_create_denied_when_item_project_cannot_be_determined():
    fake, _, error = await _call(
        _make_config(),
        "batch_create_issues",
        _batch(_item("SAFE", project={"id": "10001"})),
    )
    _assert_denied(fake, error, "Cannot determine the Jira project")


@pytest.mark.anyio
async def test_batch_create_item_without_project_left_to_tool():
    """No project at all: the guard lets it through and the tool rejects it."""
    fake, _, error = await _call(
        _make_config(),
        "batch_create_issues",
        _batch({"summary": "s", "issue_type": "Task"}),
    )
    assert error is not None
    assert "Missing required fields" in str(error)
    assert fake.writes == []


@pytest.mark.anyio
async def test_batch_create_allowed_projects_created():
    fake, result, error = await _call(
        _make_config(),
        "batch_create_issues",
        _batch(_item("SAFE"), _item("OTHER")),
    )
    assert error is None
    bulk = [c for c in fake.writes if c[1].endswith("/rest/api/2/issue/bulk")]
    assert len(bulk) == 1
    sent_projects = [u["fields"]["project"]["key"] for u in bulk[0][2]["issueUpdates"]]
    assert sent_projects == ["SAFE", "OTHER"]
    payload = json.loads(result.content[0].text)
    assert [i["key"] for i in payload["issues"]] == ["SAFE-1", "OTHER-2"]


# ---------------------------------------------------------------------------
# link_to_epic
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("issue_key", "epic_key", "match"),
    [
        ("SAFE-1", f"{BLOCKED}-500", "blocked"),
        ("SAFE-1", f"{READONLY}-500", "read-only"),
        (f"{BLOCKED}-1", "SAFE-500", "blocked"),
    ],
    ids=["epic-blocked", "epic-readonly", "issue-blocked"],
)
async def test_link_to_epic_denied(issue_key, epic_key, match):
    fake, _, error = await _call(
        _make_config(),
        "link_to_epic",
        {"issue_key": issue_key, "epic_key": epic_key},
    )
    _assert_denied(fake, error, match)


@pytest.mark.anyio
async def test_link_to_epic_allowed():
    fake, _, error = await _call(
        _make_config(),
        "link_to_epic",
        {"issue_key": "SAFE-1", "epic_key": "OTHER-500"},
    )
    assert error is None
    assert fake.writes == [
        (
            "PUT",
            "/rest/api/2/issue/SAFE-1",
            {"fields": {"parent": {"key": "OTHER-500"}}},
        )
    ]


# ---------------------------------------------------------------------------
# add_issues_to_sprint
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("issue_keys", "match"),
    [
        (f"SAFE-1,{BLOCKED}-2", "blocked"),
        (f"SAFE-1, {READONLY}-3", "read-only"),
        ("SAFE-1,10001", "Cannot determine the Jira project"),
    ],
    ids=["blocked", "readonly", "numeric-id"],
)
async def test_add_issues_to_sprint_denied(issue_keys, match):
    fake, _, error = await _call(
        _make_config(),
        "add_issues_to_sprint",
        {"sprint_id": "7", "issue_keys": issue_keys},
    )
    _assert_denied(fake, error, match)


@pytest.mark.anyio
async def test_add_issues_to_sprint_allowed():
    fake, _, error = await _call(
        _make_config(),
        "add_issues_to_sprint",
        {"sprint_id": "7", "issue_keys": "SAFE-1, OTHER-2"},
    )
    assert error is None
    assert fake.writes == [
        ("POST", "/rest/agile/1.0/sprint/7/issue", {"issues": ["SAFE-1", "OTHER-2"]})
    ]


# ---------------------------------------------------------------------------
# JSON field arguments: create_issue / update_issue / transition_issue
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("additional_fields", "match"),
    [
        ({"project": {"key": BLOCKED}}, "blocked"),
        ({"parent": f"{BLOCKED}-1"}, "blocked"),
        ({"epicKey": f"{READONLY}-500"}, "read-only"),
        ({"project": {"id": "10001"}}, "Cannot determine the Jira project"),
    ],
    ids=["project-override", "parent", "epic-alias", "project-id"],
)
async def test_create_issue_additional_fields_denied(additional_fields, match):
    fake, _, error = await _call(
        _make_config(),
        "create_issue",
        {
            "project_key": "SAFE",
            "summary": "s",
            "issue_type": "Task",
            "additional_fields": json.dumps(additional_fields),
        },
    )
    _assert_denied(fake, error, match)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("args", "match"),
    [
        ({"fields": json.dumps({"parent": {"key": f"{BLOCKED}-9"}})}, "blocked"),
        (
            {
                "fields": json.dumps({"summary": "x"}),
                "additional_fields": json.dumps({"epic_link": f"{READONLY}-500"}),
            },
            "read-only",
        ),
    ],
    ids=["fields-parent", "additional-epic-link"],
)
async def test_update_issue_field_references_denied(args, match):
    fake, _, error = await _call(
        _make_config(), "update_issue", {"issue_key": "SAFE-1", **args}
    )
    _assert_denied(fake, error, match)


@pytest.mark.anyio
async def test_transition_issue_fields_parent_denied():
    fake, _, error = await _call(
        _make_config(),
        "transition_issue",
        {
            "issue_key": "SAFE-1",
            "transition_id": "31",
            "fields": json.dumps({"parent": f"{BLOCKED}-2"}),
        },
    )
    _assert_denied(fake, error, "blocked")


@pytest.mark.anyio
async def test_update_issue_malformed_fields_left_to_tool():
    """Malformed JSON is not judged by the guard; the tool reports it."""
    fake, _, error = await _call(
        _make_config(),
        "update_issue",
        {"issue_key": "SAFE-1", "fields": "{not json"},
    )
    assert error is not None
    assert "not valid JSON" in str(error)
    assert fake.writes == []


# ---------------------------------------------------------------------------
# update_proforma_form_answers (issue_key has no key pattern on this tool)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_update_form_answers_numeric_issue_id_denied():
    fake, _, error = await _call(
        _make_config(),
        "update_proforma_form_answers",
        {
            "issue_key": "10001",
            "form_id": "f1",
            "answers": [{"questionId": "1", "type": "TEXT", "value": "v"}],
        },
    )
    _assert_denied(fake, error, "Cannot determine the Jira project")


# ---------------------------------------------------------------------------
# No lists configured: the guard adds no requests and changes nothing
# ---------------------------------------------------------------------------

ALLOWED_CALLS = [
    ("batch_create_issues", _batch(_item("SAFE"), _item("OTHER"))),
    ("link_to_epic", {"issue_key": "SAFE-1", "epic_key": "OTHER-500"}),
    ("add_issues_to_sprint", {"sprint_id": "7", "issue_keys": "SAFE-1,OTHER-2"}),
    (
        "update_issue",
        {
            "issue_key": "SAFE-1",
            "fields": json.dumps({"summary": "x"}),
            "additional_fields": json.dumps({"parent": "SAFE-500"}),
        },
    ),
]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("tool", "args"), ALLOWED_CALLS, ids=[c[0] for c in ALLOWED_CALLS]
)
async def test_request_sequence_unchanged_without_lists(tool, args):
    no_lists, _, err_none = await _call(_make_config(None, None), tool, args)
    with_lists, _, err_lists = await _call(_make_config(), tool, args)
    assert err_none is None and err_lists is None
    assert no_lists.writes, "expected the tool to send a write request"
    assert with_lists.calls == no_lists.calls


@pytest.mark.anyio
async def test_numeric_sprint_issue_id_allowed_without_lists():
    """With no lists configured, unresolvable references are not checked."""
    fake, _, error = await _call(
        _make_config(None, None),
        "add_issues_to_sprint",
        {"sprint_id": "7", "issue_keys": "10001"},
    )
    assert error is None
    assert fake.writes == [
        ("POST", "/rest/agile/1.0/sprint/7/issue", {"issues": ["10001"]})
    ]


# ---------------------------------------------------------------------------
# Epic link aliases, each in `fields` and `additional_fields`
# ---------------------------------------------------------------------------

EPIC_ALIASES = ["epicKey", "epic_link", "epicLink", "Epic Link"]

# (tool, JSON argument carrying the alias, other required arguments)
EPIC_ALIAS_TARGETS = [
    ("update_issue", "fields", {"issue_key": "SAFE-1"}),
    (
        "update_issue",
        "additional_fields",
        {"issue_key": "SAFE-1", "fields": json.dumps({"summary": "x"})},
    ),
    (
        "create_issue",
        "additional_fields",
        {"project_key": "SAFE", "summary": "s", "issue_type": "Task"},
    ),
    ("transition_issue", "fields", {"issue_key": "SAFE-1", "transition_id": "31"}),
]


@pytest.mark.anyio
@pytest.mark.parametrize("alias", EPIC_ALIASES)
@pytest.mark.parametrize(
    ("tool", "json_arg", "base_args"),
    EPIC_ALIAS_TARGETS,
    ids=[f"{t}-{a}" for t, a, _ in EPIC_ALIAS_TARGETS],
)
@pytest.mark.parametrize(
    ("epic_project", "match"),
    [(BLOCKED, "blocked"), (READONLY, "read-only")],
    ids=["blocked", "readonly"],
)
async def test_epic_link_alias_denied(
    alias, tool, json_arg, base_args, epic_project, match
):
    args = {**base_args, json_arg: json.dumps({alias: f"{epic_project}-500"})}
    fake, _, error = await _call(_make_config(), tool, args)
    _assert_denied(fake, error, match)


# ---------------------------------------------------------------------------
# Surrounding whitespace and non-key project values
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("tool", "args", "match"),
    [
        (
            "batch_create_issues",
            _batch(_item("SAFE"), _item(f" {BLOCKED}")),
            "blocked",
        ),
        (
            "batch_create_issues",
            _batch(_item("SAFE", project={"key": f" {READONLY} "})),
            "read-only",
        ),
        (
            "create_issue",
            {
                "project_key": "SAFE",
                "summary": "s",
                "issue_type": "Task",
                "additional_fields": json.dumps({"project": {"key": f"{BLOCKED} "}}),
            },
            "blocked",
        ),
        (
            "create_issue",
            {
                "project_key": "SAFE",
                "summary": "s",
                "issue_type": "Task",
                "additional_fields": json.dumps({"project": f" {BLOCKED}"}),
            },
            "blocked",
        ),
    ],
    ids=[
        "batch-project_key",
        "batch-project-field",
        "create-project-dict",
        "create-project-string",
    ],
)
async def test_whitespace_around_project_key_denied(tool, args, match):
    fake, _, error = await _call(_make_config(), tool, args)
    _assert_denied(fake, error, match)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("tool", "args"),
    [
        (
            "create_issue",
            {
                "project_key": "SAFE",
                "summary": "s",
                "issue_type": "Task",
                "additional_fields": json.dumps({"project": "10000"}),
            },
        ),
        ("batch_create_issues", _batch(_item("SAFE", project="10000"))),
        ("batch_create_issues", _batch(_item("10000"))),
    ],
    ids=["create-project-string", "batch-project-field", "batch-project_key"],
)
async def test_non_key_project_value_denied(tool, args):
    fake, _, error = await _call(_make_config(), tool, args)
    _assert_denied(fake, error, "Cannot determine the Jira project")


# ---------------------------------------------------------------------------
# Realistic field map: denied values really reach the write payload
# ---------------------------------------------------------------------------

REALISTIC_FIELDS: list[dict[str, Any]] = [
    {
        "id": "summary",
        "name": "Summary",
        "custom": False,
        "schema": {"type": "string", "system": "summary"},
    },
    {
        "id": "issuetype",
        "name": "Issue Type",
        "custom": False,
        "schema": {"type": "issuetype", "system": "issuetype"},
    },
    {
        "id": "project",
        "name": "Project",
        "custom": False,
        "schema": {"type": "project", "system": "project"},
    },
    {
        "id": "parent",
        "name": "Parent",
        "custom": False,
        "schema": {"type": "issuelink", "system": "parent"},
    },
    {
        "id": "customfield_10014",
        "name": "Epic Link",
        "custom": True,
        "schema": {
            "type": "any",
            "custom": "com.pyxis.greenhopper.jira:gh-epic-link",
            "customId": 10014,
        },
    },
]

_CREATE = {"project_key": "SAFE", "summary": "s", "issue_type": "Task"}

# (tool, args, write method, write path, path into the body, expected value)
PAYLOAD_CASES = [
    (
        "create_issue",
        {**_CREATE, "additional_fields": json.dumps({"project": {"key": BLOCKED}})},
        "POST",
        "/rest/api/2/issue",
        ["fields", "project"],
        {"key": BLOCKED},
    ),
    (
        "create_issue",
        {**_CREATE, "additional_fields": json.dumps({"parent": f"{BLOCKED}-1"})},
        "POST",
        "/rest/api/2/issue",
        ["fields", "parent"],
        {"key": f"{BLOCKED}-1"},
    ),
    (
        "update_issue",
        {
            "issue_key": "SAFE-1",
            "fields": json.dumps({"summary": "x"}),
            "additional_fields": json.dumps({"Epic Link": f"{BLOCKED}-500"}),
        },
        "PUT",
        "/rest/api/2/issue/SAFE-1",
        ["fields", "customfield_10014"],
        f"{BLOCKED}-500",
    ),
    (
        "update_issue",
        {"issue_key": "SAFE-1", "fields": json.dumps({"parent": f"{BLOCKED}-9"})},
        "PUT",
        "/rest/api/2/issue/SAFE-1",
        ["fields", "parent"],
        {"key": f"{BLOCKED}-9"},
    ),
    (
        "batch_create_issues",
        _batch(_item("SAFE", **{"Epic Link": f"{BLOCKED}-500"})),
        "POST",
        "/rest/api/2/issue/bulk",
        ["issueUpdates", 0, "fields", "customfield_10014"],
        f"{BLOCKED}-500",
    ),
    (
        "batch_create_issues",
        _batch(_item("SAFE", project={"key": BLOCKED})),
        "POST",
        "/rest/api/2/issue/bulk",
        ["issueUpdates", 0, "fields", "project"],
        {"key": BLOCKED},
    ),
]


def _dig(body: Any, path: list[Any]) -> Any:
    for part in path:
        body = body[part]
    return body


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("tool", "args", "method", "write_path", "body_path", "expected"),
    PAYLOAD_CASES,
    ids=[
        "create-project",
        "create-parent",
        "update-epic-link-field",
        "update-parent",
        "batch-epic-link-field",
        "batch-project",
    ],
)
async def test_denied_reference_reaches_payload_without_lists(
    tool, args, method, write_path, body_path, expected
):
    """With a realistic field map, the denied value is in the write payload.

    The same call is denied with lists configured, and with no lists it sends
    a write whose payload carries the referenced project or issue.
    """
    fake, _, error = await _call(
        _make_config(), tool, args, field_defs=REALISTIC_FIELDS
    )
    _assert_denied(fake, error, "blocked")

    fake, _, _ = await _call(
        _make_config(None, None), tool, args, field_defs=REALISTIC_FIELDS
    )
    matching = [b for m, p, b in fake.writes if m == method and p == write_path]
    assert matching, f"no {method} {write_path} sent: {fake.calls}"
    assert any(_dig(b, body_path) == expected for b in matching), matching
