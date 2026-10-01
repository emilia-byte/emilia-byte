"""
fb_dom.py

Shared navigation helper for the Playwright scripts in this repo.

Navigation goes through window.location rather than page.goto() because
Multilogin's proxy-auth injection only applies to in-page JS navigation —
page.goto() bypasses it and fails with ERR_INVALID_AUTH_CREDENTIALS.

The destination MUST be passed as an evaluate() argument, never built into
the JS source with an f-string. Some callers pass spreadsheet-derived data
(e.g. a Facebook UID typed by hand into an ops sheet) straight into the
navigation URL; interpolating that into JS source is a script-injection
vector into an authenticated Facebook session the moment a cell contains a
stray quote.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def js_navigate(page, url: str, timeout: int = 60000) -> None:
    """Sync-API navigate. For the async API (boost.py), await the same
    two calls directly rather than importing this.

    The navigation itself can tear down the JS context before evaluate()
    returns, and a slow page load isn't fatal to callers (they wait on their
    own selectors next), so both are logged rather than raised."""
    try:
        page.evaluate("(u) => { window.location.href = u; }", url)
    except Exception as exc:
        if "Execution context was destroyed" not in str(exc):
            raise
        log.debug("js_navigate: context destroyed mid-navigation (expected): %s", exc)
    try:
        page.wait_for_load_state("domcontentloaded", timeout=timeout)
    except Exception as exc:
        log.debug("js_navigate: load-state wait for %s failed: %s", url, exc)
