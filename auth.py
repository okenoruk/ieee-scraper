"""
auth.py
───────
Handles SSO UI authentication and remote-lib proxy navigation.
"""

import logging
import time
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

import config

logger = logging.getLogger(__name__)


def login_sso(page: Page) -> None:
    """
    Login to SSO UI (sso.ui.ac.id/cas/login) and wait until
    redirected to remote-lib.ui.ac.id.
    """
    logger.info("Navigating to SSO login page …")

    # Try networkidle first; fall back to domcontentloaded which is
    # less strict but more reliable on slow / flaky connections.
    for wait_strategy in ("networkidle", "domcontentloaded", "commit"):
        try:
            page.goto(config.SSO_LOGIN_URL, wait_until=wait_strategy,
                      timeout=config.DEFAULT_TIMEOUT)
            break
        except Exception as exc:
            logger.warning(f"goto failed with wait_until='{wait_strategy}': {exc}")
            if wait_strategy == "commit":
                raise

    logger.info(f"  Current URL: {page.url}")

    # Wait for the login form to be ready
    page.wait_for_selector(
        'input[name="username"]', timeout=config.DEFAULT_TIMEOUT
    )

    # Fill credentials
    logger.info("Filling in SSO credentials …")
    page.fill('input[name="username"]', config.SSO_USERNAME)
    page.fill('input[name="password"]', config.SSO_PASSWORD)

    # Submit
    page.click('input[type="submit"], button[type="submit"]')
    logger.info("Submitted login form, waiting for redirect …")

    # Wait until we leave the SSO domain — the redirect target may
    # vary (could be /menu, /login, or something else entirely).
    # Use polling instead of wait_for_url because glob matching can
    # be unreliable with complex URLs.
    import time as _time
    deadline = _time.time() + (config.DEFAULT_TIMEOUT / 1000)
    while _time.time() < deadline:
        current_url = page.url
        if "sso.ui.ac.id" not in current_url:
            if current_url.startswith("chrome-error://"):
                page.wait_for_timeout(1_000)
                try:
                    page.reload(wait_until="domcontentloaded", timeout=10_000)
                except Exception:
                    pass
                if not page.url.startswith("chrome-error://"):
                    break
            else:
                break
        # Check for login errors on the SSO page
        error_el = page.query_selector('.errors, .error, #msg, .alert-danger')
        if error_el:
            error_text = error_el.inner_text().strip()
            if error_text:
                logger.error(f"SSO login error: {error_text}")
                raise RuntimeError(f"SSO login failed: {error_text}")
        page.wait_for_timeout(500)
    else:
        logger.warning(
            f"Timeout waiting for redirect from SSO. Current URL: {page.url}"
        )

    if page.url.startswith("chrome-error://"):
        raise RuntimeError(f"SSO redirect landed on browser error page: {page.url}")

    logger.info(f"✓ Successfully logged in — landed on: {page.url}")

    # If we're not on /menu yet, try navigating there explicitly
    if "/menu" not in page.url:
        logger.info("Navigating explicitly to remote-lib menu …")
        try:
            page.goto(config.REMOTE_LIB_MENU_URL,
                      wait_until="domcontentloaded",
                      timeout=config.DEFAULT_TIMEOUT)
            logger.info(f"  Now at: {page.url}")
        except Exception as exc:
            logger.warning(f"Could not reach /menu directly: {exc}")
            # Not fatal — we may still be able to reach IEEE


def dismiss_cookie_banner(page: Page) -> None:
    """
    Dismiss any OneTrust or GDPR cookie consent popups if present.
    """
    cookie_selectors = [
        '#onetrust-accept-btn-handler',
        'button:has-text("Accept All Cookies")',
        'button:has-text("Accept All")',
        'button:has-text("Accept")',
        'button:has-text("I Agree")',
        '.optanon-allow-all',
        '#cookie-accept',
    ]
    for sel in cookie_selectors:
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click()
                logger.info(f"Dismissed cookie consent banner via '{sel}'.")
                page.wait_for_timeout(1_000)
                break
        except Exception:
            pass


