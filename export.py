"""
export.py
─────────
Utilities for exporting PaperRecord lists to CSV.
Supports both one-shot export and gradual/incremental row-by-row writing
with immediate flushing to ensure progress is never lost on interruption.
"""

import csv
import logging
import re
from pathlib import Path
from typing import List, Optional

from scrapers.base import PaperRecord

logger = logging.getLogger(__name__)


def sanitize_filename(name: str, max_length: int = 50) -> str:
    """
    Sanitize a string so it is valid and safe as a filename across all OSes
    (specifically Windows which rejects < > : " / \\ | ? * and control chars).
    """
    # Replace illegal filename characters and whitespace with underscores
    clean = re.sub(r'[\s/\\:*?"<>|]+', "_", name)
    # Keep only safe alphanumeric, underscores, and hyphens
    clean = re.sub(r"[^a-zA-Z0-9_\-]", "", clean)
    # Collapse consecutive underscores and strip leading/trailing separators
    clean = re.sub(r"_+", "_", clean).strip("._-")
    return clean[:max_length] or "query"


class IncrementalCSVWriter:
    """
    Gradually writes PaperRecord items to a CSV file row-by-row.
    Immediately flushes each row to disk so results are preserved
    even if the script errors or is stopped halfway.
    """

    def __init__(self, filepath: Path, resume: bool = False):
        filepath = Path(filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)

        # Extra safety safeguard: sanitize filename stem to prevent OS errors on Windows
        clean_stem = sanitize_filename(filepath.stem)
        safe_name = f"{clean_stem}{filepath.suffix or '.csv'}"
        self.filepath = filepath.parent / safe_name
        self.count = 0

        # Check if file already exists with content to resume
        file_exists = self.filepath.exists() and self.filepath.stat().st_size > 0
        mode = "a" if (resume and file_exists) else "w"
        write_header = not (resume and file_exists)

        self._file = open(self.filepath, mode, newline="", encoding="utf-8-sig")
        self._writer = csv.writer(self._file, quoting=csv.QUOTE_MINIMAL)

        if write_header:
            self._writer.writerow(PaperRecord.csv_header())
            self._file.flush()

        logger.info(f"Initialized incremental CSV storage → {self.filepath}")

    def write(self, paper: PaperRecord) -> None:
        """Sanitize paper, write row, and immediately flush to disk."""
        paper.clean()
        self._writer.writerow(paper.to_csv_row())
        self._file.flush()
        self.count += 1
        logger.debug(
            f"Flushed paper #{self.count} to disk: {paper.title[:50]}…"
        )

    def rewrite_all(self, papers: List[PaperRecord]) -> None:
        """
        Rewrite the entire CSV file with the finalized list of PaperRecords.
        Used after rescraping missing abstracts to update any recovered data.
        """
        if hasattr(self, "_file") and not self._file.closed:
            self._file.flush()
            self._file.close()

        with open(self.filepath, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
            writer.writerow(PaperRecord.csv_header())
            for paper in papers:
                paper.clean()
                writer.writerow(paper.to_csv_row())

        self.count = len(papers)
        # Re-open in append mode in case further writes occur
        self._file = open(self.filepath, "a", newline="", encoding="utf-8-sig")
        self._writer = csv.writer(self._file, quoting=csv.QUOTE_MINIMAL)
        logger.info(
            f"✓ CSV file finalized and updated with {len(papers)} records → {self.filepath}"
        )

    def close(self) -> None:
        """Flush and close the file safely."""
        if hasattr(self, "_file") and not self._file.closed:
            self._file.flush()
            self._file.close()

    def __enter__(self) -> "IncrementalCSVWriter":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


def to_csv(papers: List[PaperRecord], filepath: Path) -> Path:
    """
    Write a list of PaperRecords to a CSV file.
    Returns the absolute path of the written file.
    """
    filepath = Path(filepath)
    with IncrementalCSVWriter(filepath) as writer:
        for paper in papers:
            writer.write(paper)

    logger.info(f"✓ Exported {len(papers)} papers → {filepath}")
    return filepath.resolve()


def read_csv(filepath: Path) -> List[PaperRecord]:
    """
    Read an existing CSV file into a list of PaperRecord objects.
    Useful for checking and rescraping missing abstracts.
    """
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(f"File not found: {filepath}")

    papers: List[PaperRecord] = []
    with open(filepath, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            paper = PaperRecord(
                title=row.get("title", ""),
                authors=row.get("authors", ""),
                year=row.get("year", ""),
                abstract=row.get("abstract", ""),
                keywords=row.get("keywords", ""),
                publication_title=row.get("publication_title", ""),
                publication_type=row.get("publication_type", ""),
                publisher=row.get("publisher", ""),
                volume=row.get("volume", ""),
                issue=row.get("issue", ""),
                pages=row.get("pages", ""),
                doi=row.get("doi", ""),
                url=row.get("url", ""),
                pdf_url=row.get("pdf_url", ""),
                citation_count=row.get("citation_count", ""),
                ieee_access_type=row.get("ieee_access_type", ""),
                source_database=row.get("source_database", "IEEE Xplore"),
            )
            paper.clean()
            papers.append(paper)

    logger.info(f"Loaded {len(papers)} records from CSV → {filepath}")
    return papers

