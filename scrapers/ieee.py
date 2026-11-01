"""
scrapers/ieee.py
────────────────
Scraper for IEEE Xplore (supports both direct and remote-lib.ui.ac.id proxy access).

Features:
- Search and pagination on IEEE Xplore.
- Year range filtering (e.g. last 3 or 5 years) via both search URL parameters and
  client-side validation.
- Incremental detail scraping using an isolated browser tab to keep search pagination intact.
- Comprehensive text cleaning (no newlines, deduplicated authors/keywords, valid URLs).
- Gradual streaming of results to callback/CSV writer.
"""

import logging
import re
import time
from datetime import datetime
from typing import Callable, List, Optional

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

import config
from .base import BaseScraper, PaperRecord

logger = logging.getLogger(__name__)

_SEARCH_INPUT_SELECTORS = [
    "input.Typeahead-input",
    'input[aria-label="Search Input"]',
    'input[type="text"][placeholder]',
    ".global-search-bar input",
]

_RESULT_ITEM_SELECTOR = ".List-results-items"
_NEXT_PAGE_SELECTOR = (
    ".next-btn, button[aria-label='Next Page'], .stats-pagination-next-btn"
)


class IEEEScraper(BaseScraper):
    """IEEE Xplore scraper."""

    name = "IEEE Xplore"

    def __init__(self, page: Page):
        super().__init__(page)
        self._base_url = ""

    # ──────────────────────────────────────────────────────────
    #  Search & Year Filtering
    # ──────────────────────────────────────────────────────────

    def search(self, query: str, years: Optional[int] = None) -> None:
        """
        Type the query into the IEEE search box, submit, and optionally
        apply a year range filter (e.g. last 3 or 5 years).
        """
        logger.info(f"Searching IEEE Xplore for: «{query}»")

        search_input = None
        for selector in _SEARCH_INPUT_SELECTORS:
            try:
                search_input = self.page.wait_for_selector(selector, timeout=8_000)
                if search_input:
                    break
            except PlaywrightTimeout:
                continue

        if not search_input:
            raise RuntimeError(
                "Could not locate the IEEE search input box. "
                "The page layout may have changed."
            )

        search_input.click()
        search_input.fill(query)
        time.sleep(0.5)

        # Press Enter to submit
        search_input.press("Enter")

        # Wait for search results to load
        self.page.wait_for_selector(
            _RESULT_ITEM_SELECTOR,
            timeout=30_000,
        )
        logger.info("✓ Initial search results loaded.")

        # Apply server-side year filter if requested
        if years and years > 0:
            current_year = datetime.now().year
            start_year = current_year - years + 1
            end_year = current_year
            logger.info(
                f"Applying server-side year filter: last {years} years ({start_year} - {end_year}) …"
            )

            current_url = self.page.url
            filter_param = f"ranges={start_year}_{end_year}_Year"
            if filter_param not in current_url:
                delim = "&" if "?" in current_url else "?"
                filtered_url = f"{current_url}{delim}{filter_param}"
                try:
                    self.page.goto(
                        filtered_url,
                        wait_until="domcontentloaded",
                        timeout=config.DEFAULT_TIMEOUT,
                    )
                    self.page.wait_for_selector(
                        _RESULT_ITEM_SELECTOR, timeout=25_000
                    )
                    logger.info("✓ Search results updated with year range filter.")
                except Exception as exc:
                    logger.warning(
                        f"Could not apply server-side year filter URL: {exc}. "
                        "Will enforce year filter on scraped records client-side."
                    )

    # ──────────────────────────────────────────────────────────
    #  Scrape result listing pages
    # ──────────────────────────────────────────────────────────

    def scrape_results(
        self,
        max_pages: int = 10,
        years: Optional[int] = None,
        on_paper_scraped: Optional[Callable[[PaperRecord], None]] = None,
    ) -> List[PaperRecord]:
        """
        Iterate through up to *max_pages* pages of results.
        For each paper:
        1. Checks year filter (e.g. last 3 or 5 years).
        2. Enriches with abstract, keywords, DOI, etc. from detail page.
        3. Cleans text (removes newlines, deduplicates authors/keywords).
        4. Gradually streams each paper to *on_paper_scraped* (e.g. CSV writer).
        """
        all_papers: List[PaperRecord] = []
        current_page = 1
        min_year = (datetime.now().year - years + 1) if (years and years > 0) else None

        if min_year:
            logger.info(
                f"Active Year Filter: Only papers from year >= {min_year} will be kept."
            )

        # Open a dedicated detail tab in the same context so search pagination
        # stays intact and isn't repeatedly reloaded.
        detail_page = None
        try:
            detail_page = self.page.context.new_page()
            detail_page.set_default_timeout(config.DEFAULT_TIMEOUT)
        except Exception as exc:
            logger.debug(f"Separate detail page not created ({exc}); using main page.")

        try:
            while current_page <= max_pages:
                logger.info(
                    f"── Scraping result page {current_page}/{max_pages} ──"
                )

                # Wait for result items to appear
                try:
                    self.page.wait_for_selector(
                        _RESULT_ITEM_SELECTOR, timeout=20_000
                    )
                except PlaywrightTimeout:
                    logger.warning("No results found on this page — stopping.")
                    break

                papers_on_page = self._extract_listing_page()
                logger.info(
                    f"   Found {len(papers_on_page)} listings on page {current_page}."
                )

                for idx, paper in enumerate(papers_on_page, start=1):
                    # Check listing year filter before opening detail page
                    if min_year and paper.year:
                        try:
                            y = int(re.search(r"\b(19|20)\d{2}\b", paper.year).group(0))
                            if y < min_year:
                                logger.info(
                                    f"   [Skip: Year {y} < {min_year}] {paper.title[:50]}…"
                                )
                                continue
                        except Exception:
                            pass

                    # Fetch paper detail
                    logger.info(
                        f"   [{idx}/{len(papers_on_page)}] Fetching detail: {paper.title[:50]}…"
                    )
                    target_page = (
                        detail_page
                        if (detail_page and not detail_page.is_closed())
                        else self.page
                    )
                    try:
                        paper = self.scrape_paper_detail(paper, page=target_page)
                    except Exception as exc:
                        logger.warning(
                            f"   ⚠ Could not fetch detail for '{paper.title[:40]}': {exc}"
                        )

                    # Sanitize all fields
                    paper.clean()

                    # Re-verify year after detail enrichment
                    if min_year and paper.year:
                        try:
                            y = int(re.search(r"\b(19|20)\d{2}\b", paper.year).group(0))
                            if y < min_year:
                                logger.info(
                                    f"   [Skip: Detail Year {y} < {min_year}] {paper.title[:50]}…"
                                )
                                continue
                        except Exception:
                            pass

                    all_papers.append(paper)

                    # Gradually save paper to CSV if callback is registered
                    if on_paper_scraped:
                        try:
                            on_paper_scraped(paper)
                        except Exception as exc:
                            logger.error(f"Error in on_paper_scraped callback: {exc}")

                # Go to next page
                if current_page < max_pages:
                    if not self._go_to_next_page():
                        logger.info("No more pages available.")
                        break

                current_page += 1

        finally:
            if detail_page:
                try:
                    detail_page.close()
                except Exception:
                    pass

        logger.info(f"Total papers successfully processed: {len(all_papers)}")
        return all_papers

    # ──────────────────────────────────────────────────────────
    #  Extract data from one listing page
    # ──────────────────────────────────────────────────────────

    def _extract_listing_page(self) -> List[PaperRecord]:
        """Parse all result cards on the current page."""
        papers: List[PaperRecord] = []

        items = self.page.query_selector_all(_RESULT_ITEM_SELECTOR)
        for item in items:
            paper = PaperRecord(source_database="IEEE Xplore")

            # Title + URL
            title_el = item.query_selector("h3 a, .result-item-title a, h2 a")
            if title_el:
                paper.title = (title_el.inner_text() or "").strip()
                href = title_el.get_attribute("href") or ""
                if href:
                    paper.url = self._abs_url(href)

            # Authors
            authors_el = item.query_selector_all(
                ".author a, .authors-info a, p.author a"
            )
            if authors_el:
                paper.authors = "; ".join(
                    a.inner_text().strip()
                    for a in authors_el
                    if a.inner_text().strip()
                )

            # Publication title
            pub_el = item.query_selector(
                ".publisher-info-container a, .description a, a.stats-document-abstract-publishedIn"
            )
            if pub_el:
                paper.publication_title = (pub_el.inner_text() or "").strip()

            # Year
            pub_info = item.query_selector(
                ".publisher-info-container, .description"
            )
            if pub_info:
                text = pub_info.inner_text() or ""
                year_match = re.search(r"\b(19|20)\d{2}\b", text)
                if year_match:
                    paper.year = year_match.group(0)

            # Access type badge
            access_el = item.query_selector(
                ".icon-access-open-access, .open-access, "
                '.fa-lock-open, [title*="Open Access"]'
            )
            paper.ieee_access_type = "Open Access" if access_el else ""

            # Snippet / short abstract shown in listing
            snippet_el = item.query_selector(
                ".js-displayer-content span, .result-item-abstract"
            )
            if snippet_el:
                paper.abstract = (snippet_el.inner_text() or "").strip()

            papers.append(paper)

        return papers

    # ──────────────────────────────────────────────────────────
    #  Detail page scraping
    # ──────────────────────────────────────────────────────────

    def scrape_paper_detail(
        self, paper: PaperRecord, page: Optional[Page] = None
    ) -> PaperRecord:
        """
        Navigate to the paper's detail page and extract the full
        abstract, keywords, DOI, citation count, PDF link, etc.
        """
        if not paper.url:
            return paper

        p = page or self.page
        try:
            p.goto(paper.url, wait_until="domcontentloaded", timeout=45_000)
        except Exception as exc:
            logger.warning(f"goto failed for {paper.url}: {exc}")
            try:
                p.goto(paper.url, wait_until="commit", timeout=20_000)
            except Exception:
                return paper

        # Give the SPA a moment to render
        time.sleep(1.5)

        # ── Abstract ─────────────────────────────────────────
        # Try primary DOM selectors with wait
        try:
            abs_el = p.wait_for_selector(
                ".abstract-text div, .abstract-desktop-div div[class*='u-mb'], "
                "div[xplmathjax], .abstract-text, .abstract-desktop-div, "
                ".document-abstract, div.document-main-abstract",
                timeout=8_000,
            )
            if abs_el:
                paper.abstract = (abs_el.inner_text() or "").strip()
        except PlaywrightTimeout:
            pass

        # Fallback 1: Query abstract containers directly without wait
        if not paper.abstract:
            for sel in [
                ".abstract-text div",
                ".abstract-desktop-div",
                ".abstract-text",
                "div[class*='abstract']",
                ".u-mb-1.text-base-md-lh",
                ".u-mb-1",
            ]:
                el = p.query_selector(sel)
                if el:
                    txt = (el.inner_text() or "").strip()
                    if txt and len(txt) > 40:
                        paper.abstract = txt
                        break

        # Fallback 2: Meta tags in <head> (rendered server-side on IEEE pages)
        if not paper.abstract:
            for meta_sel in [
                'meta[property="og:description"]',
                'meta[name="citation_abstract"]',
                'meta[name="description"]',
                'meta[name="twitter:description"]',
            ]:
                meta_el = p.query_selector(meta_sel)
                if meta_el:
                    content = (meta_el.get_attribute("content") or "").strip()
                    if (
                        content
                        and len(content) > 30
                        and not content.lower().startswith("ieee xplore")
                    ):
                        paper.abstract = content
                        break

        # Fallback 3: JavaScript global metadata state
        if not paper.abstract:
            try:
                js_abstract = p.evaluate("""() => {
                    try {
                        if (window.xplGlobal && window.xplGlobal.document && window.xplGlobal.document.metadata) {
                            return window.xplGlobal.document.metadata.abstract || '';
                        }
                    } catch (e) {}
                    return '';
                }""")
                if js_abstract and len(js_abstract.strip()) > 20:
                    paper.abstract = js_abstract.strip()
            except Exception:
                pass

        # ── Authors ──────────────────────────────────────────
        # 1. Primary DOM selector
        author_els = p.query_selector_all(
            ".authors-info span[class*='author'] a, "
            '.authors-accordion-container a[href*="author"], '
            'a[href*="/author/"], .authors-banner-item a'
        )
        if author_els:
            names = [
                a.inner_text().strip()
                for a in author_els
                if a.inner_text().strip()
            ]
            if names:
                paper.authors = "; ".join(names)

        # 2. Fallback: <meta name="citation_author"> in <head>
        if not paper.authors:
            meta_authors = p.query_selector_all(
                'meta[name="citation_author"], meta[name="author"]'
            )
            if meta_authors:
                names = [
                    (m.get_attribute("content") or "").strip()
                    for m in meta_authors
                    if (m.get_attribute("content") or "").strip()
                ]
                if names:
                    paper.authors = "; ".join(names)

        # 3. Fallback: JavaScript window.xplGlobal metadata
        if not paper.authors:
            try:
                js_authors = p.evaluate("""() => {
                    try {
                        const meta = window.xplGlobal?.document?.metadata;
                        if (meta && Array.isArray(meta.authors)) {
                            return meta.authors.map(a => a.name || a.preferredName || '').filter(Boolean).join('; ');
                        }
                    } catch (e) {}
                    return '';
                }""")
                if js_authors:
                    paper.authors = js_authors.strip()
            except Exception:
                pass

        # ── Publication title ────────────────────────────────
        pub_title_el = p.query_selector(
            ".stats-document-abstract-publishedIn a, a[href*='punumber'], "
            ".document-banner-conference-title a, .document-banner-conference-title, "
            ".stats-document-abstract-publishedIn"
        )
        if pub_title_el:
            candidate_pub = (pub_title_el.inner_text() or "").strip()
            candidate_pub = re.sub(
                r"^(?:Published in|In)\s*:\s*", "", candidate_pub, flags=re.IGNORECASE
            ).strip()
            if candidate_pub:
                paper.publication_title = candidate_pub

        # Fallback meta tags for publication title
        if not paper.publication_title:
            meta_pub = p.query_selector(
                'meta[name="citation_conference_title"], meta[name="citation_journal_title"], meta[name="citation_series_title"]'
            )
            if meta_pub:
                content = (meta_pub.get_attribute("content") or "").strip()
                if content:
                    paper.publication_title = content

        # Fallback JS global for publication title
        if not paper.publication_title:
            try:
                js_pub = p.evaluate("""() => {
                    try {
                        return window.xplGlobal?.document?.metadata?.publicationTitle || '';
                    } catch (e) {}
                    return '';
                }""")
                if js_pub:
                    paper.publication_title = js_pub.strip()
            except Exception:
                pass

        # ── DOI ──────────────────────────────────────────────
        # 1. Fallback meta tag in <head> (rendered server-side on IEEE pages)
        meta_doi = p.query_selector('meta[name="citation_doi"]')
        if meta_doi:
            content = (meta_doi.get_attribute("content") or "").strip()
            if content:
                paper.doi = content

        # 2. Fallback DOM selectors
        if not paper.doi:
            doi_el = p.query_selector(
                'a[href*="doi.org"], .stats-document-abstract-doi a, .stats-document-abstract-doi span, .stats-document-abstract-doi'
            )
            if doi_el:
                href = doi_el.get_attribute("href") or ""
                if "doi.org" in href:
                    paper.doi = href
                else:
                    txt = (doi_el.inner_text() or "").strip()
                    if "10." in txt:
                        paper.doi = txt

        # 3. Fallback JavaScript global metadata state
        if not paper.doi:
            try:
                js_doi = p.evaluate("""() => {
                    try {
                        return window.xplGlobal?.document?.metadata?.doi || '';
                    } catch (e) {}
                    return '';
                }""")
                if js_doi:
                    paper.doi = js_doi.strip()
            except Exception:
                pass

        # ── Publication type ─────────────────────────────────
        type_el = p.query_selector(
            ".document-banner-conference-title, .stats-document-abstract-contentType"
        )
        if type_el:
            paper.publication_type = (type_el.inner_text() or "").strip()

        # ── Publisher ────────────────────────────────────────
        publisher_el = p.query_selector(
            ".stats-document-abstract-publisher span:last-child, .publisher-info a"
        )
        if publisher_el:
            paper.publisher = (publisher_el.inner_text() or "").strip()

        # ── Date / Year ──────────────────────────────────────
        date_el = p.query_selector(
            ".stats-document-abstract-publishedDate, .doc-abstract-pubdate"
        )
        if date_el:
            text = date_el.inner_text() or ""
            year_match = re.search(r"\b(19|20)\d{2}\b", text)
            if year_match:
                paper.year = year_match.group(0)

        # ── Volume / Issue / Pages ───────────────────────────
        self._extract_volume_issue_pages(paper, page=p)

        # ── Keywords ─────────────────────────────────────────
        self._extract_keywords(paper, page=p)

        # ── Citation count ───────────────────────────────────
        cite_el = p.query_selector(
            ".document-banner-metric-count, .citation-count, "
            'button[class*="citation"] .document-banner-metric-count'
        )
        if cite_el:
            paper.citation_count = (cite_el.inner_text() or "").strip()

        # ── PDF link ─────────────────────────────────────────
        pdf_el = p.query_selector(
            'a[href*="/stamp/stamp.jsp"], a.pdf-btn-link, a[title*="PDF"]'
        )
        if pdf_el:
            href = pdf_el.get_attribute("href") or ""
            paper.pdf_url = self._abs_url(href, page=p) if href else ""

        # Clean all extracted fields
        paper.clean()
        return paper

    # ──────────────────────────────────────────────────────────
    #  Re-scrape Incomplete Papers (Missing Abstract, Authors, or DOI)
    # ──────────────────────────────────────────────────────────

    def rescrape_incomplete_papers(
        self,
        papers: List[PaperRecord],
        max_retries: int = 2,
        on_paper_updated: Optional[Callable[[PaperRecord], None]] = None,
    ) -> List[PaperRecord]:
        """
        Check if any paper in the scraped list is missing key SLR metadata:
        - abstract is empty
        - authors is empty
        - doi is empty

        For each incomplete paper, re-scrapes its detail page specifically.
        Returns the updated list of papers.
        """
        missing_entries = []
        for i, p in enumerate(papers):
            if not p.url:
                continue
            title_lower = p.title.strip().lower()
            # Ignore pure table of contents or index entries
            if title_lower in (
                "index",
                "author index",
                "subject index",
                "table of contents",
                "contents",
            ):
                continue

            missing = []
            if not p.abstract or not p.abstract.strip():
                missing.append("abstract")
            if not p.authors or not p.authors.strip():
                missing.append("authors")
            if not p.doi or not p.doi.strip():
                missing.append("doi")

            if missing:
                missing_entries.append((i, missing))

        if not missing_entries:
            logger.info(
                "✓ Semua paper telah memiliki metadata lengkap (abstract, authors, DOI). "
                "Tidak diperlukan re-scrape."
            )
            return papers

        logger.info(
            f"🔍 Ditemukan {len(missing_entries)}/{len(papers)} paper dengan metadata belum lengkap "
            f"(abstract, authors, atau DOI kosong). Memulai re-scrape otomatis …"
        )

        rescrape_page = None
        try:
            rescrape_page = self.page.context.new_page()
            rescrape_page.set_default_timeout(config.DEFAULT_TIMEOUT)
        except Exception as exc:
            logger.warning(
                f"Tidak dapat membuka tab baru untuk rescrape ({exc}), menggunakan page utama."
            )
            rescrape_page = self.page

        recovered_count = 0

        try:
            for count, (idx, missing_fields) in enumerate(missing_entries, start=1):
                paper = papers[idx]
                fields_str = ", ".join(missing_fields)
                logger.info(
                    f"   [Re-scrape {count}/{len(missing_entries)}] Memproses '{paper.title[:50]}…' (Missing: {fields_str})"
                )

                for attempt in range(1, max_retries + 1):
                    try:
                        # Ensure page is alive
                        if rescrape_page.is_closed():
                            rescrape_page = self.page.context.new_page()
                            rescrape_page.set_default_timeout(config.DEFAULT_TIMEOUT)

                        updated_paper = self.scrape_paper_detail(
                            paper, page=rescrape_page
                        )
                        updated_paper.clean()

                        # Check which missing fields were successfully recovered
                        recovered_fields = [
                            f
                            for f in missing_fields
                            if getattr(updated_paper, f, "").strip()
                        ]
                        still_missing = [
                            f
                            for f in missing_fields
                            if not getattr(updated_paper, f, "").strip()
                        ]

                        if recovered_fields:
                            papers[idx] = updated_paper
                            recovered_count += 1
                            logger.info(
                                f"   ✓ [Berhasil Re-scrape] Berhasil melengkapi: {', '.join(recovered_fields)}"
                                f"{' | Masih belum ada: ' + ', '.join(still_missing) if still_missing else ' (Semua Lengkap!)'}"
                            )
                            if on_paper_updated:
                                try:
                                    on_paper_updated(updated_paper)
                                except Exception as exc:
                                    logger.error(
                                        f"Error in on_paper_updated callback: {exc}"
                                    )
                            # If all previously missing fields are now found, move to next paper
                            if not still_missing:
                                break
                        else:
                            if attempt < max_retries:
                                logger.info(
                                    f"   ... Percobaan {attempt}/{max_retries} belum mendapatkan data baru, jeda 2 detik …"
                                )
                                time.sleep(2)
                    except Exception as exc:
                        logger.warning(
                            f"   ⚠ Percobaan {attempt}/{max_retries} error: {exc}"
                        )
                        time.sleep(2)

        finally:
            if rescrape_page and rescrape_page != self.page:
                try:
                    rescrape_page.close()
                except Exception:
                    pass

        logger.info(
            f"🎉 Selesai Re-scrape: {recovered_count}/{len(missing_entries)} paper berhasil diperbaiki metadatanya."
        )
        return papers

    def rescrape_missing_abstracts(
        self,
        papers: List[PaperRecord],
        max_retries: int = 2,
        on_paper_updated: Optional[Callable[[PaperRecord], None]] = None,
    ) -> List[PaperRecord]:
        """Backward-compatible alias for rescrape_incomplete_papers."""
        return self.rescrape_incomplete_papers(
            papers, max_retries=max_retries, on_paper_updated=on_paper_updated
        )

    # ──────────────────────────────────────────────────────────
    #  Helpers
    # ──────────────────────────────────────────────────────────

    def _extract_volume_issue_pages(
        self, paper: PaperRecord, page: Optional[Page] = None
    ) -> None:
        """Try to pull volume, issue, and page numbers from the page."""
        p = page or self.page
        info_text = ""
        info_el = p.query_selector(
            ".stats-document-abstract-volume, .u-pb-1.stats-document-abstract-publishedIn"
        )
        if info_el:
            info_text = info_el.inner_text() or ""

        vol_match = re.search(r"[Vv]olume\s*:\s*(\S+)", info_text)
        if vol_match:
            paper.volume = vol_match.group(1)

        issue_match = re.search(r"[Ii]ssue\s*:\s*(\S+)", info_text)
        if issue_match:
            paper.issue = issue_match.group(1)

        pages_match = re.search(
            r"[Pp]age\(?s?\)?\s*:\s*([\d\s\-–]+)", info_text
        )
        if pages_match:
            paper.pages = pages_match.group(1).strip()

    def _extract_keywords(
        self, paper: PaperRecord, page: Optional[Page] = None
    ) -> None:
        """
        Keywords are often behind a 'Keywords' accordion. Click to expand
        and scrape keyword tokens.
        """
        p = page or self.page
        try:
            kw_btn = p.query_selector(
                'button:has-text("Keywords"), #defined-keywords, a[href="#keywords"]'
            )
            if kw_btn:
                try:
                    kw_btn.click(timeout=3_000)
                    time.sleep(1)
                except Exception:
                    pass

            kw_items = p.query_selector_all(
                ".stats-keywords-container a, ul.Keywords-list li a"
            )
            if kw_items:
                raw_kws = []
                for kw in kw_items:
                    text = (kw.inner_text() or "").strip()
                    if text:
                        raw_kws.append(text)
                paper.keywords = "; ".join(raw_kws)
        except Exception:
            pass

    def _go_to_next_page(self) -> bool:
        """Click the 'Next' pagination button. Returns False if absent."""
        try:
            next_btn = self.page.query_selector(_NEXT_PAGE_SELECTOR)
            if next_btn and next_btn.is_enabled():
                next_btn.click()
                time.sleep(2)
                self.page.wait_for_selector(
                    _RESULT_ITEM_SELECTOR, timeout=20_000
                )
                return True
        except (PlaywrightTimeout, Exception) as exc:
            logger.debug(f"Next page navigation failed: {exc}")
        return False

    def _abs_url(self, href: str, page: Optional[Page] = None) -> str:
        """Resolve a relative IEEE URL to absolute."""
        if href.startswith("http"):
            return href
        p = page or self.page
        origin = p.evaluate("window.location.origin")
        return f"{origin}{href}" if href.startswith("/") else f"{origin}/{href}"
