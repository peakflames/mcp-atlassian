import logging
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
                check_confluence_space_access,
                check_jira_project_access,
                extract_jira_project_key,
            )

            # --- Jira project checks ---
            jira_config = app_lifespan_ctx.full_jira_config
            if jira_config is not None and (
                jira_config.projects_blocked_set or jira_config.projects_readonly_set
            ):
                project_keys_to_check: list[str] = []

                direct_project_key = kwargs.get("project_key")
                if direct_project_key:
                    project_keys_to_check.append(str(direct_project_key))

                for kw in ("issue_key", "inward_issue_key", "outward_issue_key"):
                    ik = kwargs.get(kw)
                    if ik:
                        project_keys_to_check.append(extract_jira_project_key(str(ik)))

                for pk in project_keys_to_check:
                    try:
                        check_jira_project_access(jira_config, pk, write=True)
                    except ProjectAccessError as exc:
                        raise ValueError(str(exc)) from exc

                # batch_create_issues / batch_create_versions embed project_key in JSON
                issues_data = kwargs.get("issues_data")
                if issues_data:
                    import json as _json  # noqa: PLC0415

                    # Parse JSON separately so access-control ValueErrors are not swallowed
                    try:
                        issues_list = (
                            _json.loads(issues_data)
                            if isinstance(issues_data, str)
                            else issues_data
                        )
                    except (TypeError, _json.JSONDecodeError):
                        issues_list = None  # Malformed JSON; let the tool surface it

                    if isinstance(issues_list, list):
                        for item in issues_list:
                            if isinstance(item, dict):
                                batch_pk = item.get("project_key")
                                if batch_pk:
                                    try:
                                        check_jira_project_access(
                                            jira_config, str(batch_pk), write=True
                                        )
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
                    try:
                        from mcp_atlassian.servers.dependencies import (  # noqa: PLC0415
                            get_confluence_fetcher,
                        )

                        conf_fetcher = await get_confluence_fetcher(ctx)
                        resolved_space = conf_fetcher.get_page_space_key(str(page_id))
                        if resolved_space:
                            try:
                                check_confluence_space_access(
                                    conf_config, resolved_space, write=True
                                )
                            except ProjectAccessError as exc:
                                raise ValueError(str(exc)) from exc
                    except ValueError:
                        raise
                    except Exception as e:
                        logger.warning(
                            f"Could not resolve space for page '{page_id}' "
                            f"during write-access check: {e}"
                        )

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
