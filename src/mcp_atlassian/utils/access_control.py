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
        space_key: The space key to check (case-insensitive; surrounding
            whitespace is ignored, as in the configured lists).
        write: ``True`` for mutation operations, ``False`` for read operations.

    Raises:
        ProjectAccessError: If access is denied.
    """
    sk = space_key.strip().upper()

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


def check_confluence_content_space_access(
    config: ConfluenceConfig,
    space_key: str | None,
    *,
    content_id: str,
    write: bool,
) -> None:
    """Check access to content whose space key may not be known.

    Fails closed when ``space_key`` is ``None`` or empty: reads are denied
    when ``config.spaces_blocked_set`` is non-empty, and writes are denied
    when either ``config.spaces_blocked_set`` or ``config.spaces_readonly_set``
    is non-empty. When the space key is known, this behaves like
    :func:`check_confluence_space_access`. When no relevant list is
    configured and the key is unknown, this is a no-op.

    Args:
        config: The :class:`ConfluenceConfig` holding the access-control sets.
        space_key: The resolved space key, or ``None`` if it could not be
            resolved. Never pass a space ID here.
        content_id: The content ID, used in the error message.
        write: ``True`` for mutation operations, ``False`` for read operations.

    Raises:
        ProjectAccessError: If access is denied.
    """
    if not space_key or not space_key.strip():
        if config.spaces_blocked_set:
            msg = (
                f"Could not determine the space of content '{content_id}'; "
                "access is denied because CONFLUENCE_SPACES_BLOCKED is set."
            )
            raise ProjectAccessError(msg)
        if write and config.spaces_readonly_set:
            msg = (
                f"Could not determine the space of content '{content_id}'; "
                "write access is denied because CONFLUENCE_SPACES_READONLY is set."
            )
            raise ProjectAccessError(msg)
        return
    check_confluence_space_access(config, space_key, write=write)
