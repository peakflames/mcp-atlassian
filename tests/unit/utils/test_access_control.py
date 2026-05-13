"""Unit tests for utils/access_control.py."""

import pytest

from mcp_atlassian.confluence.config import ConfluenceConfig
from mcp_atlassian.jira.config import JiraConfig
from mcp_atlassian.utils.access_control import (
    ProjectAccessError,
    check_confluence_space_access,
    check_jira_project_access,
    extract_jira_project_key,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _jira_config(**kwargs: str | None) -> JiraConfig:
    defaults: dict = {
        "url": "https://test.atlassian.net",
        "auth_type": "basic",
        "username": "u",
        "api_token": "t",
    }
    defaults.update(kwargs)
    return JiraConfig(**defaults)


def _conf_config(**kwargs: str | None) -> ConfluenceConfig:
    defaults: dict = {
        "url": "https://test.atlassian.net/wiki",
        "auth_type": "basic",
        "username": "u",
        "api_token": "t",
    }
    defaults.update(kwargs)
    return ConfluenceConfig(**defaults)


# ---------------------------------------------------------------------------
# extract_jira_project_key
# ---------------------------------------------------------------------------


def test_extract_project_key_simple():
    assert extract_jira_project_key("PROJ-123") == "PROJ"


def test_extract_project_key_lowercase_normalised():
    assert extract_jira_project_key("proj-1") == "PROJ"


def test_extract_project_key_multipart():
    # Only the first segment is the key
    assert extract_jira_project_key("AB-CD-99") == "AB"


# ---------------------------------------------------------------------------
# check_jira_project_access – empty config is a no-op
# ---------------------------------------------------------------------------


def test_jira_access_no_config_is_noop():
    config = _jira_config()
    # Should not raise for any project key
    check_jira_project_access(config, "ANY", write=False)
    check_jira_project_access(config, "ANY", write=True)


# ---------------------------------------------------------------------------
# BLOCKED enforcement
# ---------------------------------------------------------------------------


def test_jira_blocked_raises_on_read():
    config = _jira_config(projects_blocked="PRIV,SECRET")
    with pytest.raises(ProjectAccessError, match="blocked"):
        check_jira_project_access(config, "PRIV", write=False)


def test_jira_blocked_raises_on_write():
    config = _jira_config(projects_blocked="PRIV")
    with pytest.raises(ProjectAccessError, match="blocked"):
        check_jira_project_access(config, "PRIV", write=True)


def test_jira_blocked_case_insensitive():
    config = _jira_config(projects_blocked="PRIV")
    with pytest.raises(ProjectAccessError):
        check_jira_project_access(config, "priv", write=False)


def test_jira_blocked_whitespace_stripped():
    config = _jira_config(projects_blocked=" PRIV , SECRET ")
    with pytest.raises(ProjectAccessError):
        check_jira_project_access(config, "SECRET", write=False)


def test_jira_not_blocked_passes():
    config = _jira_config(projects_blocked="PRIV")
    check_jira_project_access(config, "SAFE", write=False)
    check_jira_project_access(config, "SAFE", write=True)


# ---------------------------------------------------------------------------
# READONLY enforcement
# ---------------------------------------------------------------------------


def test_jira_readonly_allows_reads():
    config = _jira_config(projects_readonly="RO")
    check_jira_project_access(config, "RO", write=False)


def test_jira_readonly_blocks_writes():
    config = _jira_config(projects_readonly="RO")
    with pytest.raises(ProjectAccessError, match="read-only"):
        check_jira_project_access(config, "RO", write=True)


def test_jira_readonly_case_insensitive():
    config = _jira_config(projects_readonly="RO")
    with pytest.raises(ProjectAccessError):
        check_jira_project_access(config, "ro", write=True)


# ---------------------------------------------------------------------------
# BLOCKED wins over READONLY
# ---------------------------------------------------------------------------


def test_jira_blocked_wins_over_readonly():
    config = _jira_config(projects_blocked="PRIV", projects_readonly="PRIV")
    with pytest.raises(ProjectAccessError, match="blocked"):
        check_jira_project_access(config, "PRIV", write=False)


# ---------------------------------------------------------------------------
# Confluence: check_confluence_space_access
# ---------------------------------------------------------------------------


def test_confluence_access_no_config_is_noop():
    config = _conf_config()
    check_confluence_space_access(config, "ANY", write=False)
    check_confluence_space_access(config, "ANY", write=True)


def test_confluence_blocked_raises_on_read():
    config = _conf_config(spaces_blocked="LEGAL,HR")
    with pytest.raises(ProjectAccessError, match="blocked"):
        check_confluence_space_access(config, "LEGAL", write=False)


def test_confluence_blocked_raises_on_write():
    config = _conf_config(spaces_blocked="LEGAL")
    with pytest.raises(ProjectAccessError):
        check_confluence_space_access(config, "LEGAL", write=True)


def test_confluence_blocked_case_insensitive():
    config = _conf_config(spaces_blocked="LEGAL")
    with pytest.raises(ProjectAccessError):
        check_confluence_space_access(config, "legal", write=False)


def test_confluence_readonly_allows_reads():
    config = _conf_config(spaces_readonly="LEGACY")
    check_confluence_space_access(config, "LEGACY", write=False)


def test_confluence_readonly_blocks_writes():
    config = _conf_config(spaces_readonly="LEGACY")
    with pytest.raises(ProjectAccessError, match="read-only"):
        check_confluence_space_access(config, "LEGACY", write=True)


def test_confluence_blocked_wins_over_readonly():
    config = _conf_config(spaces_blocked="HR", spaces_readonly="HR")
    with pytest.raises(ProjectAccessError, match="blocked"):
        check_confluence_space_access(config, "HR", write=False)


def test_confluence_not_restricted_passes():
    config = _conf_config(spaces_blocked="LEGAL", spaces_readonly="LEGACY")
    check_confluence_space_access(config, "DEV", write=False)
    check_confluence_space_access(config, "DEV", write=True)
