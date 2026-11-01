"""
main.py
───────
Entry-point for the IEEE-Xplore Scraper.
Supports both institutional SSO proxy and direct access modes.

Features:
- Incremental CSV writing: flushes every paper to disk immediately.
- Year range filtering: last 3 or 5 years (toggleable via CLI or .env).
- Automatic text cleaning: removes newlines, deduplicates authors/keywords,
  and normalizes whitespace and quotes.

Usage:
    python main.py                           # uses defaults from .env
    python main.py --direct                  # skip SSO / proxy
    python main.py --years 3                 # papers from last 3 years
    python main.py --years 5                 # papers from last 5 years
    python main.py --years off               # disable year filter (all years)
    python main.py --query "deep learning"   # override search query
    python main.py --pages 5                 # limit to 5 pages
    python main.py --headless                # run without browser window
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

from playwright.sync_api import sync_playwright

import config
from auth import login_sso, navigate_to_ieee, navigate_to_ieee_direct
from export import IncrementalCSVWriter, read_csv, sanitize_filename
from scrapers.ieee import IEEEScraper

# ── Logging ───────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")


def parse_year_filter(val: Optional[Union[str, int]]) -> Optional[int]:
    """
    Parse year filter into integer (e.g. 3 or 5) or None if off.
    Accepted toggle values for off: '0', 'off', 'all', 'none', '', None.
    """
    if val is None:
        return None
    val_str = str(val).strip().lower()
    if val_str in ("0", "off", "all", "none", "false", ""):
        return None
    try:
        years = int(val_str)
        return years if years > 0 else None
    except ValueError:
        logger.warning(f"Unrecognized year filter '{val}', disabling filter.")
        return None


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Scrape IEEE Xplore directly or through UI's remote-lib proxy."
    )
    p.add_argument(
        "-q", "--query",
        default=config.SEARCH_QUERY,
        help="Search query (default: from .env)",
    )
    p.add_argument(
        "-p", "--pages",
        type=int,
        default=config.MAX_PAGES,
        help="Max result pages to scrape (default: from .env)",
    )
    p.add_argument(
        "-y", "--years",
        type=str,
        default=None,
        help="Filter papers from last N years (e.g. '3', '5', or 'off'/'all' to disable) [default: from .env]",
    )
    p.add_argument(
        "-m", "--mode",
        choices=["proxy", "direct"],
        default=config.MODE,
        help="Access mode: 'proxy' (login via SSO remote-lib) or 'direct' (no login, direct to IEEE) [default: from .env]",
    )
    p.add_argument(
        "--direct",
        action="store_true",
        help="Shortcut flag for --mode direct (skip SSO login and proxy)",
    )
    p.add_argument(
        "--headless",
        action="store_true",
        default=config.HEADLESS,
        help="Run browser in headless mode",
    )
    p.add_argument(
        "-o", "--output",
        type=str,
        default=None,
        help="Output CSV filename (auto-generated if omitted)",
    )
    p.add_argument(
        "--rescrape-csv",
        type=str,
        default=None,
        help="Re-scrape and fill in empty abstracts for an existing CSV file",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()

    # Determine mode and year filter
    effective_mode = "direct" if args.direct else args.mode
    raw_years = args.years if args.years is not None else config.YEAR_FILTER
    effective_years = parse_year_filter(raw_years)

    # If running in rescrape-only mode for an existing CSV file
    if args.rescrape_csv:
        csv_file = Path(args.rescrape_csv)
        if not csv_file.exists():
            csv_file = config.OUTPUT_DIR / args.rescrape_csv
        if not csv_file.exists():
            logger.error(f"File CSV tidak ditemukan: {args.rescrape_csv}")
            sys.exit(1)

        out_path = csv_file
        papers_to_rescrape = read_csv(out_path)
        incomplete_count = sum(
            1
            for p in papers_to_rescrape
            if p.url
            and p.title.strip().lower()
            not in ("index", "author index", "table of contents", "contents")
            and (
                not p.abstract.strip()
                or not p.authors.strip()
                or not p.doi.strip()
            )
        )
        if incomplete_count == 0:
            logger.info(
                f"✓ Semua data pada '{out_path.name}' sudah memiliki abstract, authors, dan DOI lengkap."
            )
            return

        logger.info("=" * 60)
        logger.info("IEEE Scraper — Re-scrape Mode untuk File Eksisting")
        logger.info("=" * 60)
        logger.info(f"Target File  : {out_path}")
        logger.info(
            f"Belum Lengkap: {incomplete_count}/{len(papers_to_rescrape)} paper (missing abstract, authors, atau DOI)"
        )
        logger.info(f"Mode         : {effective_mode.upper()}")
        logger.info("=" * 60)

        with IncrementalCSVWriter(out_path, resume=True) as writer:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(
                    channel="chrome",
                    headless=args.headless,
                    slow_mo=config.SLOW_MO,
                )
                context = browser.new_context(
                    viewport={"width": 1440, "height": 900},
                    ignore_https_errors=True,
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                )
                page = context.new_page()
                page.set_default_timeout(config.DEFAULT_TIMEOUT)

                try:
                    if effective_mode == "direct":
                        navigate_to_ieee_direct(page)
                    else:
                        login_sso(page)
                        navigate_to_ieee(page)

                    scraper = IEEEScraper(page)
                    updated_papers = scraper.rescrape_incomplete_papers(
                        papers_to_rescrape
                    )
                    writer.rewrite_all(updated_papers)
                    logger.info(f"🎉 Selesai! File {out_path} berhasil diperbarui.")
                    return
                finally:
                    context.close()
                    browser.close()

    # Build output filename
    if args.output:
        raw_out = Path(args.output)
        clean_name = sanitize_filename(raw_out.stem) + (raw_out.suffix or ".csv")
        out_path = config.OUTPUT_DIR / clean_name
    else:
        safe_query = sanitize_filename(args.query, max_length=40)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        year_tag = f"_{effective_years}y" if effective_years else ""
        out_path = config.OUTPUT_DIR / f"ieee_{safe_query}{year_tag}_{timestamp}.csv"

    # Year filter description
    if effective_years:
        curr_yr = datetime.now().year
        year_desc = f"LAST {effective_years} YEARS ({curr_yr - effective_years + 1} - {curr_yr})"
    else:
        year_desc = "OFF (All years)"

    logger.info("=" * 60)
    logger.info("IEEE Xplore Scraper — Systematic Literature Review")
    logger.info("=" * 60)
    logger.info(
        f"Mode        : {effective_mode.upper()} "
        f"({'Direct IEEE access' if effective_mode == 'direct' else 'SSO Remote-Lib Proxy'})"
    )
    logger.info(f"Query       : {args.query}")
    logger.info(f"Year Filter : {year_desc}")
    logger.info(f"Max pages   : {args.pages}")
    logger.info(f"Headless    : {args.headless}")
    logger.info(f"Output      : {out_path}")
    logger.info("=" * 60)

    # Open incremental CSV writer before starting so every paper is immediately stored
    with IncrementalCSVWriter(out_path) as writer:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                channel="chrome",
                headless=args.headless,
                slow_mo=config.SLOW_MO,
            )
            context = browser.new_context(
                viewport={"width": 1440, "height": 900},
                ignore_https_errors=True,
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            page = context.new_page()
            page.set_default_timeout(config.DEFAULT_TIMEOUT)

            try:
                max_retries = 3

                if effective_mode == "direct":
                    # Direct mode: skip SSO login, go straight to IEEE Xplore
                    for attempt in range(1, max_retries + 1):
                        try:
                            navigate_to_ieee_direct(page)
                            break
                        except Exception as exc:
                            if attempt < max_retries:
                                logger.warning(
                                    f"Direct navigate to IEEE attempt {attempt}/{max_retries} failed: {exc}. "
                                    f"Retrying in 5 seconds …"
                                )
                                page.wait_for_timeout(5_000)
                            else:
                                raise
                else:
                    # Proxy mode: login via SSO UI then navigate to IEEE proxy
                    for attempt in range(1, max_retries + 1):
                        try:
                            login_sso(page)
                            break
                        except Exception as exc:
                            if attempt < max_retries:
                                logger.warning(
                                    f"SSO login attempt {attempt}/{max_retries} failed: {exc}. "
                                    f"Retrying in 5 seconds …"
                                )
                                page.wait_for_timeout(5_000)
                            else:
                                raise

                    for attempt in range(1, max_retries + 1):
                        try:
                            navigate_to_ieee(page)
                            break
                        except Exception as exc:
                            if attempt < max_retries:
                                logger.warning(
                                    f"Navigate to IEEE attempt {attempt}/{max_retries} failed: {exc}. "
                                    f"Retrying in 5 seconds …"
                                )
                                page.wait_for_timeout(5_000)
                            else:
                                raise

                # 3. Search & Scrape (with gradual incremental saving to disk)
                scraper = IEEEScraper(page)
                scraper.search(args.query, years=effective_years)
                papers = scraper.scrape_results(
                    max_pages=args.pages,
                    years=effective_years,
                    on_paper_scraped=writer.write,
                )

                # 4. Re-scrape check sebelum data di-finalisasi:
                # Cek jika ada paper dengan abstract, authors, atau DOI yang masih kosong
                if papers:
                    papers = scraper.rescrape_incomplete_papers(papers)
                    # Perbarui file CSV final dengan data yang berhasil dilengkapi
                    writer.rewrite_all(papers)

                logger.info(
                    f"🎉 Done! {len(papers)} papers successfully finalized in {out_path}"
                )

            except (Exception, KeyboardInterrupt) as exc:
                logger.error(
                    f"Process interrupted / stopped: {exc}. "
                    f"All {writer.count} papers scraped up to this point are safely stored in {out_path}"
                )
                screenshot_path = config.OUTPUT_DIR / "error_screenshot.png"
                try:
                    page.screenshot(path=str(screenshot_path), full_page=True)
                    logger.info(f"Screenshot saved to {screenshot_path}")
                except Exception:
                    pass
                sys.exit(1)

            finally:
                context.close()
                browser.close()


if __name__ == "__main__":
    main()
