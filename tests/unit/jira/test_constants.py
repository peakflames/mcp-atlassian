"""Tests for Jira constants.

Focused tests for Jira constants, validating correct values and business logic.
"""

from mcp_atlassian.jira.constants import DEFAULT_READ_JIRA_FIELDS


class TestDefaultReadJiraFields:
    """Test suite for DEFAULT_READ_JIRA_FIELDS constant."""

    def test_type_and_structure(self):
        """Test that DEFAULT_READ_JIRA_FIELDS is a set of strings."""
        assert isinstance(DEFAULT_READ_JIRA_FIELDS, set)
        assert all(isinstance(field, str) for field in DEFAULT_READ_JIRA_FIELDS)
        assert len(DEFAULT_READ_JIRA_FIELDS) == 18

    def test_contains_expected_jira_fields(self):
        """Test that DEFAULT_READ_JIRA_FIELDS contains all expected Jira fields."""
        expected_fields = {
            "summary",
            "description",
            "status",
            "assignee",
            "reporter",
            "labels",
            "priority",
            "created",
            "updated",
            "issuetype",
            "issuelinks",
            "subtasks",
            "parent",
            "components",
            "fixVersions",
            "attachment",
            "resolution",
            "resolutiondate",
        }
        assert DEFAULT_READ_JIRA_FIELDS == expected_fields

    def test_essential_fields_present(self):
        """Test that essential Jira fields are included."""
        essential_fields = {"summary", "status", "issuetype"}
        assert essential_fields.issubset(DEFAULT_READ_JIRA_FIELDS)

    def test_field_format_validity(self):
        """Test that field names are valid for API usage (no spaces, no surrounding underscores)."""
        for field in DEFAULT_READ_JIRA_FIELDS:
            assert field
            assert " " not in field
            assert not field.startswith("_")
            assert not field.endswith("_")

    def test_default_read_jira_fields_includes_relationship_and_resolution_fields(self):
        """Expanded default set covers relationship and resolution fields (TOR-01-el7Cazx)."""
        relationship_fields = {"issuelinks", "subtasks", "parent", "components"}
        resolution_fields = {
            "fixVersions",
            "attachment",
            "resolution",
            "resolutiondate",
        }
        assert relationship_fields.issubset(DEFAULT_READ_JIRA_FIELDS)
        assert resolution_fields.issubset(DEFAULT_READ_JIRA_FIELDS)
        # Original 10 fields are still present
        original_fields = {
            "summary",
            "description",
            "status",
            "assignee",
            "reporter",
            "labels",
            "priority",
            "created",
            "updated",
            "issuetype",
        }
        assert original_fields.issubset(DEFAULT_READ_JIRA_FIELDS)
