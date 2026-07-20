Feature: 2.0 Multi-Instance OAuth Cloud-Site Resolution
    As an engineer with access to multiple Atlassian Cloud sites
    I want the OAuth flow to resolve deterministically to the configured site and reject tokens that don't cover it
    So that I never end up silently connected to the wrong Atlassian instance, and get an immediate, actionable error when I am


# --------------------------------------------------------------------------------------------------
# Cloud ID Resolution
# --------------------------------------------------------------------------------------------------

Scenario: [TOR-02-s6Jze5H] The _get_cloud_id() method shall resolve to the accessible-resource matching a configured ATLASSIAN_OAUTH_CLOUD_ID, regardless of the resource's position in the accessible-resources list
    #
    # Note:
    #   1. Root cause: src/mcp_atlassian/utils/oauth.py line 328 currently reads
    #      `self.cloud_id = resources[0]["id"]` unconditionally, ignoring any configured
    #      cloud ID. The fix must search `resources` for an entry matching the configured
    #      id before falling back to index 0.
    #
    Given an OAuth token whose accessible-resources response is [{"id": "partner-site-id"}, {"id": "8b7be5e1-e593-4e28-b67d-2a22bd5a2e6a"}]
    And ATLASSIAN_OAUTH_CLOUD_ID is configured as "8b7be5e1-e593-4e28-b67d-2a22bd5a2e6a"
    When _get_cloud_id() resolves the cloud ID for that token
    Then self.cloud_id equals "8b7be5e1-e593-4e28-b67d-2a22bd5a2e6a"
    And self.cloud_id does not equal "partner-site-id"

Scenario: [TOR-02-IUNtYgO] The _get_cloud_id() method shall fall back to the first accessible resource when no ATLASSIAN_OAUTH_CLOUD_ID is configured, preserving existing single-instance behavior
    Given an OAuth token whose accessible-resources response contains two or more sites
    And no ATLASSIAN_OAUTH_CLOUD_ID is configured
    When _get_cloud_id() resolves the cloud ID for that token
    Then self.cloud_id equals the id of the first entry in the accessible-resources response

Scenario: [TOR-02-ePsqZQq] The _get_cloud_id() method shall resolve to the sole accessible resource when the token has exactly one, regardless of whether a cloud ID is configured
    Given an OAuth token whose accessible-resources response contains exactly one site
    When _get_cloud_id() resolves the cloud ID for that token
    Then self.cloud_id equals that single site's id


# --------------------------------------------------------------------------------------------------
# Post-Auth Site Validation
# --------------------------------------------------------------------------------------------------

Scenario: [TOR-02-CE3OroW] The OAuth flow shall reject, immediately after authentication, a token whose accessible resources do not include the configured cloud site, raising an error that names both the token's actual accessible site(s) and the required site
    #
    # Note:
    #   1. No such validation exists today anywhere in utils/oauth.py between token exchange
    #      and caching — this is new behavior, not a fix to an existing check.
    #
    Given a completed OAuth authentication flow whose resulting token's accessible-resources response is [{"id": "partner-site-id", "name": "Partner Co"}]
    And ATLASSIAN_OAUTH_CLOUD_ID is configured as "8b7be5e1-e593-4e28-b67d-2a22bd5a2e6a"
    When the server performs post-auth validation of that token
    Then the server rejects the token
    And the resulting error names "Partner Co" (or "partner-site-id") as the token's actual site
    And the resulting error names "8b7be5e1-e593-4e28-b67d-2a22bd5a2e6a" as the required site

Scenario: [TOR-02-MLk6Fcn] The OAuth flow shall accept and proceed to cache a token whose accessible resources include the configured cloud site
    Given a completed OAuth authentication flow whose resulting token's accessible-resources response includes an entry matching the configured ATLASSIAN_OAUTH_CLOUD_ID
    When the server performs post-auth validation of that token
    Then the token is accepted
    And no rejection error is raised

Scenario: [TOR-02-6kYAHsQ] The OAuth flow shall not cache a token that fails post-auth site validation
    Given a completed OAuth authentication flow whose resulting token fails post-auth site validation per TOR-02-CE3OroW
    When the rejection occurs
    Then no token is written to the token cache or persistent store as a result of this authentication attempt
