#!/usr/bin/env python3
"""
update_pubs.py

Fetches recent publications for lab PIs from the Semantic Scholar Graph API,
compares them against the existing _data/pubs_table.csv, and appends
draft rows for anything new. Designed to be run by a GitHub Action that
opens a PR with the diff -- NOT to auto-publish silently.

Usage:
    python update_pubs.py --csv _data/pubs_table.csv --config pubs_config.yml
"""

import argparse
import csv
import re
import sys
import time
import unicodedata
from pathlib import Path

import requests
import yaml

S2_API = "https://api.semanticscholar.org/graph/v1"
FIELDS = "title,year,venue,externalIds,authors,url,publicationDate,publicationTypes,journal"

# Venue name fragments (checked case-insensitively) that indicate a preprint
# server rather than a peer-reviewed venue. Extend this list in
# pubs_config.yml under `preprint_keywords` if you spot others slipping through.
DEFAULT_PREPRINT_KEYWORDS = [
    "biorxiv", "medrxiv", "psyarxiv", "arxiv", "ssrn",
    "research square", "chemrxiv", "osf preprints", "preprints.org",
]


def normalize_title(title: str) -> str:
    """Lowercase, strip punctuation/whitespace for fuzzy de-duplication."""
    t = unicodedata.normalize("NFKD", title or "")
    t = re.sub(r"[^a-z0-9 ]", "", t.lower())
    t = re.sub(r"\s+", " ", t).strip()
    return t


def load_existing(csv_path: Path):
    """Return (set_of_normalized_titles, fieldnames, list_of_rows)."""
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames
    existing_titles = {normalize_title(r["title"]) for r in rows}
    return existing_titles, fieldnames, rows


