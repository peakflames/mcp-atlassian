"""Access control helpers for per-project and per-space permission enforcement."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mcp_atlassian.confluence.config import ConfluenceConfig
    from mcp_atlassian.jira.config import JiraConfig


class ProjectAccessError(ValueError):
    """Raised when access to a project or space is denied by configuration."""


def extract_jira_project_key(issue_key: str) -> str:
    """Extract the project key from a Jira issue key.

    Args:
        issue_key: A Jira issue key like 'PROJ-123'.

    Returns:
        The project key in uppercase, e.g. 'PROJ'.
    """
    return issue_key.split("-", 1)[0].upper()


def check_jira_project_access(
    config: JiraConfig,
    project_key: str,
    *,
    write: bool,
) -> None:
    """Check whether access to a Jira project is permitted.

    Raises :exc:`ProjectAccessError` if:
    - the project key is in ``config.projects_blocked_set`` (any access), or
    - ``write=True`` and the project key is in ``config.projects_readonly_set``.

    Does nothing when both sets are empty (backward-compatible no-op).

    Args:
        config: The :class:`JiraConfig` holding the access-control sets.
        project_key: The project key to check (case-insensitive).
        write: ``True`` for mutation operations, ``False`` for read operations.

    Raises:
        ProjectAccessError: If access is denied.
    """
    pk = project_key.upper()

    if pk in config.projects_blocked_set:
        raise ProjectAccessError(
            f"Access to project '{project_key}' is blocked by configuration "
            "(JIRA_PROJECTS_BLOCKED)."
        )

    if write and pk in config.projects_readonly_set:
        raise ProjectAccessError(
            f"Project '{project_key}' is read-only by configuration "
            "(JIRA_PROJECTS_READONLY). Write operations are not permitted."
        )


def check_confluence_space_access(
    config: ConfluenceConfig,
    space_key: str,
    *,
    write: bool,
) -> None:
    """Check whether access to a Confluence space is permitted.

    Raises :exc:`ProjectAccessError` if:
    - the space key is in ``config.spaces_blocked_set`` (any access), or
    - ``write=True`` and the space key is in ``config.spaces_readonly_set``.

    Does nothing when both sets are empty (backward-compatible no-op).

    Args:
        config: The :class:`ConfluenceConfig` holding the access-control sets.
        space_key: The space key to check (case-insensitive).
        write: ``True`` for mutation operations, ``False`` for read operations.

    Raises:
        ProjectAccessError: If access is denied.
    """
    sk = space_key.upper()

    if sk in config.spaces_blocked_set:
        raise ProjectAccessError(
            f"Access to space '{space_key}' is blocked by configuration "
            "(CONFLUENCE_SPACES_BLOCKED)."
        )

    if write and sk in config.spaces_readonly_set:
        raise ProjectAccessError(
            f"Space '{space_key}' is read-only by configuration "
            "(CONFLUENCE_SPACES_READONLY). Write operations are not permitted."
        )
