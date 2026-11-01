"""
scrapers/base.py
────────────────
Abstract base class for all journal/database scrapers.
New sources (Scopus, SpringerLink, etc.) should subclass BaseScraper.
"""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, fields, asdict
from typing import Any, Callable, List, Optional
from playwright.sync_api import Page


# ── Text Cleaning Utilities ──────────────────────────────────────────

BOILERPLATE_KEYWORDS = {
    "learn more",
    "learn more.",
    "ieee keywords",
    "author keywords",
    "index terms",
    "keywords",
    "inspec: non-controlled indexing",
    "inspec: controlled indexing",
}


def clean_text(val: Any) -> str:
    """
    Sanitize text to prevent breaking CSV parsers:
    - Normalizes non-breaking spaces and smart quotes.
    - Replaces internal newlines (\r\n, \r, \n) and tabs (\t) with spaces.
    - Removes unprintable / ASCII control characters (0-31, 127).
    - Collapses multiple whitespace into a single space.
    - Strips leading and trailing whitespace.
    """
    if val is None:
        return ""
    text = str(val)
    # Normalize non-breaking spaces & zero-width spaces
    text = text.replace("\xa0", " ").replace("\u200b", "").replace("\ufeff", "")
    # Normalize curly/smart quotes & apostrophes
    text = (
        text.replace("“", '"')
        .replace("”", '"')
        .replace("‘", "'")
        .replace("’", "'")
        .replace("«", '"')
        .replace("»", '"')
    )
    # Replace newlines, carriage returns, and tabs with a space
    text = re.sub(r"[\r\n\t]+", " ", text)
    # Remove control characters except standard printable
    text = re.sub(r"[\x00-\x1f\x7f]", "", text)
    # Collapse multiple spaces into one
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def clean_title(title: str) -> str:
    """Clean paper title and strip outer wrapping quotes or HTML tags."""
    text = clean_text(title)
    # Strip HTML tags like <i>, </i>, <sub>, <sup>
    text = re.sub(r"<[^>]+>", "", text)
    # Strip surrounding quotes if wrapped
    if (text.startswith('"') and text.endswith('"')) or (
        text.startswith("'") and text.endswith("'")
    ):
        text = text[1:-1].strip()
    return clean_text(text)


def clean_authors(authors: str) -> str:
    """
    Clean authors string:
    - Splits by semicolon, newline, or pipe.
    - Trims extra whitespace/commas.
    - Removes duplicates while preserving author order.
    - Joins with '; '.
    """
    if not authors:
        return ""
    parts = re.split(r"[;\n\r|]+", authors)
    seen = set()
    cleaned = []
    for part in parts:
        name = clean_text(part).strip(",. ")
        if not name:
            continue
        key = name.lower()
        if key not in seen:
            seen.add(key)
            cleaned.append(name)
    return "; ".join(cleaned)


def clean_keywords(keywords: str) -> str:
    """
    Clean keywords string:
    - Splits by semicolon or newline.
    - Filters out boilerplate ('Learn more.', 'IEEE Keywords', etc.).
    - Deduplicates case-insensitively while preserving original case and order.
    - Joins with '; '.
    """
    if not keywords:
        return ""
    parts = re.split(r"[;\n\r|]+", keywords)
    seen = set()
    cleaned = []
    for part in parts:
        kw = clean_text(part).strip(",. ")
        if not kw:
            continue
        if kw.lower() in BOILERPLATE_KEYWORDS:
            continue
        key = kw.lower()
        if key not in seen:
            seen.add(key)
            cleaned.append(kw)
    return "; ".join(cleaned)


def clean_abstract(abstract: str) -> str:
    """
    Clean abstract string:
    - Strips leading 'Abstract:' or 'ABSTRACT:' prefixes.
    - Replaces internal newlines with spaces (keeps whole abstract on 1 CSV line).
    - Collapses multiple spaces.
    """
    text = clean_text(abstract)
    text = re.sub(r"^(?:abstract|synopsis)\s*:\s*", "", text, flags=re.IGNORECASE)
    return text.strip()


def clean_year(year: str) -> str:
    """Extract a clean 4-digit publication year (e.g. 2024)."""
    text = clean_text(year)
    match = re.search(r"\b(19|20)\d{2}\b", text)
    return match.group(0) if match else text


