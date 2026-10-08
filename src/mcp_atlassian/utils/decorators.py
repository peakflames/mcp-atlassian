import json
import logging
import re
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any, TypeVar

import requests
from fastmcp import Context
from fastmcp.exceptions import ToolError
from requests.exceptions import HTTPError

from mcp_atlassian.exceptions import MCPAtlassianAuthenticationError

logger = logging.getLogger(__name__)


F = TypeVar("F", bound=Callable[..., Awaitable[Any]])

# Tool keyword arguments read by the Jira per-project write guard in
# ``check_write_access``. Tests compare these against the parameters of every
# Jira write tool so a renamed or newly added key-bearing parameter is caught.
JIRA_GUARD_PROJECT_KEY_KWARGS: tuple[str, ...] = ("project_key",)
JIRA_GUARD_ISSUE_KEY_KWARGS: tuple[str, ...] = (
    "issue_key",
    "inward_issue_key",
    "outward_issue_key",
    "epic_key",
)
JIRA_GUARD_ISSUE_KEY_LIST_KWARGS: tuple[str, ...] = ("issue_keys",)
JIRA_GUARD_FIELDS_KWARGS: tuple[str, ...] = ("fields", "additional_fields")
JIRA_GUARD_BATCH_ISSUES_KWARGS: tuple[str, ...] = ("issues",)

_JIRA_PROJECT_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_JIRA_ISSUE_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*-\d+$")
# Field names the Jira fetcher accepts as an epic link (see jira/issues.py).
_JIRA_EPIC_LINK_ALIASES = frozenset({"epickey", "epic_link", "epiclink", "epic link"})


def _unresolved_jira_reference(value: Any, source: str) -> ValueError:
    return ValueError(
        f"Cannot determine the Jira project for {value!r} in '{source}'. "
        "Per-project access control is configured (JIRA_PROJECTS_BLOCKED / "
        "JIRA_PROJECTS_READONLY), so references must use project keys "
        "(e.g. 'PROJ') or issue keys (e.g. 'PROJ-123')."
    )


def _jira_project_key(value: Any, source: str) -> str:
    """Return a stripped project key, or raise if the value is not a key."""
    text = str(value).strip()
    if not _JIRA_PROJECT_KEY_RE.match(text):
        raise _unresolved_jira_reference(value, source)
    return text


def _jira_project_from_issue_ref(ref: Any, source: str) -> str:
    """Return the project key of an issue key, or raise if it is not a key."""
    text = str(ref).strip()
    if not _JIRA_ISSUE_KEY_RE.match(text):
        raise _unresolved_jira_reference(ref, source)
    return text.split("-", 1)[0].upper()