def navigate_to_ieee_direct(page: Page, target_url: str = None) -> None:
    """
    Directly navigate to IEEE Xplore without SSO login or remote-lib proxy.

    Parameters:
        page: Playwright Page instance.
        target_url: URL to navigate to (defaults to config.IEEE_DIRECT_URL).
    """
    url = target_url or config.IEEE_DIRECT_URL
    logger.info(f"Navigating directly to IEEE Xplore ({url}) without login/proxy …")

    last_error = None
    for wait_strategy in ("domcontentloaded", "commit", "load"):
        try:
            page.goto(url, wait_until=wait_strategy, timeout=config.DEFAULT_TIMEOUT)
            if not page.url.startswith("chrome-error://"):
                break
            else:
                logger.warning(f"Landed on error page {page.url}, retrying reload …")
                page.wait_for_timeout(2_000)
                page.reload(wait_until=wait_strategy, timeout=config.DEFAULT_TIMEOUT)
                if not page.url.startswith("chrome-error://"):
                    break
        except Exception as exc:
            last_error = exc
            logger.warning(
                f"Direct IEEE goto failed with wait_until='{wait_strategy}': {exc}"
            )
            page.wait_for_timeout(2_000)

    if page.url.startswith("chrome-error://"):
        raise RuntimeError(
            f"Failed to navigate directly to IEEE Xplore (landed on {page.url}): {last_error}"
        )

    logger.info(f"  Current URL: {page.url}")

    # Give SPA time to initialize
    time.sleep(2)

    # Dismiss any cookie banner that may block interactions
    dismiss_cookie_banner(page)

    # Wait for the search bar or main interactive elements
    try:
        page.wait_for_selector(
            'input[type="text"], .Typeahead-input, input[aria-label], .global-search-bar',
            timeout=config.DEFAULT_TIMEOUT,
        )
    except PlaywrightTimeout:
        logger.warning(
            "Search input selector not immediately found; continuing anyway."
        )

    # Re-check cookie banner just in case it popped up late
    dismiss_cookie_banner(page)

    logger.info("✓ IEEE Xplore home page loaded directly.")


def navigate_to_ieee(page: Page) -> None:
    """
    From the remote-lib menu, navigate to IEEE Xplore through the
    university proxy.
    """
    logger.info("Navigating to IEEE Xplore via proxy …")

    last_error = None
    for wait_strategy in ("domcontentloaded", "commit", "load"):
        try:
            page.goto(config.IEEE_PROXY_URL, wait_until=wait_strategy,
                      timeout=config.DEFAULT_TIMEOUT)
            if not page.url.startswith("chrome-error://"):
                break
            else:
                logger.warning(f"Landed on error page {page.url}, retrying reload …")
                page.wait_for_timeout(2_000)
                page.reload(wait_until=wait_strategy, timeout=config.DEFAULT_TIMEOUT)
                if not page.url.startswith("chrome-error://"):
                    break
        except Exception as exc:
            last_error = exc
            logger.warning(
                f"IEEE goto failed with wait_until='{wait_strategy}': {exc}"
            )
            page.wait_for_timeout(2_000)

    if page.url.startswith("chrome-error://"):
        raise RuntimeError(
            f"Failed to navigate to IEEE proxy (landed on {page.url}): {last_error}"
        )

    logger.info(f"  Current URL: {page.url}")

    # Wait some time for the SPA to bootstrap
    time.sleep(3)

    # IEEE may take a while to load through the proxy; wait for
    # a recognisable element on the IEEE home page.
    page.wait_for_selector(
        'input[type="text"], .Typeahead-input, input[aria-label]',
        timeout=config.DEFAULT_TIMEOUT,
    )
    # Dismiss any cookie banner if present
    dismiss_cookie_banner(page)

    logger.info("✓ IEEE Xplore home page loaded.")