def clean_doi(doi: str) -> str:
    """
    Sanitize and normalize DOI string to standard https://doi.org/10.xxxx format.
    """
    text = clean_text(doi)
    if "javascript:" in text.lower():
        return ""
    # Strip leading "DOI:" or "doi:"
    text = re.sub(r"^(?:doi\s*:\s*)+", "", text, flags=re.IGNORECASE).strip()
    # Find canonical DOI pattern: 10.<prefix>/<suffix>
    match = re.search(r"\b10\.\d{4,9}/[^\s]+", text)
    if match:
        extracted = match.group(0).rstrip(".,;)")
        return f"https://doi.org/{extracted}"
    if text.startswith("http"):
        return text
    return text


def clean_url(url: str) -> str:
    """Sanitize URL and filter dummy links like javascript:void()."""
    text = clean_text(url)
    if "javascript:" in text.lower():
        return ""
    return text


def clean_citation_count(cite: str) -> str:
    """Extract numeric citation count."""
    text = clean_text(cite)
    match = re.search(r"\d+", text)
    return match.group(0) if match else ""


# ── PaperRecord Data Structure ────────────────────────────────────────

@dataclass
class PaperRecord:
    """Canonical representation of a paper for SLR purposes."""

    title: str = ""
    authors: str = ""                   # semicolon-separated
    year: str = ""
    abstract: str = ""
    keywords: str = ""                  # semicolon-separated
    publication_title: str = ""         # journal / conference / proceedings name
    publication_type: str = ""          # e.g. "Conference", "Journal", "Early Access"
    publisher: str = ""
    volume: str = ""
    issue: str = ""
    pages: str = ""                     # e.g. "1-6"
    doi: str = ""
    url: str = ""                       # direct link to the paper
    pdf_url: str = ""
    citation_count: str = ""
    ieee_access_type: str = ""          # e.g. "Open Access", "Subscription"
    source_database: str = ""           # e.g. "IEEE Xplore"

    @classmethod
    def csv_header(cls) -> List[str]:
        return [f.name for f in fields(cls)]

    def clean(self) -> "PaperRecord":
        """
        Sanitize all fields in-place to guarantee safe, single-line CSV output.
        """
        self.title = clean_title(self.title)
        self.authors = clean_authors(self.authors)
        self.year = clean_year(self.year)
        self.abstract = clean_abstract(self.abstract)
        self.keywords = clean_keywords(self.keywords)
        self.publication_title = clean_text(self.publication_title)
        self.publication_type = clean_text(self.publication_type)
        self.publisher = clean_text(self.publisher)
        self.volume = clean_text(self.volume)
        self.issue = clean_text(self.issue)
        self.pages = clean_text(self.pages)
        self.doi = clean_doi(self.doi)
        self.url = clean_url(self.url)
        self.pdf_url = clean_url(self.pdf_url)
        self.citation_count = clean_citation_count(self.citation_count)
        self.ieee_access_type = clean_text(self.ieee_access_type)
        self.source_database = clean_text(self.source_database)
        return self

    def to_dict(self) -> dict:
        self.clean()
        return asdict(self)

    def to_csv_row(self) -> List[str]:
        self.clean()
        return [str(getattr(self, f.name, "")) for f in fields(self)]


class BaseScraper(ABC):
    """
    Abstract scraper that every source must implement.
    """

    name: str = "base"

    def __init__(self, page: Page):
        self.page = page

    @abstractmethod
    def search(self, query: str, years: Optional[int] = None) -> None:
        """Execute a search for the given query string with optional year filter."""
        ...

    @abstractmethod
    def scrape_results(
        self,
        max_pages: int = 10,
        years: Optional[int] = None,
        on_paper_scraped: Optional[Callable[[PaperRecord], None]] = None,
    ) -> List[PaperRecord]:
        """Scrape search result pages and return a list of PaperRecords."""
        ...

    @abstractmethod
    def scrape_paper_detail(
        self, paper: PaperRecord, page: Optional[Page] = None
    ) -> PaperRecord:
        """Enrich a PaperRecord with details from its individual page."""
        ...

    @abstractmethod
    def rescrape_incomplete_papers(
        self,
        papers: List[PaperRecord],
        max_retries: int = 2,
        on_paper_updated: Optional[Callable[[PaperRecord], None]] = None,
    ) -> List[PaperRecord]:
        """Check for missing abstract, authors, or DOI and rescrape those records."""
        ...

    @abstractmethod
    def rescrape_missing_abstracts(
        self,
        papers: List[PaperRecord],
        max_retries: int = 2,
        on_paper_updated: Optional[Callable[[PaperRecord], None]] = None,
    ) -> List[PaperRecord]:
        """Check for empty abstracts and rescrape only those records."""
        ...
