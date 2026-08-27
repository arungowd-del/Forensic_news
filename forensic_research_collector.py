"""
Forensic Research Paper Collector

Collects recent genuine research papers from PubMed/NCBI E-utilities
relevant to forensic medicine and related disciplines.

Features:
- Rolling 30-day publication window
- Multiple forensic-specific PubMed searches
- PubMed ESearch + ESummary JSON API
- PMID deduplication
- Normalized-title deduplication
- Newest-first sorting
- Maximum 30 papers
- Atomic JSON write
- Fails the workflow if PubMed is completely unavailable
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

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

OUTPUT_FILE = "forensic_research.json"

REQUEST_TIMEOUT = 20

NCBI_API_KEY = os.environ.get("NCBI_API_KEY", "").strip()


# ============================================================
# FORENSIC RESEARCH QUERIES
# ============================================================

PUBMED_QUERIES = [

    # --------------------------------------------------------
    # FORENSIC MEDICINE / PATHOLOGY
    # --------------------------------------------------------

    '"forensic medicine"[Title/Abstract]',
    '"forensic pathology"[Title/Abstract]',
    '"forensic autopsy"[Title/Abstract]',
    '"medicolegal"[Title/Abstract]',
    '"postmortem examination"[Title/Abstract]',
    '"autopsy"[Title/Abstract] AND forensic',

    # --------------------------------------------------------
    # POSTMORTEM IMAGING / VIRTopsy
    # --------------------------------------------------------

    '"virtual autopsy"[Title/Abstract]',
    '"virtopsy"[Title/Abstract]',
    '"postmortem CT"[Title/Abstract]',
    '"postmortem computed tomography"[Title/Abstract]',
    '"postmortem imaging"[Title/Abstract]',
    '"postmortem MRI"[Title/Abstract]',

    # --------------------------------------------------------
    # FORENSIC TOXICOLOGY
    # --------------------------------------------------------

    '"forensic toxicology"[Title/Abstract]',
    '"postmortem toxicology"[Title/Abstract]',
    '"postmortem drug"[Title/Abstract] AND toxicology',
    '"novel psychoactive substances"[Title/Abstract] AND forensic',
    '"blood alcohol"[Title/Abstract] AND forensic',

    # --------------------------------------------------------
    # FORENSIC GENETICS / DNA
    # --------------------------------------------------------

    '"forensic genetics"[Title/Abstract]',
    '"forensic DNA"[Title/Abstract]',
    '"DNA profiling"[Title/Abstract] AND forensic',
    '"investigative genetic genealogy"[Title/Abstract]',
    '"genetic genealogy"[Title/Abstract] AND forensic',

    # --------------------------------------------------------
    # FORENSIC ANTHROPOLOGY / HUMAN REMAINS
    # --------------------------------------------------------

    '"forensic anthropology"[Title/Abstract]',
    '"human remains"[Title/Abstract] AND forensic',
    '"skeletal identification"[Title/Abstract]',
    '"forensic identification"[Title/Abstract]',
    '"taphonomy"[Title/Abstract] AND forensic',

    # --------------------------------------------------------
    # FORENSIC ODONTOLOGY
    # --------------------------------------------------------

    '"forensic odontology"[Title/Abstract]',
    '"forensic dentistry"[Title/Abstract]',
    '"dental identification"[Title/Abstract] AND forensic',

    # --------------------------------------------------------
    # FORENSIC ENTOMOLOGY
    # --------------------------------------------------------

    '"forensic entomology"[Title/Abstract]',
    '"postmortem interval"[Title/Abstract] AND entomology',
    '"insect colonization"[Title/Abstract] AND decomposition',

    # --------------------------------------------------------
    # FORENSIC SCIENCE / DIGITAL FORENSICS
    # --------------------------------------------------------

    '"forensic science"[Title/Abstract]',
    '"digital forensics"[Title/Abstract]',
    '"forensic imaging"[Title/Abstract]',
    '"forensic image analysis"[Title/Abstract]',
]


# ============================================================
# TITLE NORMALIZATION
# ============================================================

STOPWORDS = {
    "the",
    "a",
    "an",
    "of",
    "in",
    "on",
    "for",
    "and",
    "to",
    "with",
    "after",
    "at",
    "by",
    "from",
    "or",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "have",
    "has",
    "do",
    "does",
    "did",
    "will",
    "would",
    "could",
    "should",
    "may",
    "might",
    "can",
    "clinical",
    "case",
    "study",
    "method",
}


def normalize_title(title):
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


# ============================================================
# DATE HELPERS
# ============================================================

def parse_pubmed_date(article):
    """
    Extract a publication date from PubMed ESummary.

    ESummary commonly provides:
    pubdate
    sortpubdate
    epubdate
    """

    candidates = [
        article.get("pubdate"),
        article.get("sortpubdate"),
        article.get("epubdate"),
    ]

    for value in candidates:

        if not value:
            continue

        value = str(value).strip()

        # YYYY-MM-DD
        match = re.search(
            r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b",
            value,
        )

        if match:
            try:
                return datetime.datetime(
                    int(match.group(1)),
                    int(match.group(2)),
                    int(match.group(3)),
                    tzinfo=datetime.timezone.utc,
                )
            except ValueError:
                pass

        # YYYY MM DD
        match = re.search(
            r"\b(20\d{2})\s+(\d{1,2})\s+(\d{1,2})\b",
            value,
        )

        if match:
            try:
                return datetime.datetime(
                    int(match.group(1)),
                    int(match.group(2)),
                    int(match.group(3)),
                    tzinfo=datetime.timezone.utc,
                )
            except ValueError:
                pass

        # YYYY/MM/DD embedded in string
        match = re.search(
            r"\b(20\d{2})/(\d{1,2})/(\d{1,2})\b",
            value,
        )

        if match:
            try:
                return datetime.datetime(
                    int(match.group(1)),
                    int(match.group(2)),
                    int(match.group(3)),
                    tzinfo=datetime.timezone.utc,
                )
            except ValueError:
                pass

        # YYYY-MM
        match = re.search(
            r"\b(20\d{2})[-/](\d{1,2})\b",
            value,
        )

        if match:
            try:
                return datetime.datetime(
                    int(match.group(1)),
                    int(match.group(2)),
                    1,
                    tzinfo=datetime.timezone.utc,
                )
            except ValueError:
                pass

        # YYYY MM
        match = re.search(
            r"\b(20\d{2})\s+(\d{1,2})\b",
            value,
        )

        if match:
            try:
                return datetime.datetime(
                    int(match.group(1)),
                    int(match.group(2)),
                    1,
                    tzinfo=datetime.timezone.utc,
                )
            except ValueError:
                pass

        # YYYY only
        match = re.search(
            r"\b(20\d{2})\b",
            value,
        )

        if match:
            try:
                return datetime.datetime(
                    int(match.group(1)),
                    1,
                    1,
                    tzinfo=datetime.timezone.utc,
                )
            except ValueError:
                pass

    return None


# ============================================================
# PUBMED SEARCH
# ============================================================

def search_pubmed(query, now):
    """
    Search PubMed using ESearch JSON.

    IMPORTANT:
    PubMed expects retmode=json, NOT rettype=json.
    """

    start_date = (
        now - datetime.timedelta(days=MAX_AGE_DAYS)
    ).strftime("%Y/%m/%d")

    end_date = now.strftime("%Y/%m/%d")

    full_query = (
        f"({query}) "
        f"AND ({start_date}[PDat] : {end_date}[PDat])"
    )

    params = {
        "db": "pubmed",
        "term": full_query,
        "retmax": 100,
        "retmode": "json",
        "sort": "pub date",
    }

    if NCBI_API_KEY:
        params["api_key"] = NCBI_API_KEY

    url = f"{EUTILS_BASE}/esearch.fcgi"

    response = requests.get(
        url,
        params=params,
        timeout=REQUEST_TIMEOUT,
        headers={
            "User-Agent": "ForensicResearchCollector/1.0"
        },
    )

    response.raise_for_status()

    data = response.json()

    result = data.get("esearchresult", {})

    return result.get("idlist", [])


# ============================================================
# PUBMED SUMMARY
# ============================================================

def fetch_pubmed_summaries(pmids):
    """
    Fetch PubMed article summaries using ESummary JSON.

    ESummary is used deliberately because it provides a stable
    JSON response suitable for this lightweight collector.
    """

    if not pmids:
        return []

    params = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "json",
    }

    if NCBI_API_KEY:
        params["api_key"] = NCBI_API_KEY

    url = f"{EUTILS_BASE}/esummary.fcgi"

    response = requests.get(
        url,
        params=params,
        timeout=REQUEST_TIMEOUT,
        headers={
            "User-Agent": "ForensicResearchCollector/1.0"
        },
    )

    response.raise_for_status()

    data = response.json()

    result = data.get("result", {})

    papers = []

    for pmid in result.get("uids", []):

        article = result.get(str(pmid), {})

        if not article:
            continue

        title = article.get("title", "").strip()

        if not title or len(title) < 10:
            continue

        published_dt = parse_pubmed_date(article)

        if not published_dt:
            continue

        journal = (
            article.get("fulljournalname")
            or article.get("source")
            or "PubMed"
        )

        authors = []

        for author in article.get("authors", [])[:5]:

            name = author.get("name")

            if name:
                authors.append(name)

        if not authors:
            authors_text = "N/A"
        else:
            authors_text = ", ".join(authors)

        papers.append(
            {
                "title": title,
                "authors": authors_text,
                "published_dt": published_dt,
                "journal": journal,
                "pmid": str(pmid),
                "url": (
                    f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
                ),
            }
        )

    return papers


# ============================================================
# MAIN COLLECTION
# ============================================================

def run():

    now = datetime.datetime.now(datetime.timezone.utc)

    cutoff = now - datetime.timedelta(
        days=MAX_AGE_DAYS
    )

    print("=" * 60)
    print("FORENSIC RESEARCH PAPER COLLECTOR")
    print("=" * 60)

    print(f"Current UTC: {now.isoformat()}")
    print(
        f"Collection window: {cutoff.strftime('%Y-%m-%d')}"
        f" → {now.strftime('%Y-%m-%d')}"
    )
    print(f"Maximum papers: {MAX_RESULTS}")
    print(f"Queries: {len(PUBMED_QUERIES)}")
    print()

    all_pmids = set()

    successful_queries = 0
    failed_queries = 0

    # --------------------------------------------------------
    # SEARCH ALL QUERIES
    # --------------------------------------------------------

    for index, query in enumerate(
        PUBMED_QUERIES,
        start=1,
    ):

        print(
            f"[{index}/{len(PUBMED_QUERIES)}] "
            f"{query}"
        )

        try:

            pmids = search_pubmed(
                query,
                now,
            )

            new_pmids = [
                pmid
                for pmid in pmids
                if pmid not in all_pmids
            ]

            for pmid in new_pmids:
                all_pmids.add(pmid)

            successful_queries += 1

            print(
                f"  ✓ PubMed returned {len(pmids)} "
                f"results; {len(new_pmids)} new"
            )

        except Exception as exc:

            failed_queries += 1

            print(
                f"  ✗ Query failed: {exc}"
            )

        time.sleep(0.35)

    print()
    print(
        f"Successful queries: {successful_queries}"
    )

    print(
        f"Failed queries: {failed_queries}"
    )

    print(
        f"Unique PMIDs collected: {len(all_pmids)}"
    )

    # --------------------------------------------------------
    # HARD FAILURE PROTECTION
    # --------------------------------------------------------

    if successful_queries == 0:

        raise RuntimeError(
            "ALL PubMed queries failed. "
            "Refusing to overwrite forensic_research.json "
            "with an empty feed."
        )

    if not all_pmids:

        raise RuntimeError(
            "PubMed queries succeeded but returned zero PMIDs. "
            "Refusing to publish an empty research feed."
        )

    # --------------------------------------------------------
    # FETCH SUMMARIES
    # --------------------------------------------------------

    pmid_list = list(all_pmids)

    papers = []

    for start in range(
        0,
        len(pmid_list),
        200,
    ):

        batch = pmid_list[
            start:start + 200
        ]

        try:

            batch_papers = fetch_pubmed_summaries(
                batch
            )

            papers.extend(batch_papers)

            print(
                f"Fetched metadata for "
                f"{len(batch_papers)} papers"
            )

        except Exception as exc:

            print(
                f"✗ ESummary batch failed: {exc}"
            )

        time.sleep(0.35)

    # --------------------------------------------------------
    # DATE FILTER
    # --------------------------------------------------------

    recent_papers = []

    for paper in papers:

        published_dt = paper["published_dt"]

        if cutoff <= published_dt <= now:

            recent_papers.append(paper)

    print(
        f"Within 30-day window: "
        f"{len(recent_papers)}"
    )

    # --------------------------------------------------------
    # DEDUPLICATION
    # --------------------------------------------------------

    unique_papers = []

    seen_pmids = set()
    seen_titles = set()

    duplicate_pmids = 0
    duplicate_titles = 0

    for paper in sorted(
        recent_papers,
        key=lambda item: item["published_dt"],
        reverse=True,
    ):

        pmid = paper["pmid"]

        normalized = normalize_title(
            paper["title"]
        )

        if pmid in seen_pmids:

            duplicate_pmids += 1
            continue

        if normalized in seen_titles:

            duplicate_titles += 1
            continue

        seen_pmids.add(pmid)
        seen_titles.add(normalized)

        unique_papers.append(paper)

    # --------------------------------------------------------
    # LIMIT
    # --------------------------------------------------------

    unique_papers = unique_papers[
        :MAX_RESULTS
    ]

    print()
    print("SUMMARY")
    print("-" * 60)

    print(
        f"Recent papers: {len(recent_papers)}"
    )

    print(
        f"Duplicate PMIDs removed: "
        f"{duplicate_pmids}"
    )

    print(
        f"Duplicate titles removed: "
        f"{duplicate_titles}"
    )

    print(
        f"Final accepted papers: "
        f"{len(unique_papers)}"
    )

    # --------------------------------------------------------
    # HARD FAILURE IF NO VALID PAPERS
    # --------------------------------------------------------

    if not unique_papers:

        raise RuntimeError(
            "PubMed was reachable but no valid recent "
            "forensic research papers were found. "
            "Existing forensic_research.json was preserved."
        )

    # --------------------------------------------------------
    # BUILD JSON
    # --------------------------------------------------------

    output = {
        "updated": now.isoformat().replace(
            "+00:00",
            "Z",
        ),
        "source": "PubMed",
        "window_days": MAX_AGE_DAYS,
        "items": [],
    }

    for paper in unique_papers:

        output["items"].append(
            {
                "title": paper["title"],
                "authors": paper["authors"],
                "published": paper[
                    "published_dt"
                ].strftime("%Y-%m-%d"),
                "journal": paper["journal"],
                "pmid": paper["pmid"],
                "url": paper["url"],
            }
        )

    # --------------------------------------------------------
    # ATOMIC WRITE
    # --------------------------------------------------------

    temp_file = (
        OUTPUT_FILE + ".tmp"
    )

    try:

        with open(
            temp_file,
            "w",
            encoding="utf-8",
        ) as file:

            json.dump(
                output,
                file,
                indent=2,
                ensure_ascii=False,
            )

            file.write("\n")

        os.replace(
            temp_file,
            OUTPUT_FILE,
        )

    except Exception:

        if os.path.exists(temp_file):
            os.remove(temp_file)

        raise

    # --------------------------------------------------------
    # FINAL REPORT
    # --------------------------------------------------------

    print()
    print("=" * 60)
    print(
        f"✓ Written {OUTPUT_FILE} "
        f"({len(unique_papers)} items)"
    )
    print("=" * 60)

    for index, paper in enumerate(
        unique_papers,
        start=1,
    ):

        print(
            f"{index:02d}. "
            f"{paper['published_dt'].strftime('%Y-%m-%d')} "
            f"| {paper['title']}"
        )


if __name__ == "__main__":
    run()
