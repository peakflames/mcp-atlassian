"""Base client module for Confluence API interactions."""

import logging
import os
from typing import Any

from atlassian import Confluence
from requests import Session
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import HTTPError

from ..exceptions import MCPAtlassianAuthenticationError
from ..utils.access_control import (
    ProjectAccessError,
    check_confluence_content_space_access,
    check_confluence_space_access,
)
from ..utils.logging import get_masked_session_headers, log_config_param, mask_sensitive
from ..utils.oauth import configure_oauth_session
from ..utils.ssl import configure_ssl_verification
from .config import ConfluenceConfig
from .v2_adapter import ConfluenceV2Adapter

# Configure logging
logger = logging.getLogger("mcp-atlassian")


class ConfluenceClient:
    """Base client for Confluence API interactions."""

    def __init__(self, config: ConfluenceConfig | None = None) -> None:
        """Initialize the Confluence client with given or environment config.

        Args:
            config: Configuration for Confluence client. If None, will load from
                environment.

        Raises:
            ValueError: If configuration is invalid or environment variables are missing
            MCPAtlassianAuthenticationError: If OAuth authentication fails
        """
        self.config = config or ConfluenceConfig.from_env()

        # Initialize the Confluence client based on auth type
        if self.config.auth_type == "oauth":
            if not self.config.oauth_config:
                error_msg = "OAuth authentication requires oauth_config"
                raise ValueError(error_msg)

            # Determine Cloud vs Data Center OAuth
            is_dc_oauth = (
                getattr(self.config.oauth_config, "is_data_center", False) is True
            )

            if not is_dc_oauth and not self.config.oauth_config.cloud_id:
                error_msg = "Cloud OAuth authentication requires a valid cloud_id"
                raise ValueError(error_msg)

            # Create a session for OAuth
            session = Session()

            # Configure the session with OAuth authentication
            if not configure_oauth_session(session, self.config.oauth_config):
                error_msg = "Failed to configure OAuth session"
                raise MCPAtlassianAuthenticationError(error_msg)

            if is_dc_oauth:
                # Data Center: use the instance URL directly
                api_url = self.config.url
                is_cloud = False
            else:
                # Cloud: use the Atlassian Cloud API URL
                api_url = f"https://api.atlassian.com/ex/confluence/{self.config.oauth_config.cloud_id}"
                is_cloud = True

            # Initialize Confluence with the session
            self.confluence = Confluence(
                url=api_url,
                session=session,
                cloud=is_cloud,
                verify_ssl=self.config.ssl_verify,
                timeout=self.config.timeout,
            )
        elif self.config.auth_type == "pat":
            logger.debug(
                f"Initializing Confluence client with Token (PAT) auth. "
                f"URL: {self.config.url}, "
                f"Token (masked): {mask_sensitive(str(self.config.personal_token))}"
            )
            self.confluence = Confluence(
                url=self.config.url,
                token=self.config.personal_token,
                cloud=self.config.is_cloud,
                verify_ssl=self.config.ssl_verify,
                timeout=self.config.timeout,
            )
        else:  # basic auth
            logger.debug(
                f"Initializing Confluence client with Basic auth. "
                f"URL: {self.config.url}, Username: {self.config.username}, "
                f"API Token present: {bool(self.config.api_token)}, "
                f"Is Cloud: {self.config.is_cloud}"
            )
            self.confluence = Confluence(
                url=self.config.url,
                username=self.config.username,
                password=self.config.api_token,  # API token is used as password
                cloud=self.config.is_cloud,
                verify_ssl=self.config.ssl_verify,
                timeout=self.config.timeout,
            )
            logger.debug(
                f"Confluence client initialized. "
                f"Session headers (Authorization masked): "
                f"{get_masked_session_headers(dict(self.confluence._session.headers))}"
            )

        # Disable trust_env for PAT and OAuth to prevent .netrc from overriding
        # explicit credentials (#860). Basic auth can safely use .netrc.
        if self.config.auth_type in ("pat", "oauth"):
            self.confluence._session.trust_env = False

        # Configure SSL verification using the shared utility
        configure_ssl_verification(
            service_name="Confluence",
            url=self.config.url,
            session=self.confluence._session,
            ssl_verify=self.config.ssl_verify,
            client_cert=self.config.client_cert,
            client_key=self.config.client_key,
            client_key_password=self.config.client_key_password,
        )

        # Proxy configuration
        proxies = {}
        if self.config.http_proxy:
            proxies["http"] = self.config.http_proxy
        if self.config.https_proxy:
            proxies["https"] = self.config.https_proxy
        if self.config.socks_proxy:
            proxies["socks"] = self.config.socks_proxy
        if proxies:
            self.confluence._session.proxies.update(proxies)
            for k, v in proxies.items():
                log_config_param(
                    logger, "Confluence", f"{k.upper()}_PROXY", v, sensitive=True
                )
        if self.config.no_proxy and isinstance(self.config.no_proxy, str):
            os.environ["NO_PROXY"] = self.config.no_proxy
            log_config_param(logger, "Confluence", "NO_PROXY", self.config.no_proxy)

        # Apply custom headers if configured
        if self.config.custom_headers:
            self._apply_custom_headers()

        # Import here to avoid circular imports
        from ..preprocessing.confluence import ConfluencePreprocessor

        self.preprocessor = ConfluencePreprocessor(base_url=self.config.url)

        # Test authentication during initialization (in debug mode only)
        if logger.isEnabledFor(logging.DEBUG):
            try:
                self._validate_authentication()
            except MCPAtlassianAuthenticationError:
                logger.warning(
                    "Authentication validation failed during client initialization - "
                    "continuing anyway"
                )

    def _uses_oauth_gateway(self) -> bool:
        """Whether requests go through the Atlassian API gateway (Cloud OAuth)."""
        return self.config.auth_type == "oauth" and self.config.is_cloud

    @property
    def _v2_adapter(self) -> ConfluenceV2Adapter | None:
        """Get v2 API adapter for OAuth authentication.

        Returns:
            ConfluenceV2Adapter instance if OAuth is configured, None otherwise
        """
        if self._uses_oauth_gateway():
            return ConfluenceV2Adapter(
                session=self.confluence._session, base_url=self.confluence.url
            )
        return None

    def _attachment_base_url(self) -> str:
        """Return the base URL for resolving relative attachment download links.

        Cloud OAuth tokens are only accepted by the Atlassian API gateway stored
        on the underlying client, not by the site URL in ``config.url``. The
        gateway serves Confluence paths under the ``/wiki`` prefix.

        The relative ``/download/attachments/...`` link is kept as-is rather
        than rewritten to the v1 ``/rest/api/content/{id}/child/attachment/
        {att}/download`` endpoint, because the gateway is removing v1 content
        endpoints (410 Gone).

        Returns:
            Base URL to prepend to relative ``_links.download`` values.
        """
        if self._uses_oauth_gateway():
            base_url = self.confluence.url.rstrip("/")
            if not base_url.endswith("/wiki"):
                base_url = f"{base_url}/wiki"
            return base_url
        return self.config.url

    @staticmethod
    def _is_not_found(error: BaseException) -> bool:
        """Whether ``error``, or an exception it wraps, is an HTTP 404."""
        seen: set[int] = set()
        current: BaseException | None = error
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            if isinstance(current, HTTPError) and current.response is not None:
                return current.response.status_code == 404
            current = current.__cause__ or current.__context__
        return False

    def resolve_content_space_key(
        self, content_id: str, *, is_comment: bool = False
    ) -> str | None:
        """Resolve the space key of a page, blog post, attachment, or comment.

        Used only by access-control checks. Returns None when the key cannot
        be determined.

        Args:
            content_id: The content ID to resolve
            is_comment: Whether ``content_id`` is a comment. The v2 API serves
                comments from their own endpoints; v1 treats them as content.

        Returns:
            The space key, or None if it cannot be determined
        """
        adapter = self._v2_adapter
        if adapter is not None:
            if is_comment:
                return adapter.get_comment_space_key(content_id)
            return adapter.get_content_space_key(content_id)
        try:
            content = self.confluence.get_page_by_id(page_id=content_id, expand="space")
        except Exception as e:  # noqa: BLE001 - any failure means "unknown"
            logger.warning(f"Could not resolve the space of '{content_id}': {e}")
            return None
        space = content.get("space") if isinstance(content, dict) else None
        key = space.get("key") if isinstance(space, dict) else None
        return str(key) if key else None

    def check_content_access(
        self, content_id: str, *, write: bool = False, is_comment: bool = False
    ) -> None:
        """Enforce per-space access control for a content ID.

        Reads are checked against ``CONFLUENCE_SPACES_BLOCKED``; writes are
        also checked against ``CONFLUENCE_SPACES_READONLY``. Does nothing, and
        makes no request, when no relevant list is configured. If the space
        cannot be determined, access is denied whenever a relevant list is
        configured.

        Args:
            content_id: A page, blog post, attachment, or comment ID
            write: ``True`` for mutation operations
            is_comment: Whether ``content_id`` is a comment

        Raises:
            ProjectAccessError: If access is denied.
        """
        if not (
            self.config.spaces_blocked_set
            or (write and self.config.spaces_readonly_set)
        ):
            return
        space_key = self.resolve_content_space_key(content_id, is_comment=is_comment)
        check_confluence_content_space_access(
            self.config, space_key, content_id=content_id, write=write
        )

    def _embed_space_allowed(
        self, adapter: ConfluenceV2Adapter, embed: dict[str, Any]
    ) -> bool:
        """Apply ``CONFLUENCE_SPACES_BLOCKED`` to an embed.

        Fails closed: when a block list is configured and the embed's space
        cannot be resolved to a key, the embed is treated as blocked.
        """
        if not self.config.spaces_blocked_set:
            return True
        space_id = embed.get("spaceId")
        space_key = adapter.get_space_key(str(space_id)) if space_id else None
        if not space_key:
            logger.warning(
                f"Could not resolve the space of embed '{embed.get('id')}'; "
                "withholding it because CONFLUENCE_SPACES_BLOCKED is set"
            )
            return False
        try:
            check_confluence_space_access(self.config, space_key, write=False)
        except ProjectAccessError:
            return False
        return True

    def get_embed_info(
        self, content_id: str, error: BaseException
    ) -> dict[str, Any] | None:
        """Describe a Smart Link embed, if ``content_id`` is one.

        Embeds return 404 on the page and attachment endpoints, so the lookup
        only runs for Cloud OAuth (v2 API) when ``error`` is a 404. Embeds in
        a space listed in ``CONFLUENCE_SPACES_BLOCKED`` are not described.

        Args:
            content_id: The content ID that failed to resolve as a page
            error: The exception raised by the original lookup

        Returns:
            Error payload describing the embed and its external URL, or None
            if the ID is not an embed, cannot be looked up, or is blocked.
        """
        if not content_id or content_id.startswith("att"):
            return None
        adapter = self._v2_adapter
        if adapter is None or not self._is_not_found(error):
            return None

        embed = adapter.get_embed(content_id)
        if not embed or not self._embed_space_allowed(adapter, embed):
            return None

        return {
            "success": False,
            "content_id": content_id,
            "content_type": "embed",
            "title": embed.get("title"),
            "embed_url": embed.get("embedUrl"),
            "parent_id": embed.get("parentId"),
            "parent_type": embed.get("parentType"),
            "error": (
                f"Content '{content_id}' is a Smart Link embed, not a page. It has "
                "no page body or attachments; the linked resource lives at "
                "embed_url outside Confluence and cannot be fetched with "
                "Confluence credentials."
            ),
        }

    def _validate_authentication(self) -> None:
        """Validate authentication by making a simple API call."""
        try:
            logger.debug(
                "Testing Confluence authentication by making a simple API call..."
            )
            # Make a simple API call to test authentication
            spaces = self.confluence.get_all_spaces(start=0, limit=1)
            if spaces is not None:
                logger.info(
                    f"Confluence authentication successful. "
                    f"API call returned {len(spaces.get('results', []))} spaces."
                )
            else:
                logger.warning(
                    "Confluence authentication test returned None - "
                    "this may indicate an issue"
                )
        except RequestsConnectionError as e:
            error_msg = (
                f"Could not connect to Confluence at {self.config.url}. "
                "Check that CONFLUENCE_URL is correct and the instance is reachable."
            )
            logger.error(error_msg)
            raise MCPAtlassianAuthenticationError(error_msg) from e
        except Exception as e:
            error_msg = f"Confluence authentication validation failed: {e}"
            logger.error(error_msg)
            logger.debug(
                f"Authentication headers during failure: "
                f"{get_masked_session_headers(dict(self.confluence._session.headers))}"
            )
            raise MCPAtlassianAuthenticationError(error_msg) from e

    def _apply_custom_headers(self) -> None:
        """Apply custom headers to the Confluence session."""
        if not self.config.custom_headers:
            return

        logger.debug(
            f"Applying {len(self.config.custom_headers)} custom headers to Confluence session"
        )
        for header_name, header_value in self.config.custom_headers.items():
            self.confluence._session.headers[header_name] = header_value
            logger.debug(f"Applied custom header: {header_name}")

    def _process_html_content(
        self, html_content: str, space_key: str
    ) -> tuple[str, str]:
        """Process HTML content into both HTML and markdown formats.

        Args:
            html_content: Raw HTML content from Confluence
            space_key: The key of the space containing the content

        Returns:
            Tuple of (processed_html, processed_markdown)
        """
        return self.preprocessor.process_html_content(
            html_content, space_key, self.confluence
        )
