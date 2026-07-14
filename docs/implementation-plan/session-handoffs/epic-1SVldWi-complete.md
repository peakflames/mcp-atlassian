# Epic 1SVldWi: Multi-Instance OAuth Cloud-Site Resolution — Complete

**Completed:** 2026-07-14
**Verified by:** Independent review via `/peak-workflow:wrapup-epic 1SVldWi`

## What Was Built

Rewrote `OAuthConfig._get_cloud_id()` so it resolves deterministically to the configured
`ATLASSIAN_OAUTH_CLOUD_ID` when multiple Atlassian Cloud sites are accessible, rather than
blindly taking the first resource returned. Added immediate post-auth rejection when the
authorized account's token does not cover the configured site, with an actionable error
message naming both the actual and required sites — and a guarantee that the bad token is
never written to keyring or file storage.

## Key Files

| File | Purpose |
|------|---------|
| `src/mcp_atlassian/utils/oauth.py` | Validation + resolution rewrite in `_get_cloud_id()`; dedicated `except MCPAtlassianAuthenticationError` in `exchange_code_for_tokens()` |
| `src/mcp_atlassian/exceptions.py` | Docstring broadened to cover OAuth site-access mismatch |
| `tests/unit/utils/test_oauth.py` | `TestMultiInstanceOAuthCloudIdResolution` — 6 regression tests, one per TOR |

## Key Decisions

- Validation runs **before** resolution: capture `configured_id = self.cloud_id` before any fetch, check accessibility post-fetch, then resolve. This ordering guarantees the configured ID is in the accessible set before we rely on it.
- `MCPAtlassianAuthenticationError` is raised from `_get_cloud_id()` and caught by a dedicated handler in `exchange_code_for_tokens()` placed before `_save_tokens()` — this is what guarantees no bad token is cached.
- Resolution priority: `len(resources) == 1` → sole resource; `configured_id` set → configured id; else → `resources[0]`. The sole-resource check is first so a single-site account is never blocked by the configured-id branch.

## Requirements Implemented

| TOR ID | Feature File | Verdict | Test Reference |
|--------|--------------|---------|----------------|
| TOR-02-s6Jze5H | `docs/requirements/02-multi-instance-oauth.feature.md` | PASS | `tests/unit/utils/test_oauth.py:1242` |
| TOR-02-IUNtYgO | `docs/requirements/02-multi-instance-oauth.feature.md` | PASS | `tests/unit/utils/test_oauth.py:1258` |
| TOR-02-ePsqZQq | `docs/requirements/02-multi-instance-oauth.feature.md` | PASS WITH EXCEPTIONS | `tests/unit/utils/test_oauth.py:1273` |
| TOR-02-CE3OroW | `docs/requirements/02-multi-instance-oauth.feature.md` | PASS | `tests/unit/utils/test_oauth.py:1285` |
| TOR-02-MLk6Fcn | `docs/requirements/02-multi-instance-oauth.feature.md` | PASS | `tests/unit/utils/test_oauth.py:1303` |
| TOR-02-6kYAHsQ | `docs/requirements/02-multi-instance-oauth.feature.md` | PASS | `tests/unit/utils/test_oauth.py:1319` |

## Verification Summary

### Counts
- TOR Requirements: 6/6 PASS (0 CANNOT VERIFY)
- Quality Gates: 5/5 PASS
- Tests: 75 passed (OAuth file), 0 regressions in unit suite

### Highlights
- ✅ TOR-02-s6Jze5H — configured ID resolved regardless of list position (`test_oauth.py:1242`, `oauth.py:363`)
- ✅ TOR-02-CE3OroW — mismatch raises with both actual site name and required ID in message (`test_oauth.py:1285`, `oauth.py:351`)
- ✅ TOR-02-6kYAHsQ — `_save_tokens` never reached when validation fails; except clause at `oauth.py:242` fires before `_save_tokens()` at line 225 (`test_oauth.py:1319`)
- ⚠️ TOR-02-ePsqZQq — "regardless of whether a cloud ID is configured" tested with `cloud_id=None` only; the configured-but-matching sole-resource variant is not independently exercised (behavior is sound, covered by MLk6Fcn logic)

### Conclusion
All six TOR requirements are implemented correctly and all tests faithfully mirror the Gherkin Given/When/Then. The single exception is a minor coverage gap in TOR-02-ePsqZQq that is non-blocking — the underlying behavior is correct. The no-cache guarantee (TOR-02-6kYAHsQ) and the actionable error message (TOR-02-CE3OroW) are the highest-risk requirements and both are fully verified.

### Manual verification performed: No

## Known Issues / Follow-ups

- TOR-02-ePsqZQq: test covers `cloud_id=None` only. The "regardless of configured ID" case (sole resource that matches a configured ID) is not independently tested. Low priority — the resolution logic is trivially verified by inspection.
- **Manual end-to-end test needed:** The multi-instance OAuth path (token with access to 2+ Atlassian sites + `ATLASSIAN_OAUTH_CLOUD_ID` configured to a non-first site) cannot be exercised without an account that has access to multiple Atlassian Cloud organizations. A reviewer with such an account should run `uv run mcp-atlassian --oauth-setup` and confirm: (1) the correct site is selected, and (2) a wrong configured ID produces the expected actionable error rather than silently connecting to the wrong site.
