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
    """Lowercase, strip accents/punctuation for fuzzy de-duplication.
    Punctuation and whitespace (including embedded newlines from multi-line
    CSV fields) are collapsed to single spaces rather than deleted outright,
    so words don't get fused together across a line break."""
    t = unicodedata.normalize("NFKD", title or "")
    t = "".join(c for c in t if not unicodedata.combining(c))  # strip accents
    t = t.lower()
    t = re.sub(r"[^a-z0-9]+", " ", t)  # any non-alnum run (incl. newlines) -> one space
    return t.strip()


_PDF_LINK_PATTERN = re.compile(r"\(([^()]*\.pdf)\)", re.IGNORECASE)


def load_existing(csv_path: Path):
    """Return (set_of_normalized_titles, fieldnames, list_of_rows, set_of_already_used_pdf_paths)."""
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames
    existing_titles = {normalize_title(r["title"]) for r in rows}
    used_pdfs = set()
    for r in rows:
        for m in _PDF_LINK_PATTERN.findall(r.get("links", "") or ""):
            used_pdfs.add(Path(m.strip()).name)  # basename only -- robust to path-format differences
    return existing_titles, fieldnames, rows, used_pdfs


def fetch_author_papers(author_id: str, session: requests.Session):
    url = f"{S2_API}/author/{author_id}/papers"
    params = {"fields": FIELDS, "limit": 500}
    resp = session.get(url, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json().get("data", [])


_CORRECTION_KEYWORDS = [
    "erratum", "corrigendum", "correction to", "correction:",
    "retraction of", "retracted:", "retraction:", "retraction note",
    "author-initiated retraction",
]

# Anchored patterns (checked against the START of the lowercased title) that
# indicate an OSF/dataset/materials record rather than a paper -- these get
# indexed by Semantic Scholar as "papers" co-authored by whoever uploaded
# them, but they're supplementary data/code/task files, not publications.
_MATERIALS_TITLE_PATTERNS = [
    re.compile(r"^data:\s"),
    re.compile(r"^code:\s"),
    re.compile(r"^analysis[\s\-]"),
    re.compile(r"^open data\b"),
    re.compile(r"^task code:"),
    re.compile(r"^experiment\s*\d"),  # "Experiment 1a:", "Experiment 2 (inter-mixed):"
]
_MATERIALS_SUBSTRINGS = ["raw data & experiment scripts", "experiment scripts"]

_ACKNOWLEDGMENT_PATTERN = re.compile(r"^acknowledg[e]?ment\b")


def is_manually_excluded(title: str, excluded_titles_normalized: set) -> bool:
    """Check a paper's normalized title against a manually curated exclusion
    list (config's `manual_exclude_titles`) -- for one-off cases no automated
    rule can catch, like a paper that was later retracted but whose own
    title carries no hint of that."""
    return normalize_title(title) in excluded_titles_normalized


def classify_paper(paper, preprint_keywords):
    """
    Return None if the paper looks like a finished, peer-reviewed piece.
    Otherwise return a short string reason explaining why it's being excluded:
    'preprint', 'conference-only', or 'correction/erratum'.
    """
    title = (paper.get("title") or "").lower()
    if any(kw in title for kw in _CORRECTION_KEYWORDS):
        # Corrections/errata aren't standalone papers -- your existing rows
        # attach them as a link on the original entry (e.g. the Adam 2017
        # row's "Corrigendum" link), not as their own row. Flag for you to
        # attach by hand rather than adding as a new "publication."
        return "correction/erratum"

    stripped_title = title.strip()
    if any(p.match(stripped_title) for p in _MATERIALS_TITLE_PATTERNS) or \
       any(sub in title for sub in _MATERIALS_SUBSTRINGS):
        return "dataset/materials"

    if _ACKNOWLEDGMENT_PATTERN.match(stripped_title):
        return "reviewer-acknowledgment"

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


def _paper_surname(paper) -> str:
    authors = paper.get("authors") or []
    if not authors:
        return ""
    name = (authors[0].get("name") or "").strip()
    if not name:
        return ""
    return re.sub(r"[^a-z0-9]", "", name.split()[-1].lower())


def _is_jov_abstract_venue(venue: str) -> bool:
    """Journal of Vision (bare, no article DOI) is this lab's VSS conference
    abstract venue -- when it duplicates something else in the same batch,
    the abstract is the one to drop, not the full paper."""
    return (venue or "").strip().lower() == "journal of vision"


def dedupe_candidates_within_batch(candidates, year_window=2, min_overlap=3, min_ratio=0.5):
    """Two different Semantic Scholar records fetched in the SAME run can
    both be 'new' (neither is in the existing CSV yet) and still be the same
    underlying study -- e.g. a VSS abstract and its eventual full paper both
    showing up for the first time together. find_fuzzy_duplicate only checks
    new-vs-existing; this checks new-vs-new. Returns (kept_dict, dropped_dict)
    where dropped values are (paper, reason_str)."""
    items = list(candidates.items())
    dropped = {}
    live = set(candidates.keys())

    for i in range(len(items)):
        key_i, paper_i = items[i]
        if key_i in dropped or key_i not in live:
            continue
        surname_i = _paper_surname(paper_i)
        year_i = paper_i.get("year")
        kw_i = set(title_keywords(paper_i.get("title", "")))
        if not surname_i or year_i is None or not kw_i:
            continue

        for j in range(i + 1, len(items)):
            key_j, paper_j = items[j]
            if key_j in dropped or key_j not in live:
                continue
            if _paper_surname(paper_j) != surname_i:
                continue
            year_j = paper_j.get("year")
            if year_j is None or abs(year_j - year_i) > year_window:
                continue
            kw_j = set(title_keywords(paper_j.get("title", "")))
            if not kw_j:
                continue
            overlap = len(kw_i & kw_j)
            ratio = overlap / min(len(kw_i), len(kw_j))
            if overlap < min_overlap or ratio < min_ratio:
                continue

            # Same underlying study. Decide which to keep:
            # prefer to drop the Journal-of-Vision abstract over a real venue;
            # otherwise prefer whichever has a DOI; otherwise keep the first.
            venue_i, venue_j = paper_i.get("venue", ""), paper_j.get("venue", "")
            if _is_jov_abstract_venue(venue_i) and not _is_jov_abstract_venue(venue_j):
                drop_key, keep_key = key_i, key_j
            elif _is_jov_abstract_venue(venue_j) and not _is_jov_abstract_venue(venue_i):
                drop_key, keep_key = key_j, key_i
            else:
                has_doi_i = bool(paper_i.get("externalIds", {}).get("DOI"))
                has_doi_j = bool(paper_j.get("externalIds", {}).get("DOI"))
                if has_doi_j and not has_doi_i:
                    drop_key, keep_key = key_i, key_j
                else:
                    drop_key, keep_key = key_j, key_i

            kept_title = candidates[keep_key].get("title", "")
            dropped[drop_key] = (
                candidates[drop_key],
                f"duplicate of another new entry in this same batch: \"{kept_title}\"",
            )
            live.discard(drop_key)

    kept = {k: v for k, v in candidates.items() if k in live}
    return kept, dropped


def first_author_surname(authors_field: str) -> str:
    """Extract the first author's surname from the CSV's formatted authors
    string, e.g. 'Sutterer DW, Foster JJ & Awh E' -> 'sutterer'."""
    first = re.split(r"[,&]", authors_field or "")[0].strip()
    tokens = first.split()
    return tokens[0].lower() if tokens else ""


def build_existing_index(rows):
    """Pre-compute (surname, year, keyword_set) for each existing row, for
    fuzzy duplicate detection against new candidates."""
    index = []
    for r in rows:
        try:
            year = int(r.get("year") or 0)
        except ValueError:
            year = 0
        surname = first_author_surname(r.get("authors", ""))
        kws = set(title_keywords(r.get("title", "")))
        if surname and kws:
            index.append((surname, year, kws, r.get("title", "")))
    return index


def find_fuzzy_duplicate(paper, existing_index, year_window=2, min_overlap=3, min_ratio=0.5):
    """Check whether a candidate paper is likely the same study as an
    existing row under a different title -- the classic case being a VSS/
    conference abstract (working title) vs. the eventual published paper.
    Returns the matched existing title if found, else None."""
    authors = paper.get("authors") or []
    if not authors:
        return None
    surname = re.sub(r"[^a-z0-9]", "", (authors[0].get("name") or "").split()[-1].lower()) if authors[0].get("name") else ""
    year = paper.get("year")
    kws = set(title_keywords(paper.get("title", "")))
    if not surname or not year or not kws:
        return None

    for ex_surname, ex_year, ex_kws, ex_title in existing_index:
        if ex_surname != surname or abs(ex_year - year) > year_window:
            continue
        overlap = len(kws & ex_kws)
        ratio = overlap / min(len(kws), len(ex_kws))
        if overlap >= min_overlap and ratio >= min_ratio:
            return ex_title
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


DEFAULT_PDF_EXCLUDE_KEYWORDS = ["resume", "_cv", "cv_", "vitae"]


def scan_pdf_dir(pdf_dir: Path, exclude_keywords=None, already_used=None):
    """Return a list of (url_path, normalized_filename) for every PDF found,
    excluding: files matching exclude_keywords (resumes/CVs), and files
    already referenced in an existing CSV row (already_used, a set of the
    literal path strings as written in those links) -- a PDF that's already
    correctly attached to one paper should never be up for grabs for a
    different one."""
    exclude_keywords = exclude_keywords or DEFAULT_PDF_EXCLUDE_KEYWORDS
    already_used = already_used or set()
    if not pdf_dir.is_dir():
        return []
    results = []
    for f in pdf_dir.rglob("*.pdf"):
        lower_name = f.name.lower()
        if any(kw in lower_name for kw in exclude_keywords):
            continue
        url = "/" + f.as_posix()
        if f.name in already_used:
            continue
        norm = re.sub(r"[^a-z0-9]", "", lower_name)
        results.append((url, norm))
    return results


_STOPWORDS = {
    "the", "and", "for", "with", "from", "into", "during", "while", "using",
    "a", "an", "of", "in", "on", "is", "are", "to", "as", "by", "at", "or",
    "visual", "working", "memory", "attention", "cognitive", "neural",
}


def title_keywords(title: str):
    """Distinctive words from a title, for fuzzy-matching against filename
    fragments. Deliberately strips out words too generic to discriminate
    between this lab's papers (e.g. 'visual', 'working', 'memory')."""
    words = re.sub(r"[^a-z0-9 ]", " ", (title or "").lower()).split()
    return [w for w in words if len(w) > 3 and w not in _STOPWORDS]


def find_matching_pdfs(paper, pdf_files):
    """
    Fuzzy-match a paper to existing PDF filenames by first-author surname + year.
    If that yields multiple candidates, break the tie using distinctive title
    words that appear in the filename (several of this lab's PDFs are named
    with a title fragment rather than a journal abbreviation, e.g.
    'hakim_2018_phase-coding.pdf'). Returns a list: 0 = no match, 1 = confident
    match (whether from surname+year alone or after a clean title tiebreak),
    2+ = still ambiguous even after the tiebreak -- caller flags for review.
    """
    authors = paper.get("authors") or []
    if not authors:
        return []
    first_author_name = (authors[0].get("name") or "").strip()
    if not first_author_name:
        return []
    surname = re.sub(r"[^a-z0-9]", "", first_author_name.split()[-1].lower())
    year = str(paper.get("year", ""))
    if not surname or not year:
        return []

    matches = [url for url, norm in pdf_files if surname in norm and year in norm]
    if len(matches) <= 1:
        return matches

    # Tie-break using title keywords against the filename.
    norm_by_url = {url: norm for url, norm in pdf_files}
    keywords = title_keywords(paper.get("title", ""))
    if not keywords:
        return matches

    scored = [(url, sum(1 for kw in keywords if kw in norm_by_url[url])) for url in matches]
    scored.sort(key=lambda x: x[1], reverse=True)
    top_score = scored[0][1]
    if top_score > 0 and sum(1 for _, s in scored if s == top_score) == 1:
        return [scored[0][0]]  # unique winner after tiebreak
    return matches  # still tied -- leave as ambiguous


def format_links(paper, pdf_match=None):
    links = []
    doi = paper.get("externalIds", {}).get("DOI")
    if doi:
        links.append(f"[Link](https://doi.org/{doi})")
    elif paper.get("url"):
        links.append(f"[Link]({paper['url']})")
    if pdf_match:
        links.append(f"[PDF]({pdf_match})")
    return " \\| ".join(links) if links else ""


def build_row(paper, lab_surnames, pdf_match=None):
    return {
        "authors": format_authors(paper.get("authors", []), lab_surnames),
        "year": paper.get("year", ""),
        "title": paper.get("title", "").strip(),
        "publication": f"<i>{paper.get('venue', '').strip()}</i>" if paper.get("venue") else "",
        "links": format_links(paper, pdf_match),
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
    ap.add_argument(
        "--pdf-dir",
        default="files/pdfs",
        help="Directory of existing PDFs to fuzzy-match against new papers",
    )
    args = ap.parse_args()

    csv_path = Path(args.csv)
    config = yaml.safe_load(Path(args.config).read_text())
    author_ids = config["semantic_scholar_author_ids"]
    lab_surnames = set(config.get("lab_surnames", []))
    min_year = config.get("min_year", 2020)
    preprint_keywords = [kw.lower() for kw in config.get("preprint_keywords", DEFAULT_PREPRINT_KEYWORDS)]
    manual_exclude_titles = {normalize_title(t) for t in config.get("manual_exclude_titles", [])}

    existing_titles, fieldnames, rows, used_pdfs = load_existing(csv_path)
    existing_index = build_existing_index(rows)
    pdf_files = scan_pdf_dir(Path(args.pdf_dir), already_used=used_pdfs)
    print(f"Found {len(pdf_files)} unclaimed PDF(s) under {args.pdf_dir} ({len(used_pdfs)} already linked from existing rows)")

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
            if key in manual_exclude_titles:
                excluded[key] = (p, "manually excluded")
                continue

            dup_of = find_fuzzy_duplicate(p, existing_index)
            if dup_of:
                excluded[key] = (p, f"possible duplicate of existing entry: \"{dup_of}\"")
                continue

            reason = classify_paper(p, preprint_keywords)
            if reason:
                excluded[key] = (p, reason)
                continue

            # de-dup across the two PIs' paper lists
            candidates[key] = p
        time.sleep(1)  # be polite to the API

    # Catch duplicates where BOTH the abstract and the full paper are new
    # in this same run -- these can't be caught by the existing-CSV check
    # above since neither is in the CSV yet.
    candidates, batch_dupes = dedupe_candidates_within_batch(candidates)
    excluded.update(batch_dupes)

    if not candidates and not excluded:
        print("No new publications found.")
        Path(args.draft_out).write_text("No new publications found this run.\n")
        return

    if not candidates:
        print(f"No new finished papers found ({len(excluded)} preprint/conference/correction item(s) skipped).")
        lines = ["## No new finished publications found\n",
                 f"{len(excluded)} item(s) were skipped as preprints, conference-only entries, or corrections/errata:\n"]
        for p, reason in excluded.values():
            lines.append(f"- ({reason}) {p.get('year', '?')} — {p.get('title', '')}")
        Path(args.draft_out).write_text("\n".join(lines) + "\n")
        return

    new_rows = []
    ambiguous_pdfs = []  # (paper, list_of_candidate_paths)
    remaining_pdfs = list(pdf_files)
    for p in sorted(candidates.values(), key=lambda p: p.get("year", 0), reverse=True):
        matches = find_matching_pdfs(p, remaining_pdfs)
        if len(matches) == 1:
            new_rows.append(build_row(p, lab_surnames, pdf_match=matches[0]))
            remaining_pdfs = [(url, norm) for url, norm in remaining_pdfs if url != matches[0]]
        else:
            if len(matches) > 1:
                ambiguous_pdfs.append((p, matches))
            new_rows.append(build_row(p, lab_surnames))
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
    if ambiguous_pdfs:
        summary_lines.append(f"\n_Found multiple possible PDF matches for {len(ambiguous_pdfs)} paper(s) -- add the right link by hand:_\n")
        for p, matches in ambiguous_pdfs:
            summary_lines.append(f"- {p.get('title', '')}: candidates {', '.join(matches)}")
    if excluded:
        summary_lines.append(f"\n_Also skipped {len(excluded)} preprint/conference/correction item(s) -- check these weren't wrongly excluded:_\n")
        for p, reason in excluded.values():
            summary_lines.append(f"- ({reason}) {p.get('year', '?')} — {p.get('title', '')}")
    Path(args.draft_out).write_text("\n".join(summary_lines) + "\n")
    print(f"Added {len(new_rows)} new row(s). See {args.draft_out} for summary.")


if __name__ == "__main__":
    main()
