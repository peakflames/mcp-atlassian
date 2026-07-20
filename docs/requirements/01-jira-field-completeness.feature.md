Feature: 1.0 JIRA Field Completeness
    As a downstream MCP server operator relying on jira_get_issue and jira_search
    I want field responses that match their documented contract — a true '*all' mode that returns only populated fields and a minimal default set aligned with the upstream project
    So that I get correct, token-efficient data without null-field noise or contradictions between documented and actual behavior


# --------------------------------------------------------------------------------------------------
# Full Field Retrieval via '*all' — Sentinel Fix
# --------------------------------------------------------------------------------------------------

Scenario: [TOR-01-BJ2CyHG] The jira_get_issue tool shall return every populated field, including custom fields, when called with fields='*all', rather than substituting the default field set
    #
    # Note:
    #   1. Root cause: jira/issues.py line 104 currently reads
    #      `if fields_param == "*all" or fields_set == DEFAULT_READ_JIRA_FIELDS:` and falls
    #      through to `list(DEFAULT_READ_JIRA_FIELDS)` at line 109 — treating '*all' as
    #      identical to "use defaults". The fix must separate the '*all' branch so it requests
    #      the issue's full field catalog instead.
    #
    Given a JIRA issue with at least one populated custom field outside DEFAULT_READ_JIRA_FIELDS (e.g. customfield_10334)
    When jira_get_issue is called for that issue with fields='*all'
    Then the response includes customfield_10334 and its value
    And the response is not limited to the fields in DEFAULT_READ_JIRA_FIELDS

Scenario: [TOR-01-9TP0naJ] The jira_get_issue tool shall return exactly the fields named in an explicit, non-'*all' fields parameter, unaffected by the '*all'-handling fix
    Given a call to jira_get_issue with an explicit fields parameter of 'summary,status,customfield_10334'
    When jira_get_issue is called for that issue
    Then the response contains exactly the summary, status, and customfield_10334 fields
    And no additional field beyond those named and required issue metadata is present

Scenario: [TOR-01-uzPp7yt] The jira_get_issue tool shall return a well-formed '*all' response, with no error, for an issue with no populated custom fields beyond the standard set
    Given a JIRA issue with no populated custom fields
    When jira_get_issue is called for that issue with fields='*all'
    Then the response is a successful, well-formed result containing the issue's standard fields
    And no exception or error is raised


# --------------------------------------------------------------------------------------------------
# Null-Safe '*all' Output
# --------------------------------------------------------------------------------------------------

Scenario: [TOR-01-twYUvG9] The jira_get_issue tool shall exclude custom fields whose value is null or an empty list from '*all' responses, so that LLM context is not flooded with empty field entries on instances with many defined custom fields
    #
    # Note:
    #   1. Root cause: models/jira/issue.py from_api_response() stores every customfield_*
    #      key returned by the Jira API (including null values) in self.custom_fields.
    #      to_simplified_dict() for *all iterates all of them and emits {"value": None} dicts.
    #      The final `{k: v for k, v in result.items() if v is not None}` filter does not
    #      remove these because the wrapper dict itself is not None.
    #   2. Jira returns all defined field keys (including nulls) when fields=*all is requested.
    #      Instances with 2000+ defined custom fields produce thousands of null entries in the
    #      LLM response context without this filter.
    #
    Given a JIRA issue on an instance with multiple defined custom fields, where some custom fields have null or empty-list values and at least one has a populated (non-null, non-empty) value
    When jira_get_issue is called for that issue with fields='*all'
    Then no customfield_* key appears in the response with a null value
    And no customfield_* key appears in the response with an empty list value
    And custom fields that have a non-null, non-empty value on this issue ARE present in the response


# --------------------------------------------------------------------------------------------------
# Default Field Set Alignment (Upstream Compatibility)
# --------------------------------------------------------------------------------------------------

Scenario: [TOR-01-sa52UmE] DEFAULT_READ_JIRA_FIELDS shall contain exactly the 10 essential fields from the upstream sooperset/mcp-atlassian project, with no additional fields, ensuring default responses remain aligned with the upstream contract
    #
    # Note:
    #   1. An earlier iteration expanded DEFAULT_READ_JIRA_FIELDS to 18 fields (adding
    #      issuelinks, subtasks, parent, components, fixVersions, attachment, resolution,
    #      resolutiondate). After maintainer feedback and upstream reference review, that
    #      approach was reversed. Callers needing relationship or custom field data should use
    #      jira_search_fields to discover field IDs and pass them explicitly via the fields
    #      parameter.
    #   2. This TOR supersedes TOR-01-el7Cazx (removed), TOR-01-7XlRNfG (removed), and
    #      TOR-01-NfGirOm (removed), which asserted the expanded 18-field set.
    #
    Given the DEFAULT_READ_JIRA_FIELDS constant in jira/constants.py
    When the constant is inspected
    Then it contains exactly the fields: summary, description, status, assignee, reporter, labels, priority, created, updated, and issuetype
    And it does not contain any of: issuelinks, subtasks, parent, components, fixVersions, attachment, resolution, or resolutiondate
    And its length is exactly 10