def _parse_json_arg(value: Any) -> Any:
    """Parse a JSON string argument; return None if it is malformed."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None  # Malformed JSON; let the tool surface it
    return value


def _jira_projects_from_fields(fields: dict[str, Any], source: str) -> list[str]:
    """Collect project keys referenced by a Jira fields dictionary.

    Covers a ``project`` override, a ``parent`` issue and epic link aliases.
    A non-empty reference whose project cannot be determined raises
    ``ValueError`` because the write would otherwise proceed against an
    unidentified project.
    """
    keys: list[str] = []
    for name, value in fields.items():
        if value is None or value == "":
            continue
        lname = name.lower()
        if lname not in ("project", "parent") and lname not in _JIRA_EPIC_LINK_ALIASES:
            continue
        where = f"{source}.{name}"
        ref = value.get("key") if isinstance(value, dict) else value
        if not isinstance(ref, str):
            raise _unresolved_jira_reference(value, where)
        if lname == "project":
            keys.append(_jira_project_key(ref, where))
        else:
            keys.append(_jira_project_from_issue_ref(ref, where))
    return keys


def handle_tool_errors(func: F) -> F:
    """
    Decorator for FastMCP tool handlers that catches exceptions and re-raises
    them as ToolError with the original error message preserved.

    ToolError bypasses FastMCP's mask_error_details setting, ensuring that
    descriptive error messages are always sent to MCP clients regardless of
    server configuration.

    Assumes the decorated function is async.
    """
    tool_name = func.__name__

    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await func(*args, **kwargs)
        except ToolError:
            raise
        except Exception as e:
            logger.error(f"Error in tool '{tool_name}': {e}", exc_info=True)
            raise ToolError(str(e)) from e

    return wrapper  # type: ignore


def check_write_access(func: F) -> F:
    """
    Decorator for FastMCP tools to check if the application is in read-only mode
    and to enforce per-project/space BLOCKED and READONLY access controls.

    Raises a ToolError when:
    - The server is in global read-only mode.
    - The target Jira project is in JIRA_PROJECTS_BLOCKED or JIRA_PROJECTS_READONLY.
      Every project a call touches is checked: project keys, issue keys
      (both link ends, the epic, comma-separated lists), each batch item, and
      ``project`` / ``parent`` / epic-link entries in JSON field arguments.
      While either list is set, a reference whose project cannot be
      determined (e.g. a numeric issue ID) is rejected.
    - The target Confluence space is in CONFLUENCE_SPACES_BLOCKED or
      CONFLUENCE_SPACES_READONLY.

    Assumes the decorated function is async and has ``ctx: Context`` as its first
    argument.
    """
    tool_name = func.__name__

    @wraps(func)
    @handle_tool_errors
    async def wrapper(ctx: Context, *args: Any, **kwargs: Any) -> Any:
        lifespan_ctx_dict = ctx.request_context.lifespan_context
        app_lifespan_ctx = (
            lifespan_ctx_dict.get("app_lifespan_context")
            if isinstance(lifespan_ctx_dict, dict)
            else None
        )  # type: ignore

        if app_lifespan_ctx is not None and app_lifespan_ctx.read_only:
            action_description = tool_name.replace(
                "_", " "
            )  # e.g., "create_issue" -> "create issue"
            logger.warning(f"Attempted to call tool '{tool_name}' in read-only mode.")
            raise ValueError(f"Cannot {action_description} in read-only mode.")

        # Per-project / per-space write access control
        if app_lifespan_ctx is not None:
            # Late import avoids circular dependency at module load time
            from mcp_atlassian.utils.access_control import (  # noqa: PLC0415
                ProjectAccessError,
                check_confluence_content_space_access,
                check_confluence_space_access,
                check_jira_project_access,
            )

            # --- Jira project checks ---
            jira_config = app_lifespan_ctx.full_jira_config
            if jira_config is not None and (
                jira_config.projects_blocked_set or jira_config.projects_readonly_set
            ):
                project_keys_to_check: list[str] = []

                for kw in JIRA_GUARD_PROJECT_KEY_KWARGS:
                    direct_project_key = kwargs.get(kw)
                    if direct_project_key:
                        project_keys_to_check.append(
                            _jira_project_key(direct_project_key, kw)
                        )

                # Single issue keys (incl. both ends of a link and the epic)
                for kw in JIRA_GUARD_ISSUE_KEY_KWARGS:
                    ik = kwargs.get(kw)
                    if ik:
                        project_keys_to_check.append(
                            _jira_project_from_issue_ref(ik, kw)
                        )

                # Comma-separated (or list) issue keys, e.g. add_issues_to_sprint
                for kw in JIRA_GUARD_ISSUE_KEY_LIST_KWARGS:
                    raw_keys = kwargs.get(kw)
                    if not raw_keys:
                        continue
                    key_items = (
                        raw_keys
                        if isinstance(raw_keys, list | tuple)
                        else str(raw_keys).split(",")
                    )
                    for ik in key_items:
                        if str(ik).strip():
                            project_keys_to_check.append(
                                _jira_project_from_issue_ref(ik, kw)
                            )

                # JSON field dictionaries: project override, parent, epic link
                for kw in JIRA_GUARD_FIELDS_KWARGS:
                    fields_dict = _parse_json_arg(kwargs.get(kw))
                    if isinstance(fields_dict, dict):
                        project_keys_to_check.extend(
                            _jira_projects_from_fields(fields_dict, kw)
                        )

                # batch_create_issues: every item names its own project.
                # Items without a project_key are left for the tool to reject;
                # it does not create an issue for them.
                for kw in JIRA_GUARD_BATCH_ISSUES_KWARGS:
                    issues_list = _parse_json_arg(kwargs.get(kw))
                    if not isinstance(issues_list, list):
                        continue
                    for idx, item in enumerate(issues_list):
                        if not isinstance(item, dict):
                            continue
                        batch_pk = item.get("project_key")
                        if batch_pk:
                            project_keys_to_check.append(
                                _jira_project_key(batch_pk, f"{kw}[{idx}].project_key")
                            )
                        project_keys_to_check.extend(
                            _jira_projects_from_fields(item, f"{kw}[{idx}]")
                        )

                for pk in project_keys_to_check:
                    try:
                        check_jira_project_access(jira_config, pk, write=True)
                    except ProjectAccessError as exc:
                        raise ValueError(str(exc)) from exc

            # --- Confluence space checks ---
            conf_config = app_lifespan_ctx.full_confluence_config
            if conf_config is not None and (
                conf_config.spaces_blocked_set or conf_config.spaces_readonly_set
            ):
                space_key = kwargs.get("space_key") or kwargs.get("target_space_key")
                if space_key:
                    try:
                        check_confluence_space_access(
                            conf_config, str(space_key), write=True
                        )
                    except ProjectAccessError as exc:
                        raise ValueError(str(exc)) from exc

                page_id = kwargs.get("page_id")
                if page_id:
                    from mcp_atlassian.servers.dependencies import (  # noqa: PLC0415
                        get_confluence_fetcher,
                    )

                    # Fails closed: denied when the page's space cannot be
                    # determined while a space list applies. The space is
                    # checked against both the server's lists and the
                    # per-request fetcher's, so a per-request config that
                    # lacks the lists cannot weaken the check.
                    conf_fetcher = await get_confluence_fetcher(ctx)
                    content_id = str(page_id)
                    page_space = conf_fetcher.resolve_content_space_key(content_id)
                    try:
                        for config in (conf_config, conf_fetcher.config):
                            check_confluence_content_space_access(
                                config, page_space, content_id=content_id, write=True
                            )
                    except ProjectAccessError as exc:
                        raise ValueError(str(exc)) from exc

        return await func(ctx, *args, **kwargs)

    return wrapper  # type: ignore


def handle_auth_errors(
    service_name: str = "Atlassian API",
) -> Callable:
    """Decorator to handle 401/403 HTTPError as auth errors.

    Only catches HTTPError with 401/403 status codes and raises
    MCPAtlassianAuthenticationError. All other exceptions pass
    through unmodified.

    Args:
        service_name: Name of the service for error messages.
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            try:
                return func(self, *args, **kwargs)
            except HTTPError as http_err:
                if http_err.response is not None and http_err.response.status_code in [
                    401,
                    403,
                ]:
                    error_msg = (
                        f"Authentication failed for "
                        f"{service_name} "
                        f"({http_err.response.status_code}). "
                        "Token may be expired or invalid. "
                        "Please verify credentials."
                    )
                    logger.error(error_msg)
                    raise MCPAtlassianAuthenticationError(error_msg) from http_err
                raise  # re-raise non-auth HTTPError

        return wrapper

    return decorator


