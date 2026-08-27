"""
Forensic Research Paper Collector
---------------------------------
Collects recent genuine research papers from PubMed/NCBI E-utilities
relevant to forensic medicine and related disciplines.

Features:
- Rolling 30-day PubMed search window
- Multiple forensic-specific searches
- PMID deduplication
- Normalized-title deduplication
- Newest-first sorting
- Maximum 30 papers
- Robust PubMed date handling
- Atomic JSON output
"""

import datetime
import json
import os
import re
import time

import requests


# ============================================================
# CONFIGURATION
# ============================================================

MAX_AGE_DAYS = 30
MAX_RESULTS = 30
SEARCH_RETMAX = 50
REQUEST_TIMEOUT = 20

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

OUTPUT_FILE = "forensic_research.json"
TEMP_FILE = "forensic_research.tmp.json"


# ============================================================
# FORENSIC PUBMED SEARCHES
# ============================================================

PUBMED_QUERIES = [
    "forensic medicine",
    "forensic pathology",
    "forensic autopsy",
    "medicolegal",
    "postmortem examination",
    "autopsy pathology",
    "virtual autopsy",
    "virtopsy",
    "postmortem CT",
    "postmortem computed tomography",
    "forensic toxicology",
    "postmortem toxicology",
    "postmortem drug analysis",
    "novel psychoactive substances forensic",
    "blood alcohol postmortem",
    "forensic genetics",
    "forensic DNA",
    "DNA profiling identification",
    "investigative genetic genealogy",
    "genetic genealogy forensic",
    "forensic anthropology",
    "skeletal identification forensic",
    "disaster victim identification",
    "mass fatality forensic",
    "human remains identification",
    "taphonomy decomposition",
    "forensic odontology",
    "forensic dentistry",
    "forensic entomology",
    "postmortem interval insects",
    "forensic imaging",
    "digital forensics",
    "forensic image analysis",
    "pattern recognition forensic",
    "forensic science",
    "postmortem imaging",
    "medico-legal forensic",
]


# ============================================================
# HELPERS
# ============================================================

STOPWORDS = {
    "the", "a", "an", "of", "in", "on", "for", "and",
    "to", "with", "after", "at", "by", "from", "or",
    "is", "are", "was", "were", "be", "been", "have",
    "has", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "clinical", "case",
    "study", "method"
}


def normalize_title(title):
    """Normalize a title for duplicate detection."""

    if not title:
        return ""

    title = title.lower()

    title = re.sub(r"https?://\S+", "", title)

    title = re.sub(r"[^a-z0-9\s]", " ", title)

    words = [
        word
        for word in title.split()
        if word not in STOPWORDS and len(word) > 2
    ]

    return " ".join(words)


def parse_pubmed_date(value):
    """
    Robustly parse common PubMed date formats.

    Examples:
        2026 Aug 25
        2026 Aug
        2026
        2026-08-25
        2026/08/25
    """

    if not value:
        return None

    value = str(value).strip()

    # ISO date
    for fmt in (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y-%m",
        "%Y/%m",
    ):
        try:
            return datetime.datetime.strptime(value, fmt).replace(
                tzinfo=datetime.timezone.utc
            )
        except ValueError:
            pass

    # PubMed style: YYYY Mon DD
    for fmt in (
        "%Y %b %d",
        "%Y %B %d",
        "%Y %b",
        "%Y %B",
        "%Y",
    ):
        try:
            return datetime.datetime.strptime(value, fmt).replace(
                tzinfo=datetime.timezone.utc
            )
        except ValueError:
            pass

    # Extract year/month/day from messy strings
    match = re.search(
        r"(\d{4})[-/\s]+([A-Za-z]{3,9}|\d{1,2})[-/\s]+(\d{1,2})",
        value
    )

    if match:
        year = int(match.group(1))
        month_value = match.group(2)
        day = int(match.group(3))

        try:
            if month_value.isdigit():
                month = int(month_value)
            else:
                month = datetime.datetime.strptime(
                    month_value[:3],
                    "%b"
                ).month

            return datetime.datetime(
                year,
                month,
                day,
                tzinfo=datetime.timezone.utc
            )

        except ValueError:
            pass

    # At minimum, accept the year
    year_match = re.search(r"\b(20\d{2})\b", value)

    if year_match:
        return datetime.datetime(
            int(year_match.group(1)),
            1,
            1,
            tzinfo=datetime.timezone.utc
        )

    return None


def format_pubmed_date(value):
    """Return YYYY-MM-DD when possible."""

    dt = parse_pubmed_date(value)

    if not dt:
        return None

    return dt.strftime("%Y-%m-%d")


