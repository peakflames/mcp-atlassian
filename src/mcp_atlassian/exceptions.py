class MCPAtlassianAuthenticationError(Exception):
    """Raised when Atlassian API authentication fails (401/403) or an OAuth token does not cover the configured cloud site."""

    pass
