# Epic 1SVldWi — Session Handoff: Implemented

**Date:** 2026-07-14  
**Branch:** `feature/epic-1SVldWi-multi-instance-oauth-fixes`  
**Base:** `peakflames/main`

---

## What Was Built

Rewrote `OAuthConfig._get_cloud_id()` in `src/mcp_atlassian/utils/oauth.py` to:

1. **Validate** (new behavior): when `ATLASSIAN_OAUTH_CLOUD_ID` is configured, check that the token's accessible resources include it. Raise `MCPAtlassianAuthenticationError` with a human-readable message naming both the token's actual site(s) and the required site if not.

2. **Resolve deterministically** (fix to existing bug): instead of unconditionally taking `resources[0]["id"]`, now applies:
   - Sole resource → that resource's id
   - Multiple resources + configured id → the configured id (guaranteed accessible by the validation step)
   - Multiple resources + no configured id → first resource's id (unchanged fallback)

Added a dedicated `except MCPAtlassianAuthenticationError` clause in `exchange_code_for_tokens()` that logs the full site-mismatch message at ERROR and returns `False` **before** `_save_tokens()` — ensuring the bad token is never cached.

---

## Key Files Changed

| File | Change |
|------|--------|
| `src/mcp_atlassian/utils/oauth.py` | Import `MCPAtlassianAuthenticationError`; rewrite `_get_cloud_id()` (validation → resolution); add dedicated except clause in `exchange_code_for_tokens()` |
| `src/mcp_atlassian/exceptions.py` | Broadened `MCPAtlassianAuthenticationError` docstring to cover OAuth site-access mismatch |
| `tests/unit/utils/test_oauth.py` | Added `TestMultiInstanceOAuthCloudIdResolution` with 6 regression tests (one per TOR) |

---

## Spec Deviations

None. All TOR requirements implemented as specified.

---

## TOR Coverage

| TOR ID | Test | Impl line | Result |
|--------|------|-----------|--------|
| TOR-02-s6Jze5H | `test_get_cloud_id_resolves_configured_match` | `oauth.py:363` (`elif configured_id`) | PASS |
| TOR-02-IUNtYgO | `test_get_cloud_id_falls_back_to_first_when_unconfigured` | `oauth.py:366` (`else`) | PASS |
| TOR-02-ePsqZQq | `test_get_cloud_id_uses_sole_resource` | `oauth.py:361` (`if len == 1`) | PASS |
| TOR-02-CE3OroW | `test_get_cloud_id_rejects_mismatched_site` | `oauth.py:351` (`raise`) | PASS |
| TOR-02-MLk6Fcn | `test_get_cloud_id_accepts_matching_site` | `oauth.py:363` (no raise) | PASS |
| TOR-02-6kYAHsQ | `test_exchange_does_not_cache_on_site_mismatch` | `oauth.py:260` (except clause before `_save_tokens`) | PASS |

---

## Verification Results

- `pytest tests/unit/utils/test_oauth.py -xvs` — **75/75 passed** (all new + existing)
- `pytest tests/unit/ -x` — **all pass** (2 pre-existing failures on Windows path format in `test_attachments.py` and field count in `test_constants.py`, both present on `peakflames/main` before this branch)
- `pre-commit run ruff --all-files` — **passed** (auto-fixed one import sort)
- `pre-commit run ruff-format --all-files` — **passed**
- `pre-commit run mypy --all-files` — **1 pre-existing error** in `servers/main.py:363` (`TTLCache` type args), not introduced by this epic

---

## Notes

- The `uv sync --frozen --all-extras --dev --no-editable` command does not reinstall the package when the lockfile is unchanged. Source file changes must be copied to `.venv/Lib/site-packages/mcp_atlassian/` manually or by `uv sync --reinstall-package` (which fails due to the PEP 440 version tag issue documented in CLAUDE.md). This is a known limitation of the peakflames fork on Windows.