def within_window(dt, now):
    """Check whether a date falls inside the rolling window."""

    if not dt:
        return False

    age_seconds = (now - dt).total_seconds()

    return (
        age_seconds >= 0
        and age_seconds <= MAX_AGE_DAYS * 86400
    )


# ============================================================
# PUBMED SEARCH
# ============================================================

def search_pubmed(query, start_date, end_date):
    """
    Search PubMed for PMIDs within the rolling date window.
    """

    search_url = f"{EUTILS_BASE}/esearch.fcgi"

    # PubMed itself performs the date filtering.
    search_term = (
        f'({query}) '
        f'AND ({start_date}[PDat] : {end_date}[PDat])'
    )

    params = {
        "db": "pubmed",
        "term": search_term,
        "retmax": SEARCH_RETMAX,
        "retmode": "json",
        "sort": "pub date",
    }

    api_key = os.environ.get("NCBI_API_KEY")

    if api_key:
        params["api_key"] = api_key

    response = requests.get(
        search_url,
        params=params,
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()

    data = response.json()

    return data.get("esearchresult", {}).get("idlist", [])


# ============================================================
# PUBMED METADATA
# ============================================================

def fetch_metadata(pmids):
    """
    Fetch PubMed metadata using ESummary.

    ESummary is deliberately used instead of relying on the
    previous EFetch JSON structure.
    """

    if not pmids:
        return []

    summary_url = f"{EUTILS_BASE}/esummary.fcgi"

    params = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "json",
    }

    api_key = os.environ.get("NCBI_API_KEY")

    if api_key:
        params["api_key"] = api_key

    response = requests.get(
        summary_url,
        params=params,
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()

    data = response.json()

    result = data.get("result", {})

    papers = []

    for pmid in pmids:

        article = result.get(str(pmid), {})

        if not article:
            continue

        title = article.get("title", "").strip()

        if not title or len(title) < 10:
            continue

        # ----------------------------------------------------
        # Authors
        # ----------------------------------------------------

        author_names = []

        for author in article.get("authors", []):
            name = author.get("name")

            if name:
                author_names.append(name)

        authors = ", ".join(author_names[:5])

        if not authors:
            authors = "N/A"

        # ----------------------------------------------------
        # Publication date
        # ----------------------------------------------------

        pubdate = article.get("pubdate")

        if not pubdate:
            pubdate = article.get("sortpubdate")

        published = format_pubmed_date(pubdate)

        # If PubMed gives no usable date, don't crash.
        # The original ESearch already applied the 30-day filter.
        if not published:
            continue

        # ----------------------------------------------------
        # Journal
        # ----------------------------------------------------

        journal = (
            article.get("fulljournalname")
            or article.get("source")
            or "PubMed"
        )

        # ----------------------------------------------------
        # URL
        # ----------------------------------------------------

        url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"

        papers.append({
            "title": title,
            "authors": authors,
            "published": published,
            "journal": journal,
            "pmid": str(pmid),
            "url": url,
        })

    return papers


# ============================================================
# MAIN COLLECTOR
# ============================================================

def run():

    now = datetime.datetime.now(datetime.timezone.utc)

    start_dt = now - datetime.timedelta(days=MAX_AGE_DAYS)

    start_date = start_dt.strftime("%Y/%m/%d")
    end_date = now.strftime("%Y/%m/%d")

    print("=" * 60)
    print("FORENSIC RESEARCH PAPER COLLECTOR")
    print("=" * 60)

    print(f"Current UTC time : {now.isoformat()}")
    print(f"Search window    : {start_date} → {end_date}")
    print(f"Maximum papers   : {MAX_RESULTS}")
    print()

    # --------------------------------------------------------
    # Search all queries
    # --------------------------------------------------------

    all_pmids = set()

    successful_queries = 0
    failed_queries = 0

    for index, query in enumerate(PUBMED_QUERIES, 1):

        print(
            f"[{index}/{len(PUBMED_QUERIES)}] "
            f"Searching: {query}"
        )

        try:

            pmids = search_pubmed(
                query,
                start_date,
                end_date
            )

            if pmids:

                print(
                    f"  ✓ PubMed returned "
                    f"{len(pmids)} results"
                )

                before = len(all_pmids)

                all_pmids.update(pmids)

                new_count = len(all_pmids) - before

                print(
                    f"    New unique PMIDs: "
                    f"{new_count}"
                )

            else:

                print("  – No results")

            successful_queries += 1

        except Exception as exc:

            failed_queries += 1

            print(
                f"  ✗ Query failed: {exc}"
            )

        # Respect PubMed rate limits.
        time.sleep(0.35)

    print()
    print(f"Successful queries     : {successful_queries}")
    print(f"Failed queries         : {failed_queries}")
    print(f"Unique PMIDs collected : {len(all_pmids)}")
    print()

    # --------------------------------------------------------
    # Metadata
    # --------------------------------------------------------

    pmid_list = list(all_pmids)

    papers = []

    # Process in manageable chunks.
    chunk_size = 200

    for start in range(0, len(pmid_list), chunk_size):

        chunk = pmid_list[start:start + chunk_size]

        print(
            f"Fetching metadata "
            f"{start + 1}-{start + len(chunk)} "
            f"of {len(pmid_list)}..."
        )

        try:

            chunk_papers = fetch_metadata(chunk)

            papers.extend(chunk_papers)

            print(
                f"  ✓ Metadata received: "
                f"{len(chunk_papers)}"
            )

        except Exception as exc:

            print(
                f"  ✗ Metadata request failed: "
                f"{exc}"
            )

        time.sleep(0.35)

    print()
    print(f"Total metadata records : {len(papers)}")

    # --------------------------------------------------------
    # Date validation
    # --------------------------------------------------------

    recent_papers = []

    for paper in papers:

        dt = parse_pubmed_date(
            paper.get("published")
        )

        if dt and within_window(dt, now):

            paper["_dt"] = dt

            recent_papers.append(paper)

    print(
        f"Within {MAX_AGE_DAYS}-day window : "
        f"{len(recent_papers)}"
    )

    # --------------------------------------------------------
    # Sort newest first
    # --------------------------------------------------------

    recent_papers.sort(
        key=lambda p: p["_dt"],
        reverse=True
    )

    # --------------------------------------------------------
    # Deduplication
    # --------------------------------------------------------

    final_papers = []

    seen_pmids = set()
    seen_titles = set()

    duplicate_pmids = 0
    duplicate_titles = 0

    for paper in recent_papers:

        pmid = paper.get("pmid")
        title = paper.get("title", "")

        # PMID duplicate
        if pmid in seen_pmids:

            duplicate_pmids += 1
            continue

        # Title duplicate
        normalized = normalize_title(title)

        if normalized in seen_titles:

            duplicate_titles += 1
            continue

        seen_pmids.add(pmid)
        seen_titles.add(normalized)

        final_papers.append(paper)

        if len(final_papers) >= MAX_RESULTS:
            break

    # --------------------------------------------------------
    # Remove internal datetime
    # --------------------------------------------------------

    output_items = []

    for paper in final_papers:

        output_items.append({
            "title": paper["title"],
            "authors": paper["authors"],
            "published": paper["published"],
            "journal": paper["journal"],
            "pmid": paper["pmid"],
            "url": paper["url"],
        })

    # --------------------------------------------------------
    # Summary
    # --------------------------------------------------------

    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)

    print(
        f"Unique PMIDs          : {len(all_pmids)}"
    )

    print(
        f"Metadata records      : {len(papers)}"
    )

    print(
        f"Recent papers         : {len(recent_papers)}"
    )

    print(
        f"Duplicate PMIDs       : {duplicate_pmids}"
    )

    print(
        f"Duplicate titles      : {duplicate_titles}"
    )

    print(
        f"FINAL ACCEPTED PAPERS : {len(output_items)}"
    )

    # --------------------------------------------------------
    # Output JSON
    # --------------------------------------------------------

    output = {
        "updated": now.isoformat().replace(
            "+00:00",
            "Z"
        ),
        "source": "PubMed",
        "items": output_items,
    }

    try:

        with open(
            TEMP_FILE,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                output,
                file,
                indent=2,
                ensure_ascii=False
            )

        os.replace(
            TEMP_FILE,
            OUTPUT_FILE
        )

        print()
        print(
            f"✓ Written {OUTPUT_FILE} "
            f"({len(output_items)} items)"
        )

    except Exception as exc:

        print(
            f"✗ Failed to write output: {exc}"
        )

        if os.path.exists(TEMP_FILE):
            os.remove(TEMP_FILE)

        raise

    # --------------------------------------------------------
    # Fail only if PubMed itself failed
    # --------------------------------------------------------

    if failed_queries == len(PUBMED_QUERIES):

        raise RuntimeError(
            "All PubMed queries failed. "
            "PubMed may be unavailable."
        )

    print()
    print("✓ COLLECTION COMPLETE")


if __name__ == "__main__":
    run()