def fetch_author_papers(author_id: str, session: requests.Session):
    url = f"{S2_API}/author/{author_id}/papers"
    params = {"fields": FIELDS, "limit": 500}
    resp = session.get(url, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json().get("data", [])


def classify_paper(paper, preprint_keywords):
    """
    Return None if the paper looks like a finished, peer-reviewed piece.
    Otherwise return a short string reason ('preprint' or 'conference-only')
    explaining why it's being excluded.
    """
    venue = (paper.get("venue") or "").lower()
    if any(kw in venue for kw in preprint_keywords):
        return "preprint"

    pub_types = paper.get("publicationTypes") or []
    # S2's publicationTypes is a list like ["JournalArticle"], ["Conference"],
    # ["Review"], etc. If it's explicitly tagged Conference and NOT also
    # tagged JournalArticle, treat it as a conference abstract/talk, not a
    # finished paper. If publicationTypes is empty, S2 didn't classify it --
    # don't exclude on that basis alone, since plenty of legitimate journal
    # articles come through with no tag at all.
    if pub_types and "JournalArticle" not in pub_types and "Review" not in pub_types:
        if "Conference" in pub_types:
            return "conference-only"

    return None


def format_authors(paper_authors, lab_surnames):
    """
    Best-effort author formatting: 'Last FM' per author, joined with commas
    and '&' before the last, matching the lab's existing house style
    (e.g. 'Awh E & Vogel EK'). ALWAYS double-check this against the actual
    paper -- initials from the API are not always reliable, and this does
    not preserve any '*' co-first-author markers.
    """
    formatted = []
    for a in paper_authors:
        name = a.get("name", "").strip()
        if not name:
            continue
        parts = name.split()
        if len(parts) == 1:
            formatted.append(parts[0])
            continue
        last = parts[-1]
        initials = "".join(p[0] for p in parts[:-1] if p)
        formatted.append(f"{last} {initials}")
    if len(formatted) > 1:
        return ", ".join(formatted[:-1]) + " & " + formatted[-1]
    return formatted[0] if formatted else "UNKNOWN AUTHORS"


def format_links(paper):
    links = []
    doi = paper.get("externalIds", {}).get("DOI")
    if doi:
        links.append(f"[Link](https://doi.org/{doi})")
    elif paper.get("url"):
        links.append(f"[Link]({paper['url']})")
    return " \\| ".join(links) if links else ""


def build_row(paper, lab_surnames):
    return {
        "authors": format_authors(paper.get("authors", []), lab_surnames),
        "year": paper.get("year", ""),
        "title": paper.get("title", "").strip(),
        "publication": f"<i>{paper.get('venue', '').strip()}</i>" if paper.get("venue") else "",
        "links": format_links(paper),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, help="Path to _data/pubs_table.csv")
    ap.add_argument("--config", required=True, help="Path to pubs_config.yml")
    ap.add_argument(
        "--draft-out",
        default="pending_pubs_review.md",
        help="Where to write a human-readable summary of new rows for the PR body",
    )
    args = ap.parse_args()

    csv_path = Path(args.csv)
    config = yaml.safe_load(Path(args.config).read_text())
    author_ids = config["semantic_scholar_author_ids"]
    lab_surnames = set(config.get("lab_surnames", []))
    min_year = config.get("min_year", 2020)
    preprint_keywords = [kw.lower() for kw in config.get("preprint_keywords", DEFAULT_PREPRINT_KEYWORDS)]

    existing_titles, fieldnames, rows = load_existing(csv_path)

    session = requests.Session()
    session.headers.update({"User-Agent": "AwhVogelLab-pubs-bot/1.0"})

    candidates = {}
    excluded = {}  # key: normalized title -> (paper, reason), deduped across author IDs
    for name, author_id in author_ids.items():
        try:
            papers = fetch_author_papers(author_id, session)
        except requests.HTTPError as e:
            print(f"WARNING: failed fetching papers for {name} ({author_id}): {e}", file=sys.stderr)
            continue
        for p in papers:
            if not p.get("title") or not p.get("year"):
                continue
            if p["year"] < min_year:
                continue
            key = normalize_title(p["title"])
            if key in existing_titles:
                continue

            reason = classify_paper(p, preprint_keywords)
            if reason:
                excluded[key] = (p, reason)
                continue

            # de-dup across the two PIs' paper lists
            candidates[key] = p
        time.sleep(1)  # be polite to the API

    if not candidates and not excluded:
        print("No new publications found.")
        Path(args.draft_out).write_text("No new publications found this run.\n")
        return

    if not candidates:
        print(f"No new finished papers found ({len(excluded)} preprint/conference item(s) skipped).")
        lines = ["## No new finished publications found\n",
                 f"{len(excluded)} item(s) were skipped as preprints or conference-only entries:\n"]
        for p, reason in excluded.values():
            lines.append(f"- ({reason}) {p.get('year', '?')} — {p.get('title', '')}")
        Path(args.draft_out).write_text("\n".join(lines) + "\n")
        return

    new_rows = [build_row(p, lab_surnames) for p in candidates.values()]
    new_rows.sort(key=lambda r: r.get("year", 0), reverse=True)

    # Prepend new rows (CSV appears newest-first based on existing file)
    all_rows = new_rows + rows

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)

    summary_lines = [
        "## New publications found\n",
        "Please review formatting (author initials, italics, `*` for co-first-authors, links) before merging.\n",
    ]
    for r in new_rows:
        summary_lines.append(f"- **{r['year']}** — {r['title']} ({r['authors']})")
    if excluded:
        summary_lines.append(f"\n_Also skipped {len(excluded)} preprint/conference item(s) -- check these weren't wrongly excluded:_\n")
        for p, reason in excluded.values():
            summary_lines.append(f"- ({reason}) {p.get('year', '?')} — {p.get('title', '')}")
    Path(args.draft_out).write_text("\n".join(summary_lines) + "\n")
    print(f"Added {len(new_rows)} new row(s). See {args.draft_out} for summary.")


if __name__ == "__main__":
    main()
