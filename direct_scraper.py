"""
direct_scraper.py
─────────────────
Alternative standalone script to scrape IEEE Xplore directly
WITHOUT SSO login or remote-lib.ui.ac.id proxy.

Features:
- Incremental CSV writing: records are saved immediately to disk.
- Year range filtering: last 3 or 5 years (toggleable via CLI or .env).
- Automatic text cleaning: strips newlines, quotes, and duplicates.

Usage:
    python direct_scraper.py                           # uses defaults
    python direct_scraper.py --query "graph neural"   # custom query
    python direct_scraper.py --years 3                 # last 3 years
    python direct_scraper.py --years 5                 # last 5 years
    python direct_scraper.py --years off               # all years
    python direct_scraper.py --pages 3                 # limit pages
    python direct_scraper.py --headless                # run headless
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Union

from playwright.sync_api import sync_playwright

import config
from auth import navigate_to_ieee_direct
from export import IncrementalCSVWriter, read_csv, sanitize_filename
from scrapers.base import PaperRecord
from scrapers.ieee import IEEEScraper

# ── Logging ───────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("direct_scraper")


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
        return None


def scrape_ieee_direct(
    query: str,
    max_pages: int = 5,
    years: Optional[int] = None,
    headless: bool = False,
    output_path: Optional[Path] = None,
) -> List[PaperRecord]:
    """
    Directly scrapes IEEE Xplore without login or proxy, saving
    each record incrementally to CSV.
    """
    if years:
        curr_yr = datetime.now().year
        year_desc = f"LAST {years} YEARS ({curr_yr - years + 1} - {curr_yr})"
    else:
        year_desc = "OFF (All years)"

    logger.info("=" * 60)
    logger.info("IEEE Xplore Scraper — Direct Mode (No SSO / No Proxy)")
    logger.info("=" * 60)
    logger.info(f"Query       : {query}")
    logger.info(f"Year Filter : {year_desc}")
    logger.info(f"Max pages   : {max_pages}")
    logger.info(f"Headless    : {headless}")
    logger.info(f"Output      : {output_path}")
    logger.info("=" * 60)

    papers: List[PaperRecord] = []

    # Use IncrementalCSVWriter if output path provided
    writer = IncrementalCSVWriter(output_path) if output_path else None

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                channel="chrome",
                headless=headless,
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
                # 1. Navigate directly to IEEE Xplore
                navigate_to_ieee_direct(page)

                # 2. Search & scrape with incremental saving
                scraper = IEEEScraper(page)
                scraper.search(query, years=years)
                papers = scraper.scrape_results(
                    max_pages=max_pages,
                    years=years,
                    on_paper_scraped=writer.write if writer else None,
                )

                # 3. Re-scrape check sebelum finalisasi:
                # Cek jika ada paper dengan abstract, authors, atau DOI yang masih kosong
                if papers:
                    papers = scraper.rescrape_incomplete_papers(papers)
                    if writer:
                        writer.rewrite_all(papers)

            except (Exception, KeyboardInterrupt) as exc:
                count_saved = writer.count if writer else 0
                logger.error(
                    f"Scraping stopped/interrupted: {exc}. "
                    f"Saved {count_saved} records safely to disk."
                )
                screenshot_path = config.OUTPUT_DIR / "direct_error_screenshot.png"
                try:
                    page.screenshot(path=str(screenshot_path), full_page=True)
                    logger.info(f"Screenshot saved to {screenshot_path}")
                except Exception:
                    pass
                raise

            finally:
                context.close()
                browser.close()

    finally:
        if writer:
            writer.close()

    return papers


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Directly scrape IEEE Xplore without login or university proxy."
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

    # Re-scrape mode for existing CSV file
    if args.rescrape_csv:
        csv_file = Path(args.rescrape_csv)
        if not csv_file.exists():
            csv_file = config.OUTPUT_DIR / args.rescrape_csv
        if not csv_file.exists():
            logger.error(f"File CSV tidak ditemukan: {args.rescrape_csv}")
            sys.exit(1)

        papers_to_rescrape = read_csv(csv_file)
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
                f"✓ Semua data pada '{csv_file.name}' sudah memiliki abstract, authors, dan DOI lengkap."
            )
            return

        logger.info("=" * 60)
        logger.info("IEEE Scraper — Re-scrape Mode (Direct)")
        logger.info("=" * 60)
        logger.info(f"Target File  : {csv_file}")
        logger.info(
            f"Belum Lengkap: {incomplete_count}/{len(papers_to_rescrape)} paper (missing abstract, authors, atau DOI)"
        )
        logger.info("=" * 60)

        with IncrementalCSVWriter(csv_file, resume=True) as writer:
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
                    navigate_to_ieee_direct(page)
                    scraper = IEEEScraper(page)
                    updated = scraper.rescrape_incomplete_papers(
                        papers_to_rescrape
                    )
                    writer.rewrite_all(updated)
                    logger.info(f"🎉 Selesai! File {csv_file} berhasil diperbarui.")
                    return
                finally:
                    context.close()
                    browser.close()

    raw_years = args.years if args.years is not None else config.YEAR_FILTER
    effective_years = parse_year_filter(raw_years)

    if args.output:
        raw_out = Path(args.output)
        clean_name = sanitize_filename(raw_out.stem) + (raw_out.suffix or ".csv")
        out_path = config.OUTPUT_DIR / clean_name
    else:
        safe_query = sanitize_filename(args.query, max_length=40)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        year_tag = f"_{effective_years}y" if effective_years else ""
        out_path = config.OUTPUT_DIR / f"ieee_direct_{safe_query}{year_tag}_{timestamp}.csv"

    try:
        papers = scrape_ieee_direct(
            query=args.query,
            max_pages=args.pages,
            years=effective_years,
            headless=args.headless,
            output_path=out_path,
        )
        if papers:
            logger.info(f"🎉 Done! {len(papers)} papers saved to {out_path}")
        else:
            logger.warning("No papers scraped.")
    except Exception:
        sys.exit(1)


if __name__ == "__main__":
    main()