def handle_atlassian_api_errors(service_name: str = "Atlassian API") -> Callable:
    """
    Decorator to handle common Atlassian API exceptions (Jira, Confluence, etc.).

    Args:
        service_name: Name of the service for error logging (e.g., "Jira API").
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            try:
                return func(self, *args, **kwargs)
            except HTTPError as http_err:
                if http_err.response is not None and http_err.response.status_code in [
                    401,
                    403,
                ]:
                    error_msg = (
                        f"Authentication failed for {service_name} "
                        f"({http_err.response.status_code}). "
                        "Token may be expired or invalid. Please verify credentials."
                    )
                    logger.error(error_msg)
                    raise MCPAtlassianAuthenticationError(error_msg) from http_err
                else:
                    operation_name = getattr(func, "__name__", "API operation")
                    logger.error(
                        f"HTTP error during {operation_name}: {http_err}",
                        exc_info=False,
                    )
                    raise http_err
            except KeyError as e:
                operation_name = getattr(func, "__name__", "API operation")
                logger.error(f"Missing key in {operation_name} results: {str(e)}")
                return []
            except requests.RequestException as e:
                operation_name = getattr(func, "__name__", "API operation")
                logger.error(f"Network error during {operation_name}: {str(e)}")
                return []
            except (ValueError, TypeError) as e:
                operation_name = getattr(func, "__name__", "API operation")
                logger.error(f"Error processing {operation_name} results: {str(e)}")
                return []
            except Exception as e:  # noqa: BLE001 - Intentional fallback with logging
                operation_name = getattr(func, "__name__", "API operation")
                logger.error(f"Unexpected error during {operation_name}: {str(e)}")
                logger.debug(
                    f"Full exception details for {operation_name}:", exc_info=True
                )
                return []

        return wrapper

    return decorator
