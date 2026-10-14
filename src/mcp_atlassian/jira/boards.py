"""Module for Jira boards operations."""

import logging
from typing import Any

import requests

from ..models.jira import JiraBoard
from .client import JiraClient

logger = logging.getLogger("mcp-jira")


class BoardsMixin(JiraClient):
    """Mixin for Jira boards operations."""

    def get_all_agile_boards(
        self,
        board_name: str | None = None,
        project_key: str | None = None,
        board_type: str | None = None,
        start: int = 0,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """
        Get boards from Jira by name, project key, or type.

        Args:
            board_name: The name of board, support fuzzy search
            project_key: Project key (e.g., PROJECT-123)
            board_type: Board type (e.g., scrum, kanban)
            start: Start index
            limit: Maximum number of boards to return

        Returns:
            List of board information

        Raises:
            Exception: If there is an error retrieving the boards
        """
        try:
            boards = self.jira.get_all_agile_boards(
                board_name=board_name,
                project_key=project_key,
                board_type=board_type,
                start=start,
                limit=limit,
            )
            return boards.get("values", []) if isinstance(boards, dict) else []
        except requests.HTTPError as e:
            # Re-raise so auth failures (e.g. missing Jira Software OAuth scope)
            # reach the caller instead of looking like an empty board list.
            logger.error(f"Error getting all agile boards: {str(e.response.content)}")
            raise
        except Exception as e:
            logger.error(f"Error getting all agile boards: {str(e)}")
            return []

    def get_all_agile_boards_model(
        self,
        board_name: str | None = None,
        project_key: str | None = None,
        board_type: str | None = None,
        start: int = 0,
        limit: int = 50,
    ) -> list[JiraBoard]:
        """
        Get boards as JiraBoards model from Jira by name, project key, or type.

        Args:
            board_name: The name of board, support fuzzy search
            project_key: Project key (e.g., PROJECT-123)
            board_type: Board type (e.g., scrum, kanban)
            start: Start index
            limit: Maximum number of boards to return

        Returns:
            List of JiraBoards model with board information

        Raises:
            Exception: If there is an error retrieving the boards
        """
        boards = self.get_all_agile_boards(
            board_name=board_name,
            project_key=project_key,
            board_type=board_type,
            start=start,
            limit=limit,
        )
        return [JiraBoard.from_api_response(board) for board in boards]

    def move_issues_to_board(
        self, board_id: str, issue_keys: list[str]
    ) -> list[dict[str, Any]]:
        """Move issues from a board's backlog onto the board.

        On a Kanban board with the backlog enabled (including team-managed
        Kanban) this transitions the issues to the board's first column.

        Args:
            board_id: The board ID
            issue_keys: Issue keys to move, at most 50

        Returns:
            Per-issue failures reported by Jira (``issueKey``, ``status``,
            ``errors``); empty when every issue moved

        Raises:
            ValueError: If no keys or more than 50 keys are given
            requests.HTTPError: If the request fails
        """
        return self._post_board_issue_move(
            f"rest/agile/1.0/board/{board_id}/issue", issue_keys
        )

    def move_issues_to_backlog(
        self, board_id: str, issue_keys: list[str]
    ) -> list[dict[str, Any]]:
        """Move issues from a board back to that board's backlog.

        On a board with sprints this removes the issues from their active and
        future sprints.

        Args:
            board_id: The board ID
            issue_keys: Issue keys to move, at most 50

        Returns:
            Per-issue failures reported by Jira (``issueKey``, ``status``,
            ``errors``); empty when every issue moved

        Raises:
            ValueError: If no keys or more than 50 keys are given
            requests.HTTPError: If the request fails
        """
        return self._post_board_issue_move(
            f"rest/agile/1.0/backlog/{board_id}/issue", issue_keys
        )

    def _post_board_issue_move(
        self, path: str, issue_keys: list[str]
    ) -> list[dict[str, Any]]:
        if not issue_keys:
            raise ValueError("At least one issue key is required")
        if len(issue_keys) > 50:
            raise ValueError("At most 50 issues can be moved at once")
        # 204 when every issue moved; 207 lists each issue's outcome.
        result = self.jira.post(path, data={"issues": issue_keys})
        entries = result.get("entries", []) if isinstance(result, dict) else []
        return [
            {
                "issueKey": entry.get("issueKey") or entry.get("issueId"),
                "status": entry.get("status"),
                "errors": entry.get("errors", []),
            }
            for entry in entries
            if isinstance(entry, dict)
            and (int(entry.get("status") or 0) >= 400 or entry.get("errors"))
        ]
