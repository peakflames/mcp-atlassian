"""Confluence REST API v2 adapter for OAuth authentication.

This module provides direct v2 API calls to handle the deprecated v1 endpoints
when using OAuth authentication. The v1 endpoints have been removed for OAuth
but still work for API token authentication.
"""

import logging
import urllib.parse
from typing import Any

import requests
from requests.exceptions import HTTPError

from .utils import emoji_to_hex_id, extract_emoji_from_property

logger = logging.getLogger("mcp-atlassian")

# Largest ``limit`` the v2 list endpoints accept.
V2_MAX_PAGE_SIZE = 250


class ChildListTruncatedError(ValueError):
    """Raised when a child listing hits the request cap before it completes."""


class ConfluenceV2Adapter:
    """Adapter for Confluence REST API v2 operations when using OAuth."""

    def __init__(self, session: requests.Session, base_url: str) -> None:
        """Initialize the v2 adapter.

        Args:
            session: Authenticated requests session (OAuth configured)
            base_url: Base URL for the Confluence instance
        """
        self.session = session
        self.base_url = base_url

    def _get_space_id(self, space_key: str) -> str:
        """Get space ID from space key using v2 API.

        Args:
            space_key: The space key to look up

        Returns:
            The space ID

        Raises:
            ValueError: If space not found or API error
        """
        try:
            # Use v2 spaces endpoint to get space ID
            url = f"{self.base_url}/api/v2/spaces"
            params = {"keys": space_key}

            response = self.session.get(url, params=params)
            response.raise_for_status()

            data = response.json()
            results = data.get("results", [])

            if not results:
                raise ValueError(f"Space with key '{space_key}' not found")

            space_id = results[0].get("id")
            if not space_id:
                raise ValueError(f"No ID found for space '{space_key}'")

            return space_id

        except Exception as e:
            if isinstance(e, HTTPError) and e.response is not None:
                logger.error(
                    f"HTTP error getting space ID for '{space_key}': {e}\n"
                    f"Response: {e.response.text}"
                )
            else:
                logger.error(f"Error getting space ID for '{space_key}': {e}")
            raise ValueError(f"Failed to get space ID for '{space_key}': {e}") from e

    def create_page(
        self,
        space_key: str,
        title: str,
        body: str,
        parent_id: str | None = None,
        representation: str = "storage",
        status: str = "current",
    ) -> dict[str, Any]:
        """Create a page using the v2 API.

        Args:
            space_key: The key of the space to create the page in
            title: The title of the page
            body: The content body in the specified representation
            parent_id: Optional parent page ID
            representation: Content representation format (default: "storage")
            status: Page status (default: "current")

        Returns:
            The created page data from the API response

        Raises:
            ValueError: If page creation fails
        """
        try:
            # Get space ID from space key
            space_id = self._get_space_id(space_key)

            # Prepare request data for v2 API
            data = {
                "spaceId": space_id,
                "status": status,
                "title": title,
                "body": {
                    "representation": representation,
                    "value": body,
                },
            }

            # Add parent if specified
            if parent_id:
                data["parentId"] = parent_id

            # Make the v2 API call
            url = f"{self.base_url}/api/v2/pages"
            response = self.session.post(url, json=data)
            response.raise_for_status()

            result = response.json()
            logger.debug(f"Successfully created page '{title}' with v2 API")

            # Convert v2 response to v1-compatible format for consistency
            return self._convert_v2_to_v1_format(result, space_key)

        except Exception as e:
            if isinstance(e, HTTPError) and e.response is not None:
                logger.error(
                    f"HTTP error creating page '{title}': {e}\n"
                    f"Response: {e.response.text}"
                )
            else:
                logger.error(f"Error creating page '{title}': {e}")
            raise ValueError(f"Failed to create page '{title}': {e}") from e

    def _get_page_version(self, page_id: str) -> int:
        """Get the current version number of a page.

        Args:
            page_id: The ID of the page

        Returns:
            The current version number

        Raises:
            ValueError: If page not found or API error
        """
        try:
            url = f"{self.base_url}/api/v2/pages/{page_id}"
            params = {"body-format": "storage"}

            response = self.session.get(url, params=params)
            response.raise_for_status()

            data = response.json()
            version_number = data.get("version", {}).get("number")

            if version_number is None:
                raise ValueError(f"No version number found for page '{page_id}'")

            return version_number

        except Exception as e:
            if isinstance(e, HTTPError) and e.response is not None:
                logger.error(
                    f"HTTP error getting page version for '{page_id}': {e}\n"
                    f"Response: {e.response.text}"
                )
            else:
                logger.error(f"Error getting page version for '{page_id}': {e}")
            raise ValueError(f"Failed to get page version for '{page_id}': {e}") from e

    def update_page(
        self,
        page_id: str,
        title: str,
        body: str,
        representation: str = "storage",
        version_comment: str = "",
        status: str = "current",
    ) -> dict[str, Any]:
        """Update a page using the v2 API.

        Args:
            page_id: The ID of the page to update
            title: The new title of the page
            body: The new content body in the specified representation
            representation: Content representation format (default: "storage")
            version_comment: Optional comment for this version
            status: Page status (default: "current")

        Returns:
            The updated page data from the API response

        Raises:
            ValueError: If page update fails
        """
        try:
            # Get current version and increment it
            current_version = self._get_page_version(page_id)
            new_version = current_version + 1

            # Prepare request data for v2 API
            data = {
                "id": page_id,
                "status": status,
                "title": title,
                "body": {
                    "representation": representation,
                    "value": body,
                },
                "version": {
                    "number": new_version,
                },
            }

            # Add version comment if provided
            if version_comment:
                data["version"]["message"] = version_comment

            # Make the v2 API call
            url = f"{self.base_url}/api/v2/pages/{page_id}"
            response = self.session.put(url, json=data)
            response.raise_for_status()

            result = response.json()
            logger.debug(f"Successfully updated page '{title}' with v2 API")

            # Convert v2 response to v1-compatible format for consistency
            # For update, we need to extract space key from the result
            space_id = result.get("spaceId")
            space_key = self._get_space_key_from_id(space_id) if space_id else "unknown"

            return self._convert_v2_to_v1_format(result, space_key)

        except Exception as e:
            if isinstance(e, HTTPError) and e.response is not None:
                logger.error(
                    f"HTTP error updating page '{page_id}': {e}\n"
                    f"Response: {e.response.text}"
                )
            else:
                logger.error(f"Error updating page '{page_id}': {e}")
            raise ValueError(f"Failed to update page '{page_id}': {e}") from e

    def _get_space_key_from_id(self, space_id: str) -> str:
        """Get space key from space ID using v2 API.

        Args:
            space_id: The space ID to look up

        Returns:
            The space key, or the space ID itself if the key cannot be read
        """
        return self.get_space_key(space_id) or space_id

    def get_page(
        self,
        page_id: str,
        expand: str | None = None,
    ) -> dict[str, Any]:
        """Get a page using the v2 API.

        Args:
            page_id: The ID of the page to retrieve
            expand: Fields to expand in the response (not used in v2 API, for compatibility only)

        Returns:
            The page data from the API response in v1-compatible format

        Raises:
            ValueError: If page retrieval fails
        """
        try:
            # Make the v2 API call to get the page
            url = f"{self.base_url}/api/v2/pages/{page_id}"

            # Convert v1 expand parameters to v2 format
            params = {"body-format": "storage"}

            response = self.session.get(url, params=params)
            response.raise_for_status()

            v2_response = response.json()
            logger.debug(f"Successfully retrieved page '{page_id}' with v2 API")

            # Get space key from space ID
            space_id = v2_response.get("spaceId")
            space_key = self._get_space_key_from_id(space_id) if space_id else "unknown"

            # Convert v2 response to v1-compatible format
            v1_compatible = self._convert_v2_to_v1_format(v2_response, space_key)

            # Add body.storage structure if body content exists
            if "body" in v2_response and v2_response["body"].get("storage"):
                storage_value = v2_response["body"]["storage"].get("value", "")
                v1_compatible["body"] = {
                    "storage": {"value": storage_value, "representation": "storage"}
                }

            # Add space information with more details
            if space_id:
                v1_compatible["space"] = {
                    "key": space_key,
                    "id": space_id,
                }

            # Add version information
            if "version" in v2_response:
                v1_compatible["version"] = {
                    "number": v2_response["version"].get("number", 1)
                }

            return v1_compatible

        except Exception as e:
            if isinstance(e, HTTPError) and e.response is not None:
                logger.error(
                    f"HTTP error getting page '{page_id}': {e}\n"
                    f"Response: {e.response.text}"
                )
            else:
                logger.error(f"Error getting page '{page_id}': {e}")
            raise ValueError(f"Failed to get page '{page_id}': {e}") from e

    def delete_page(self, page_id: str) -> bool:
        """Delete a page using the v2 API.

        Args:
            page_id: The ID of the page to delete

        Returns:
            True if the page was successfully deleted, False otherwise

        Raises:
            ValueError: If page deletion fails
        """
        try:
            # Make the v2 API call to delete the page
            url = f"{self.base_url}/api/v2/pages/{page_id}"
            response = self.session.delete(url)
            response.raise_for_status()

            logger.debug(f"Successfully deleted page '{page_id}' with v2 API")

            # Check if status code indicates success (204 No Content is typical for deletes)
            if response.status_code in [200, 204]:
                return True

            # If we get here, it's an unexpected success status
            logger.warning(
                f"Delete page returned unexpected status {response.status_code}"
            )
            return True

        except Exception as e:
            if isinstance(e, HTTPError) and e.response is not None:
                logger.error(
                    f"HTTP error deleting page '{page_id}': {e}\n"
                    f"Response: {e.response.text}"
                )
            else:
                logger.error(f"Error deleting page '{page_id}': {e}")
            raise ValueError(f"Failed to delete page '{page_id}': {e}") from e

    def _convert_v2_to_v1_format(
        self, v2_response: dict[str, Any], space_key: str
    ) -> dict[str, Any]:
        """Convert v2 API response to v1-compatible format.

        This ensures compatibility with existing code that expects v1 response format.

        Args:
            v2_response: The response from v2 API
            space_key: The space key (needed since v2 response uses space ID)

        Returns:
            Response formatted like v1 API for compatibility
        """
        # Map v2 response fields to v1 format
        v1_compatible = {
            "id": v2_response.get("id"),
            "type": "page",
            "status": v2_response.get("status"),
            "title": v2_response.get("title"),
            "space": {
                "key": space_key,
                "id": v2_response.get("spaceId"),
            },
            "version": {
                "number": v2_response.get("version", {}).get("number", 1),
            },
            "_links": v2_response.get("_links", {}),
        }

        # Add body if present in v2 response
        if "body" in v2_response:
            v1_compatible["body"] = {
                "storage": {
                    "value": v2_response["body"].get("storage", {}).get("value", ""),
                    "representation": "storage",
                }
            }

        return v1_compatible

    def create_footer_comment(
        self,
        *,
        page_id: str | None = None,
        parent_comment_id: str | None = None,
        body: str,
        representation: str = "storage",
    ) -> dict[str, Any]:
        """Create a footer comment using the v2 API.

        Either page_id (for top-level comments) or parent_comment_id (for replies)
        must be provided, but not both.

        Args:
            page_id: The page ID for top-level comments
            parent_comment_id: The parent comment ID for replies
            body: The comment content
            representation: Content representation format (default: "storage")

        Returns:
            The created comment data in v1-compatible format

        Raises:
            ValueError: If both or neither params provided, or if creation fails
        """
        if page_id and parent_comment_id:
            raise ValueError("page_id and parent_comment_id are mutually exclusive")
        if not page_id and not parent_comment_id:
            raise ValueError("Either page_id or parent_comment_id must be provided")

        try:
            data: dict[str, Any] = {
                "body": {
                    "representation": representation,
                    "value": body,
                },
            }

            if page_id:
                data["pageId"] = page_id
            else:
                data["parentCommentId"] = parent_comment_id

            url = f"{self.base_url}/api/v2/footer-comments"
            response = self.session.post(url, json=data)
            response.raise_for_status()

            result = response.json()
            logger.debug("Successfully created footer comment with v2 API")

            return self._convert_v2_comment_to_v1_format(result)

        except Exception as e:
            if isinstance(e, ValueError | HTTPError):
                raise
            logger.error(f"Error creating footer comment: {e}")
            raise ValueError(f"Failed to create footer comment: {e}") from e

    def _convert_v2_comment_to_v1_format(
        self, v2_response: dict[str, Any]
    ) -> dict[str, Any]:
        """Convert v2 comment response to v1-compatible format.

        Maps body.storage.value → body.view.value since
        ConfluenceComment.from_api_response reads from body.view.value.

        Args:
            v2_response: The response from v2 API

        Returns:
            Response formatted like v1 API for compatibility
        """
        body_value = v2_response.get("body", {}).get("storage", {}).get("value", "")

        v1_compatible: dict[str, Any] = {
            "id": v2_response.get("id"),
            "type": "comment",
            "status": v2_response.get("status"),
            "title": v2_response.get("title"),
            "body": {
                "view": {
                    "value": body_value,
                    "representation": "view",
                },
            },
            "version": v2_response.get("version", {}),
            "_links": v2_response.get("_links", {}),
        }

        # Map v2 author to v1 format
        if author := v2_response.get("author"):
            v1_compatible["author"] = author

        # Map parentCommentId for model compatibility
        if parent_id := v2_response.get("parentCommentId"):
            v1_compatible["parentCommentId"] = parent_id

        # v2 footer-comments endpoint always returns footer comments
        v1_compatible["extensions"] = {"location": "footer"}

        return v1_compatible

    def move_page(
        self,
        page_id: str,
        position: str = "append",
        target_id: str | None = None,
    ) -> None:
        """Move a page using the v1 REST API.

        Uses PUT /wiki/rest/api/content/{id}/move/{position}/{targetId}
        which works with OAuth authentication (unlike movepage.action).

        Args:
            page_id: The ID of the page to move.
            position: Position relative to target. Valid values:
                - "append": Move as child of target (default).
                - "before": Move before target as sibling.
                - "after": Move after target as sibling.
            target_id: Target page ID. Required for "append", "before",
                and "after" positions.

        Raises:
            ValueError: If move fails.
            HTTPError: If the API request fails (propagates 401/403).
        """
        try:
            if target_id:
                url = (
                    f"{self.base_url}/rest/api/content/{page_id}"
                    f"/move/{position}/{target_id}"
                )
            else:
                # Move to space root (no target ID)
                url = f"{self.base_url}/rest/api/content/{page_id}/move/{position}"

            response = self.session.put(url)
            response.raise_for_status()

            logger.debug(
                f"Successfully moved page '{page_id}' "
                f"(position={position}, target={target_id})"
            )

        except HTTPError as e:
            if e.response is not None and e.response.status_code in [401, 403]:
                raise
            logger.error(f"HTTP error moving page '{page_id}': {e}")
            if e.response is not None:
                logger.error(f"Response: {e.response.text}")
            raise ValueError(f"Failed to move page '{page_id}': {e}") from e
        except Exception as e:
            logger.error(f"Error moving page '{page_id}': {e}")
            raise ValueError(f"Failed to move page '{page_id}': {e}") from e

    def get_page_emoji(self, page_id: str) -> str | None:
        """Get the page title emoji from content properties using v2 API.

        The page emoji (icon shown in navigation) is stored as a content property
        with key 'emoji-title-published' or 'emoji-title-draft'.

        Args:
            page_id: The ID of the page

        Returns:
            The emoji character if set, None otherwise
        """
        try:
            # Use v2 content properties API
            url = f"{self.base_url}/api/v2/pages/{page_id}/properties"

            response = self.session.get(url)
            response.raise_for_status()

            data = response.json()
            properties = data.get("results", [])

            # Look for emoji-title-published first, then emoji-title-draft
            for prop in properties:
                key = prop.get("key", "")
                if key in ("emoji-title-published", "emoji-title-draft"):
                    value = prop.get("value", {})
                    return extract_emoji_from_property(value)

            return None

        except Exception as e:
            if isinstance(e, HTTPError) and e.response is not None:
                logger.debug(
                    f"HTTP error getting emoji for page '{page_id}': {e}\n"
                    f"Response: {e.response.text}"
                )
            else:
                logger.debug(f"Error getting emoji for page '{page_id}': {e}")
            return None

    def _set_page_property(
        self, page_id: str, property_key: str, value: str | None
    ) -> bool:
        """Set or remove a single page property.

        Args:
            page_id: The ID of the page
            property_key: The property key to set
            value: The value to set, or None to delete the property

        Returns:
            True if the operation succeeded, False otherwise
        """
        try:
            if value is None:
                # Delete the property
                url = (
                    f"{self.base_url}/api/v2/pages/{page_id}/properties/{property_key}"
                )
                response = self.session.delete(url)
                # 204 No Content or 404 Not Found are both success cases
                return response.status_code in [200, 204, 404]

            # Check if the property already exists
            existing_property = self._get_property(page_id, property_key)

            if existing_property:
                # Update existing property
                url = (
                    f"{self.base_url}/api/v2/pages/{page_id}/properties/{property_key}"
                )
                current_version = existing_property.get("version", {}).get("number", 1)
                data = {
                    "key": property_key,
                    "value": value,
                    "version": {"number": current_version + 1},
                }
                response = self.session.put(url, json=data)
            else:
                # Create new property
                url = f"{self.base_url}/api/v2/pages/{page_id}/properties"
                data = {
                    "key": property_key,
                    "value": value,
                }
                response = self.session.post(url, json=data)

            response.raise_for_status()
            return True

        except Exception as e:
            if isinstance(e, HTTPError) and e.response is not None:
                logger.debug(
                    f"HTTP error setting property '{property_key}' for page "
                    f"'{page_id}': {e}\nResponse: {e.response.text}"
                )
            else:
                logger.debug(
                    f"Error setting property '{property_key}' for page '{page_id}': {e}"
                )
            return False

    def set_page_emoji(self, page_id: str, emoji: str | None) -> bool:
        """Set or remove the page title emoji using v2 API.

        The page emoji (icon shown in navigation) is stored as content properties.
        Both 'emoji-title-published' and 'emoji-title-draft' are set to ensure
        the emoji appears in both view and edit modes.

        Args:
            page_id: The ID of the page
            emoji: The emoji character to set, or None to remove the emoji

        Returns:
            True if the operation succeeded, False otherwise
        """
        try:
            # Convert emoji to hex code, or None to delete
            emoji_value = emoji_to_hex_id(emoji) if emoji else None

            # Set both published and draft properties
            published_ok = self._set_page_property(
                page_id, "emoji-title-published", emoji_value
            )
            draft_ok = self._set_page_property(
                page_id, "emoji-title-draft", emoji_value
            )

            if not published_ok:
                logger.warning(
                    f"Failed to set emoji-title-published for page '{page_id}'"
                )
            if not draft_ok:
                logger.warning(f"Failed to set emoji-title-draft for page '{page_id}'")

            return published_ok and draft_ok

        except Exception as e:
            logger.warning(f"Error setting emoji for page '{page_id}': {e}")
            return False

    def _get_property(self, page_id: str, property_key: str) -> dict[str, Any] | None:
        """Get a specific content property by key.

        Args:
            page_id: The ID of the page
            property_key: The property key to retrieve

        Returns:
            The property data if found, None otherwise
        """
        try:
            url = f"{self.base_url}/api/v2/pages/{page_id}/properties/{property_key}"
            response = self.session.get(url)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()
        except Exception:
            return None

    def get_page_versions_list(self, page_id: str) -> list[dict[str, Any]]:
        """Get list of all versions for a page using v2 API.

        Args:
            page_id: The ID of page

        Returns:
            List of version objects with their IDs and numbers

        Raises:
            ValueError: If page retrieval fails
        """
        try:
            # Use to versions API endpoint to list all versions
            url = f"{self.base_url}/api/v2/pages/{page_id}/versions"

            response = self.session.get(url)
            response.raise_for_status()

            data = response.json()
            versions = data.get("results", [])
            logger.debug(f"Retrieved {len(versions)} versions for page '{page_id}'")

            return versions

        except Exception as e:
            if isinstance(e, HTTPError) and e.response is not None:
                logger.error(
                    f"HTTP error getting versions list for page '{page_id}': {e}\n"
                    f"Response: {e.response.text}"
                )
            else:
                logger.error(f"Error getting versions list for page '{page_id}': {e}")
            raise ValueError(
                f"Failed to get versions list for page '{page_id}': {e}"
            ) from e

    def get_page_by_version(
        self,
        page_id: str,
        version: int,
        expand: str | None = None,
    ) -> dict[str, Any]:
        """Get a specific version of a page using the versions API.

        Note: The v2 API uses version IDs, not version numbers. We need to:
        1. List all versions to find the version ID for the given version number
        2. Fetch the specific version using its version ID

        Args:
            page_id: The ID of page
            version: The version number to retrieve
            expand: Fields to expand in the response

        Returns:
            The page data for the specified version in v1-compatible format

        Raises:
            ValueError: If page retrieval fails or version not found
        """
        try:
            # Step 1: Get all versions to find the version ID
            versions_list = self.get_page_versions_list(page_id)

            # Find the version with the matching version number
            version_id = None
            for ver in versions_list:
                if ver.get("number") == version:
                    version_id = ver.get("id")
                    break

            if not version_id:
                raise ValueError(f"Version {version} not found for page '{page_id}'")

            # Step 2: Fetch the specific version using its version ID
            url = f"{self.base_url}/api/v2/versions/{version_id}"

            # Convert v1 expand parameters to v2 format
            params = {"body-format": "storage"}

            response = self.session.get(url, params=params)
            response.raise_for_status()

            v2_response = response.json()
            logger.debug(f"Successfully retrieved page '{page_id}' version {version}")

            # Get space key from space ID if present
            space_id = v2_response.get("spaceId")
            space_key = self._get_space_key_from_id(space_id) if space_id else "unknown"

            # Convert v2 response to v1-compatible format
            v1_compatible = self._convert_v2_to_v1_format(v2_response, space_key)

            # Add body.storage structure if body content exists
            if "body" in v2_response and v2_response["body"].get("storage"):
                storage_value = v2_response["body"]["storage"].get("value", "")
                v1_compatible["body"] = {
                    "storage": {"value": storage_value, "representation": "storage"}
                }

            # Add version information from version response
            # In versions API, version info is at the top level
            if "number" in v2_response:
                v1_compatible["version"] = {
                    "number": v2_response.get("number"),
                }
            elif "version" in v2_response and "number" in v2_response["version"]:
                v1_compatible["version"] = {
                    "number": v2_response["version"].get("number"),
                }

            # Add space information
            if space_id:
                v1_compatible["space"] = {
                    "key": space_key,
                    "id": space_id,
                }

            # Add children.attachment for compatibility with v1 expand
            if "children" in v2_response and "attachment" in v2_response["children"]:
                v1_compatible.setdefault("children", {})["attachment"] = v2_response[
                    "children"
                ]["attachment"]

            return v1_compatible

        except Exception as e:
            if isinstance(e, HTTPError) and e.response is not None:
                logger.error(
                    f"HTTP error getting page '{page_id}' version {version}: {e}\n"
                    f"Response: {e.response.text}"
                )
            else:
                logger.error(f"Error getting page '{page_id}' version {version}: {e}")
            raise ValueError(
                f"Failed to get page '{page_id}' version {version}: {e}"
            ) from e

    def get_page_views(self, page_id: str) -> dict[str, Any]:
        """Get view statistics for a page using the Analytics API.

        Note: This API is only available for Confluence Cloud.

        Args:
            page_id: The ID of the page

        Returns:
            Dictionary containing view statistics:
            - count: Total view count
            - lastSeen: Last viewed timestamp (if available)

        Raises:
            HTTPError: If the API request fails (propagates 401/403)
            ValueError: If page not found or other errors
        """
        try:
            # Use the Analytics API endpoint
            url = f"{self.base_url}/rest/api/analytics/content/{page_id}/views"

            response = self.session.get(url)
            response.raise_for_status()

            data = response.json()
            logger.debug(f"Successfully retrieved view stats for page '{page_id}'")

            return data

        except HTTPError as e:
            # Propagate auth errors (401, 403)
            if e.response is not None and e.response.status_code in [401, 403]:
                logger.error(
                    f"Authentication error getting views for page '{page_id}': {e}"
                )
                raise
            logger.warning(f"HTTP error getting views for page '{page_id}': {e}")
            raise ValueError(
                f"Failed to get view statistics for page '{page_id}': {e}"
            ) from e
        except Exception as e:
            logger.error(f"Error getting views for page '{page_id}': {e}")
            raise ValueError(
                f"Failed to get view statistics for page '{page_id}': {e}"
            ) from e

    def get_page_attachments(
        self,
        page_id: str,
        start: int = 0,
        limit: int = 50,
        filename: str | None = None,
        media_type: str | None = None,
        sort: str | None = None,
    ) -> dict[str, Any]:
        """Get attachments for a page using v2 API.

        Args:
            page_id: The page ID
            start: Starting index for pagination (default: 0)
            limit: Maximum number of results (default: 50, max: 250)
            filename: Filter by filename
            media_type: Filter by media type (e.g., "image/png")
            sort: Sort field (e.g., "created-date", "-created-date")

        Returns:
            Dictionary containing:
            - results: List of attachment objects
            - _links: Pagination links

        Raises:
            HTTPError: If the API request fails (propagates 401/403)
            ValueError: If page not found or other errors
        """
        try:
            url = f"{self.base_url}/api/v2/pages/{page_id}/attachments"
            params: dict[str, Any] = {"start": start, "limit": limit}

            if filename:
                params["filename"] = filename
            if media_type:
                params["media-type"] = media_type
            if sort:
                params["sort"] = sort

            response = self.session.get(url, params=params)
            response.raise_for_status()

            data = response.json()
            logger.debug(
                f"Successfully retrieved attachments for page '{page_id}' "
                f"(found {len(data.get('results', []))})"
            )

            # Convert v2 format to v1-compatible format for consistency
            return self._convert_attachments_v2_to_v1(data)

        except HTTPError as e:
            if e.response is not None and e.response.status_code in [401, 403]:
                logger.error(
                    f"Authentication error getting attachments for page '{page_id}': {e}"
                )
                raise
            logger.warning(f"HTTP error getting attachments for page '{page_id}': {e}")
            raise ValueError(
                f"Failed to get attachments for page '{page_id}': {e}"
            ) from e
        except Exception as e:
            logger.error(f"Error getting attachments for page '{page_id}': {e}")
            raise ValueError(
                f"Failed to get attachments for page '{page_id}': {e}"
            ) from e

    def get_attachment_by_id(self, attachment_id: str) -> dict[str, Any]:
        """Get a single attachment by ID using v2 API.

        Args:
            attachment_id: The attachment ID

        Returns:
            Attachment object in v1-compatible format

        Raises:
            HTTPError: If the API request fails (propagates 401/403)
            ValueError: If attachment not found or other errors
        """
        try:
            url = f"{self.base_url}/api/v2/attachments/{attachment_id}"

            response = self.session.get(url)
            response.raise_for_status()

            data = response.json()
            logger.debug(f"Successfully retrieved attachment '{attachment_id}'")

            # Convert v2 format to v1-compatible format
            return self._convert_single_attachment_v2_to_v1(data)

        except HTTPError as e:
            if e.response is not None and e.response.status_code in [401, 403]:
                logger.error(
                    f"Authentication error getting attachment '{attachment_id}': {e}"
                )
                raise
            if e.response is not None and e.response.status_code == 404:
                raise ValueError(f"Attachment '{attachment_id}' not found") from e
            logger.warning(f"HTTP error getting attachment '{attachment_id}': {e}")
            raise ValueError(f"Failed to get attachment '{attachment_id}': {e}") from e
        except Exception as e:
            logger.error(f"Error getting attachment '{attachment_id}': {e}")
            raise ValueError(f"Failed to get attachment '{attachment_id}': {e}") from e

    def delete_attachment(self, attachment_id: str) -> None:
        """Delete an attachment by ID using v2 API.

        Args:
            attachment_id: The attachment ID to delete

        Raises:
            HTTPError: If the API request fails (propagates 401/403)
            ValueError: If attachment not found or deletion fails
        """
        try:
            url = f"{self.base_url}/api/v2/attachments/{attachment_id}"

            response = self.session.delete(url)
            response.raise_for_status()

            logger.info(f"Successfully deleted attachment '{attachment_id}'")

        except HTTPError as e:
            if e.response is not None and e.response.status_code in [401, 403]:
                logger.error(
                    f"Authentication error deleting attachment '{attachment_id}': {e}"
                )
                raise
            if e.response is not None and e.response.status_code == 404:
                raise ValueError(f"Attachment '{attachment_id}' not found") from e
            logger.warning(f"HTTP error deleting attachment '{attachment_id}': {e}")
            raise ValueError(
                f"Failed to delete attachment '{attachment_id}': {e}"
            ) from e
        except Exception as e:
            logger.error(f"Error deleting attachment '{attachment_id}': {e}")
            raise ValueError(
                f"Failed to delete attachment '{attachment_id}': {e}"
            ) from e

    def _convert_attachments_v2_to_v1(
        self, v2_response: dict[str, Any]
    ) -> dict[str, Any]:
        """Convert v2 attachments list response to v1-compatible format.

        Args:
            v2_response: The v2 API response with results array

        Returns:
            Response formatted like v1 API for compatibility
        """
        results = v2_response.get("results", [])
        converted_results = [
            self._convert_single_attachment_v2_to_v1(att) for att in results
        ]

        return {
            "results": converted_results,
            "start": v2_response.get("start", 0),
            "limit": v2_response.get("limit", 50),
            "size": len(converted_results),
            "_links": v2_response.get("_links", {}),
        }

    def _convert_single_attachment_v2_to_v1(
        self, v2_attachment: dict[str, Any]
    ) -> dict[str, Any]:
        """Convert a single v2 attachment to v1-compatible format.

        Args:
            v2_attachment: Single attachment object from v2 API

        Returns:
            Attachment formatted like v1 API for compatibility
        """
        return {
            "id": v2_attachment.get("id"),
            "type": "attachment",
            "status": v2_attachment.get("status", "current"),
            "title": v2_attachment.get("title"),
            "metadata": {
                "mediaType": v2_attachment.get("mediaType"),
                "comment": v2_attachment.get("comment"),
            },
            "extensions": {
                "fileSize": v2_attachment.get("fileSize"),
                "mediaType": v2_attachment.get("mediaType"),
            },
            "version": v2_attachment.get("version", {}),
            "_links": v2_attachment.get("_links", {}),
        }

    def get_embed(self, embed_id: str) -> dict[str, Any] | None:
        """Get a Smart Link embed (content type ``embed``) using v2 API.

        Embeds live in the content tree alongside pages and folders but point
        at an external URL (``embedUrl``). They have no body and no attachments,
        so page and attachment endpoints return 404 for them.

        Args:
            embed_id: The content ID to look up

        Returns:
            The v2 embed object, or None if the ID is not an embed or cannot
            be read (e.g. the token lacks the ``read:embed:confluence`` scope).
        """
        url = f"{self.base_url}/api/v2/embeds/{embed_id}"
        try:
            response = self.session.get(url)
        except requests.RequestException as e:
            logger.debug(f"Embed lookup for '{embed_id}' failed: {e}")
            return None

        if response.status_code == 200:
            try:
                data = response.json()
            except ValueError as e:
                logger.debug(f"Embed lookup for '{embed_id}' returned non-JSON: {e}")
                return None
            return data if isinstance(data, dict) else None
        if response.status_code in (401, 403):
            logger.warning(
                f"Could not read embed '{embed_id}' (HTTP {response.status_code}); "
                "the OAuth app may be missing the read:embed:confluence scope"
            )
        return None

    def get_content_labels(self, content_id: str) -> dict[str, Any]:
        """Get labels for a page, blog post, or attachment using v2 API.

        The v1 ``/rest/api/content/{id}/label`` endpoint is no longer served
        through the OAuth API gateway. Attachment IDs (``att`` prefix) use the
        attachments endpoint; other IDs try pages first, then blog posts.

        Args:
            content_id: The content ID

        Returns:
            v1-compatible dictionary with a ``results`` list of labels

        Raises:
            HTTPError: If the API request fails with 401/403
            ValueError: If the content is not found or other errors
        """
        if content_id.startswith("att"):
            content_types = ["attachments"]
        else:
            content_types = ["pages", "blogposts"]

        for index, content_type in enumerate(content_types):
            url = f"{self.base_url}/api/v2/{content_type}/{content_id}/labels"
            try:
                labels = self._get_all_results(url)
            except HTTPError as e:
                status = e.response.status_code if e.response is not None else None
                if status in (401, 403):
                    logger.error(
                        f"Authentication error getting labels for '{content_id}': {e}"
                    )
                    raise
                if status == 404 and index < len(content_types) - 1:
                    continue
                msg = f"Failed to get labels for content '{content_id}': {e}"
                raise ValueError(msg) from e
            except Exception as e:
                msg = f"Failed to get labels for content '{content_id}': {e}"
                raise ValueError(msg) from e

            return {
                "results": [
                    {
                        "id": label.get("id"),
                        "name": label.get("name"),
                        "prefix": label.get("prefix", "global"),
                        "label": label.get("name"),
                    }
                    for label in labels
                ]
            }

        return {"results": []}

    def _get_all_results(
        self, url: str, limit: int = 250, max_pages: int = 20
    ) -> list[dict[str, Any]]:
        """Collect ``results`` from a cursor-paginated v2 list endpoint.

        Args:
            url: The v2 list endpoint URL
            limit: Page size to request
            max_pages: Safety cap on the number of pages fetched

        Returns:
            Combined list of result objects

        Raises:
            HTTPError: If any request fails
        """
        results: list[dict[str, Any]] = []
        params: dict[str, Any] = {"limit": limit}
        for _ in range(max_pages):
            response = self.session.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            results.extend(data.get("results", []))

            next_link = data.get("_links", {}).get("next")
            if not next_link:
                break
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(next_link).query)
            cursor = query.get("cursor", [None])[0]
            if not cursor:
                break
            params = {"limit": limit, "cursor": cursor}
        else:
            logger.warning(
                f"Stopped after {max_pages} pages ({len(results)} results) from "
                f"{url}; remaining results were not fetched"
            )
        return results

    def get_page_children(
        self,
        page_id: str,
        *,
        start: int = 0,
        limit: int = 25,
        include_folders: bool = True,
        include_version: bool = True,
        include_body: bool = False,
        max_pages: int = 20,
    ) -> list[dict[str, Any]]:
        """Get child pages (and optionally folders) of a page using v2 API.

        Lists ``/api/v2/pages/{id}/direct-children`` and keeps only ``page``
        and, when requested, ``folder`` items, in the order the API returns
        them. The v2 list is cursor-paginated, so ``start`` is applied by
        skipping that many matching items while following ``_links.next``.

        The children endpoint returns only ``id``, ``status``, ``title``,
        ``type``, ``spaceId`` and ``childPosition``. When ``include_version``
        or ``include_body`` is set, child pages are looked up in batches of
        up to 250 via ``/api/v2/pages?id=...`` to add ``version`` and
        ``body.storage``. Folders have no body and the batch lookup does not
        cover them, so folders never carry a version or body on this path.
        A failed space lookup only drops the ``space`` field (logged as a
        warning); it does not fail the listing.

        Args:
            page_id: The ID of the parent page
            start: Number of matching child items to skip
            limit: Maximum number of child items to return
            include_folders: Whether to include child folders
            include_version: Whether to add each child page's version
            include_body: Whether to add each child page's storage body
            max_pages: Safety cap on the number of list pages fetched

        Returns:
            List of child items in v1-compatible format

        Raises:
            HTTPError: If the API request fails with 401/403
            ChildListTruncatedError: If ``max_pages`` requests did not reach
                the end of the list or ``start + limit`` matching items
            ValueError: If the parent is not found or other errors. The
                message never contains the request URL.
        """
        if limit <= 0:
            return []
        wanted_types = {"page", "folder"} if include_folders else {"page"}
        url = f"{self.base_url}/api/v2/pages/{page_id}/direct-children"
        try:
            children: list[dict[str, Any]] = []
            skipped = 0
            scanned = 0
            # Always request the v2 maximum so that skipping ``start`` items
            # and filtering out other content types cannot exhaust
            # ``max_pages`` early.
            params: dict[str, Any] = {"limit": V2_MAX_PAGE_SIZE}
            for _ in range(max_pages):
                response = self.session.get(url, params=params)
                response.raise_for_status()
                data = response.json()
                for item in data.get("results", []):
                    scanned += 1
                    if item.get("type") not in wanted_types:
                        continue
                    if skipped < start:
                        skipped += 1
                        continue
                    children.append(item)
                    if len(children) >= limit:
                        break
                if len(children) >= limit:
                    break

                next_link = data.get("_links", {}).get("next")
                query = urllib.parse.parse_qs(
                    urllib.parse.urlsplit(next_link or "").query
                )
                cursor = query.get("cursor", [None])[0]
                if not cursor:
                    break
                params = {"limit": V2_MAX_PAGE_SIZE, "cursor": cursor}
            else:
                # Returning what was collected would look like a complete
                # (possibly empty) list of children.
                msg = (
                    f"Could not list children of page '{page_id}': stopped after "
                    f"{max_pages} requests ({scanned} child items scanned) before "
                    f"finding {start + limit} matching items"
                )
                raise ChildListTruncatedError(msg)

            details: dict[str, dict[str, Any]] = {}
            page_ids = [str(c["id"]) for c in children if c.get("type") == "page"]
            if page_ids and (include_version or include_body):
                details = self._get_pages_by_id(page_ids, include_body=include_body)
                missing = [pid for pid in page_ids if pid not in details]
                if missing:
                    logger.warning(
                        f"{len(missing)} child page(s) of page '{page_id}' were "
                        "not returned by the page lookup; they are listed "
                        "without version or body"
                    )

            spaces: dict[str, dict[str, Any] | None] = {}
            converted = []
            for child in children:
                child_id = str(child.get("id"))
                detail = details.get(child_id, {})
                space_id = child.get("spaceId") or detail.get("spaceId")
                if space_id and space_id not in spaces:
                    spaces[space_id] = self._get_space_summary(str(space_id))
                converted.append(
                    self._convert_child_v2_to_v1(
                        child,
                        detail,
                        spaces.get(space_id) if space_id else None,
                        include_version=include_version,
                        include_body=include_body,
                    )
                )

            logger.debug(
                f"Retrieved {len(converted)} children of page '{page_id}' with v2 API"
            )
            return converted

        except ChildListTruncatedError:
            logger.error(f"Truncated child listing for page '{page_id}'")
            raise
        except HTTPError as e:
            # Error strings returned to clients must not contain request URLs:
            # under OAuth they include the gateway path with the cloud ID.
            # The full error is logged instead.
            status = e.response.status_code if e.response is not None else None
            if status in (401, 403):
                logger.error(
                    f"Authentication error getting children of page '{page_id}': {e}"
                )
                raise
            logger.warning(f"HTTP error getting children of page '{page_id}': {e}")
            if status == 404:
                msg = f"Page not found or not accessible: {page_id}"
            else:
                msg = f"Failed to get children of page '{page_id}': HTTP {status}"
            raise ValueError(msg) from e
        except Exception as e:
            logger.error(f"Error getting children of page '{page_id}': {e}")
            msg = f"Failed to get children of page '{page_id}': {type(e).__name__}"
            raise ValueError(msg) from e

    def _get_pages_by_id(
        self, page_ids: list[str], *, include_body: bool
    ) -> dict[str, dict[str, Any]]:
        """Look up pages via ``/api/v2/pages?id=...``, 250 IDs per request.

        Args:
            page_ids: Page IDs to look up
            include_body: Whether to request the storage body

        Returns:
            Mapping of page ID to the v2 page object

        Raises:
            HTTPError: If a request fails
        """
        details: dict[str, dict[str, Any]] = {}
        for offset in range(0, len(page_ids), V2_MAX_PAGE_SIZE):
            chunk = page_ids[offset : offset + V2_MAX_PAGE_SIZE]
            params: dict[str, Any] = {"id": chunk, "limit": len(chunk)}
            if include_body:
                params["body-format"] = "storage"
            response = self.session.get(f"{self.base_url}/api/v2/pages", params=params)
            response.raise_for_status()
            for page in response.json().get("results", []):
                details[str(page.get("id"))] = page
        return details

    def _get_space_summary(self, space_id: str) -> dict[str, Any] | None:
        """Get a space's ID, key and name, or None if it cannot be read."""
        url = f"{self.base_url}/api/v2/spaces/{space_id}"
        try:
            response = self.session.get(url)
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as e:
            logger.warning(f"Could not read space '{space_id}': {e}")
            return None
        if not isinstance(data, dict) or not data.get("key"):
            return None
        summary: dict[str, Any] = {"id": space_id, "key": data["key"]}
        if data.get("name"):
            summary["name"] = data["name"]
        return summary

    @staticmethod
    def _convert_child_v2_to_v1(
        child: dict[str, Any],
        detail: dict[str, Any],
        space: dict[str, Any] | None,
        *,
        include_version: bool,
        include_body: bool,
    ) -> dict[str, Any]:
        """Convert a v2 child item (plus optional page detail) to v1 format."""
        result: dict[str, Any] = {
            "id": child.get("id"),
            "type": child.get("type", "page"),
            "status": child.get("status", "current"),
            "title": child.get("title") or detail.get("title"),
            "_links": detail.get("_links", {}),
        }
        if space:
            result["space"] = space
        version = detail.get("version")
        if include_version and isinstance(version, dict):
            v1_version: dict[str, Any] = {"number": version.get("number", 0)}
            if version.get("createdAt"):
                v1_version["when"] = version["createdAt"]
            if version.get("message"):
                v1_version["message"] = version["message"]
            result["version"] = v1_version
        if include_body and "body" in detail:
            result["body"] = {
                "storage": {
                    "value": detail["body"].get("storage", {}).get("value", ""),
                    "representation": "storage",
                }
            }
        return result

    def get_space_key(self, space_id: str) -> str | None:
        """Get a space key from its ID, without falling back to the ID.

        Args:
            space_id: The space ID to look up

        Returns:
            The space key, or None if the space cannot be read
        """
        url = f"{self.base_url}/api/v2/spaces/{space_id}"
        try:
            response = self.session.get(url)
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as e:
            logger.warning(f"Could not read space '{space_id}': {e}")
            return None
        key = data.get("key") if isinstance(data, dict) else None
        return str(key) if key else None
