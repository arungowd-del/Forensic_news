"""
Indian Forensic Research Collector

Collects recent genuine research papers/articles originating from India,
with priority given to Indian forensic/medico-legal journals and Indian
authors/institutions.

Independent collector:
- Does NOT modify forensic_research_collector.py
- Does NOT modify forensic_research.json
- Writes only indian_forensic_research.json

Sources:
- PubMed / NCBI E-utilities
- Crossref public API

Output:
indian_forensic_research.json
"""

import datetime
import json
import os
import re
import time
import tempfile
from urllib.parse import quote

import requests


# ============================================================
# CONFIGURATION
# ============================================================

OUTPUT_FILE = "indian_forensic_research.json"

DAYS_BACK = 30
MAX_PAPERS = 30

PUBMED_ESEARCH = (
    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
)

PUBMED_ESUMMARY = (
    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
)

CROSSREF_API = (
    "https://api.crossref.org/works"
)

HEADERS = {
    "User-Agent": (
        "Indian-Forensic-Research-Collector/1.0 "
        "(research feed)"
    )
}


# ============================================================
# DATE HELPERS
# ============================================================

TODAY = datetime.date.today()
START_DATE = TODAY - datetime.timedelta(days=DAYS_BACK)


def iso_date(value):
    """
    Convert common date formats to YYYY-MM-DD.
    """
    if not value:
        return ""

    value = str(value).strip()

    match = re.search(
        r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})",
        value
    )

    if match:
        y, m, d = match.groups()
        return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"

    match = re.search(
        r"(\d{4})[-/](\d{1,2})",
        value
    )

    if match:
        y, m = match.groups()
        return f"{int(y):04d}-{int(m):02d}-01"

    match = re.search(r"(\d{4})", value)

    if match:
        return f"{match.group(1)}-01-01"

    return ""


def recent_date(date_string):
    """
    Return True when date is within the rolling window.
    """
    parsed = iso_date(date_string)

    if not parsed:
        return False

    try:
        d = datetime.date.fromisoformat(parsed)
        return START_DATE <= d <= TODAY
    except Exception:
        return False


# ============================================================
# TEXT HELPERS
# ============================================================

def clean_text(value):
    if value is None:
        return ""

    value = str(value)

    value = re.sub(r"\s+", " ", value)

    return value.strip()


def normalize_title(title):
    """
    Used for title deduplication.
    """
    title = clean_text(title).lower()

    title = re.sub(
        r"[^a-z0-9]+",
        " ",
        title
    )

    return " ".join(title.split())


def safe_get(dictionary, *keys):
    value = dictionary

    for key in keys:
        if not isinstance(value, dict):
            return ""

        value = value.get(key, "")

    return value


# ============================================================
# INDIAN FORENSIC JOURNALS
# ============================================================

INDIAN_JOURNALS = [
    "Journal of Indian Academy of Forensic Medicine",
    "Indian Journal of Forensic Medicine & Toxicology",
    "Indian Journal of Forensic Medicine and Toxicology",
    "Indian Journal of Forensic Medicine and Pathology",
    "Indian Journal of Forensic Medicine & Pathology",
    "Journal of Forensic Medicine and Toxicology",
    "Indian Journal of Forensic Sciences",
    "Indian Journal of Medical Ethics",
]


# ============================================================
# CORE FORENSIC SEARCH TERMS
# ============================================================

FORENSIC_TERMS = [
    "forensic medicine",
    "forensic pathology",
    "forensic science",
    "forensic toxicology",
    "medicolegal",
    "medico-legal",
    "legal medicine",
    "medical jurisprudence",
    "autopsy",
    "postmortem",
    "post-mortem",
    "forensic anthropology",
    "forensic genetics",
    "forensic DNA",
    "forensic radiology",
    "virtopsy",
    "PMCT",
    "forensic odontology",
    "sexual assault forensic",
    "forensic psychiatry",
    "poisoning forensic",
]


# ============================================================
# INDIA-SPECIFIC TERMS
# ============================================================

INDIA_TERMS = [
    "India",
    "Indian",
    "Indian medical college",
    "Indian university",
    "AIIMS",
    "PGIMER",
    "JIPMER",
    "NIMHANS",
    "forensic medicine India",
    "forensic toxicology India",
    "medico-legal India",
]


# ============================================================
# PUBMED REQUEST
# ============================================================

def pubmed_search(query):
    """
    Search PubMed and return PMIDs.
    """

    params = {
        "db": "pubmed",
        "term": query,
        "retmode": "json",
        "retmax": 100,
        "datetype": "pdat",
        "mindate": START_DATE.isoformat(),
        "maxdate": TODAY.isoformat(),
    }

    try:
        response = requests.get(
            PUBMED_ESEARCH,
            params=params,
            headers=HEADERS,
            timeout=30,
        )

        response.raise_for_status()

        data = response.json()

        return data.get("esearchresult", {}).get(
            "idlist",
            []
        )

    except Exception as error:
        print(
            f"  PubMed search failed: {error}"
        )

        return []


# ============================================================
# PUBMED METADATA
# ============================================================

def pubmed_metadata(pmids):
    """
    Fetch PubMed metadata in batches.
    """

    records = []

    if not pmids:
        return records

    batch_size = 100

    for start in range(
        0,
        len(pmids),
        batch_size
    ):

        batch = pmids[
            start:start + batch_size
        ]

        params = {
            "db": "pubmed",
            "id": ",".join(batch),
            "retmode": "json",
        }

        try:
            response = requests.get(
                PUBMED_ESUMMARY,
                params=params,
                headers=HEADERS,
                timeout=30,
            )

            response.raise_for_status()

            data = response.json()

            result = data.get(
                "result",
                {}
            )

            for pmid in batch:

                item = result.get(
                    pmid
                )

                if not isinstance(item, dict):
                    continue

                title = clean_text(
                    item.get("title", "")
                )

                journal = clean_text(
                    item.get("fulljournalname")
                    or item.get("source")
                    or ""
                )

                published = clean_text(
                    item.get("pubdate", "")
                )

                authors = []

                for author in item.get(
                    "authors",
                    []
                ):

                    if isinstance(author, dict):

                        name = clean_text(
                            author.get(
                                "name",
                                ""
                            )
                        )

                        if name:
                            authors.append(
                                name
                            )

                if not title:
                    continue

                records.append({
                    "title": title,
                    "journal": journal,
                    "published": iso_date(
                        published
                    ),
                    "authors": ", ".join(
                        authors[:10]
                    ),
                    "url": (
                        "https://pubmed.ncbi.nlm.nih.gov/"
                        + str(pmid)
                        + "/"
                    ),
                    "doi": "",
                    "source": "PubMed",
                    "pmid": str(pmid),
                })

            time.sleep(0.15)

        except Exception as error:

            print(
                f"  PubMed metadata failed: {error}"
            )

    return records


# ============================================================
# CROSSREF SEARCH
# ============================================================

def crossref_search(query):
    """
    Search Crossref for recent scholarly records.
    """

    params = {
        "query.bibliographic": query,
        "filter": (
            "from-pub-date:"
            + START_DATE.isoformat()
            + ",until-pub-date:"
            + TODAY.isoformat()
        ),
        "rows": 100,
        "select": (
            "DOI,title,container-title,"
            "published,published-print,"
            "published-online,author,"
            "URL,type"
        ),
    }

    try:

        response = requests.get(
            CROSSREF_API,
            params=params,
            headers=HEADERS,
            timeout=30,
        )

        response.raise_for_status()

        data = response.json()

        return data.get(
            "message",
            {}
        ).get(
            "items",
            []
        )

    except Exception as error:

        print(
            f"  Crossref search failed: {error}"
        )

        return []


# ============================================================
# CROSSREF NORMALIZATION
# ============================================================

def crossref_to_record(item):

    title_list = item.get(
        "title",
        []
    )

    title = ""

    if title_list:
        title = clean_text(
            title_list[0]
        )

    journal_list = item.get(
        "container-title",
        []
    )

    journal = ""

    if journal_list:
        journal = clean_text(
            journal_list[0]
        )

    authors = []

    for author in item.get(
        "author",
        []
    ):

        if not isinstance(
            author,
            dict
        ):
            continue

        given = clean_text(
            author.get(
                "given",
                ""
            )
        )

        family = clean_text(
            author.get(
                "family",
                ""
            )
        )

        name = clean_text(
            f"{given} {family}"
        )

        if name:
            authors.append(name)

    date_candidates = [
        item.get(
            "published-online"
        ),
        item.get(
            "published-print"
        ),
        item.get(
            "published"
        ),
    ]

    published = ""

    for candidate in date_candidates:

        if not isinstance(
            candidate,
            dict
        ):
            continue

        parts = candidate.get(
            "date-parts",
            []
        )

        if not parts:
            continue

        first = parts[0]

        if not first:
            continue

        year = first[0]

        month = (
            first[1]
            if len(first) > 1
            else 1
        )

        day = (
            first[2]
            if len(first) > 2
            else 1
        )

        published = (
            f"{int(year):04d}-"
            f"{int(month):02d}-"
            f"{int(day):02d}"
        )

        break

    doi = clean_text(
        item.get(
            "DOI",
            ""
        )
    )

    url = clean_text(
        item.get(
            "URL",
            ""
        )
    )

    if not url and doi:
        url = (
            "https://doi.org/"
            + doi
        )

    if not title:
        return None

    return {
        "title": title,
        "journal": journal,
        "published": published,
        "authors": ", ".join(
            authors[:10]
        ),
        "url": url,
        "doi": doi,
        "source": "Crossref",
        "pmid": "",
    }


# ============================================================
# INDIA DETECTION
# ============================================================

def is_indian_journal(journal):

    journal_normalized = (
        clean_text(journal).lower()
    )

    for known in INDIAN_JOURNALS:

        known_normalized = (
            known.lower()
        )

        if (
            known_normalized
            in journal_normalized
        ):
            return True

    return False


def india_signal(record):

    """
    Determine whether the record has meaningful
    India-related evidence.

    Returns a score.
    """

    score = 0

    title = clean_text(
        record.get(
            "title",
            ""
        )
    ).lower()

    journal = clean_text(
        record.get(
            "journal",
            ""
        )
    ).lower()

    authors = clean_text(
        record.get(
            "authors",
            ""
        )
    ).lower()

    # --------------------------------------------------------
    # Indian journal
    # --------------------------------------------------------

    if is_indian_journal(
        journal
    ):
        score += 10

    # --------------------------------------------------------
    # Explicit India in title
    # --------------------------------------------------------

    india_title_terms = [
        "india",
        "indian",
        "in india",
    ]

    for term in india_title_terms:

        if term in title:
            score += 4
            break

    # --------------------------------------------------------
    # Indian institutions
    # --------------------------------------------------------

    institution_terms = [
        "aiims",
        "all india institute",
        "pgimer",
        "jipmer",
        "nimhans",
        "safdarjung",
        "maulana azad medical",
        "king george",
        "banaras hindu",
        "amrita institute",
        "manipal",
        "jss academy",
        "andhra",
        "telangana",
        "tamil nadu",
        "kerala",
        "karnataka",
        "maharashtra",
        "gujarat",
        "rajasthan",
        "punjab",
        "haryana",
        "odisha",
        "west bengal",
        "uttar pradesh",
        "madhya pradesh",
        "bihar",
        "chandigarh",
        "delhi",
    ]

    combined = (
        title
        + " "
        + authors
        + " "
        + journal
    )

    for term in institution_terms:

        if term in combined:
            score += 5
            break

    # --------------------------------------------------------
    # India geographical signal
    # --------------------------------------------------------

    if (
        "india" in combined
        or "indian" in combined
    ):
        score += 3

    return score


# ============================================================
# FORENSIC RELEVANCE
# ============================================================

def forensic_score(record):

    text = (
        clean_text(
            record.get(
                "title",
                ""
            )
        )
        + " "
        + clean_text(
            record.get(
                "journal",
                ""
            )
        )
    ).lower()

    score = 0

    high_value = [
        "forensic medicine",
        "forensic pathology",
        "forensic toxicology",
        "medicolegal",
        "medico-legal",
        "legal medicine",
        "medical jurisprudence",
        "postmortem",
        "post-mortem",
        "autopsy",
        "forensic anthropology",
        "forensic genetics",
        "forensic dna",
        "forensic odontology",
        "forensic radiology",
        "virtopsy",
        "pmct",
        "sexual assault",
        "poisoning",
    ]

    for term in high_value:

        if term in text:
            score += 3

    medium_value = [
        "toxicology",
        "anthropology",
        "dna",
        "genetics",
        "death",
        "injury",
        "drowning",
        "hanging",
        "strangulation",
        "burn",
        "trauma",
        "identification",
        "disaster victim",
    ]

    for term in medium_value:

        if term in text:
            score += 1

    return score


# ============================================================
# SEARCH PLAN
# ============================================================

def build_search_plan():

    searches = []

    # --------------------------------------------------------
    # Indian forensic journals
    # --------------------------------------------------------

    for journal in INDIAN_JOURNALS:

        searches.append(
            (
                "journal",
                f'"{journal}"'
            )
        )

    # --------------------------------------------------------
    # India + forensic searches
    # --------------------------------------------------------

    for term in FORENSIC_TERMS:

        searches.append(
            (
                "india_forensic",
                f'"{term}" AND India'
            )
        )

    # --------------------------------------------------------
    # Major Indian forensic institutions
    # --------------------------------------------------------

    institutions = [
        "AIIMS",
        "PGIMER",
        "JIPMER",
        "NIMHANS",
    ]

    for institution in institutions:

        searches.append(
            (
                "institution",
                f'"{institution}" AND forensic'
            )
        )

    return searches


# ============================================================
# DEDUPLICATION
# ============================================================

def deduplicate(records):

    unique = []

    seen_doi = set()
    seen_pmid = set()
    seen_title = set()
    seen_url = set()

    duplicates = 0

    for record in records:

        doi = clean_text(
            record.get(
                "doi",
                ""
            )
        ).lower()

        pmid = clean_text(
            record.get(
                "pmid",
                ""
            )
        )

        url = clean_text(
            record.get(
                "url",
                ""
            )
        ).lower()

        title_key = normalize_title(
            record.get(
                "title",
                ""
            )
        )

        duplicate = False

        if doi and doi in seen_doi:
            duplicate = True

        elif pmid and pmid in seen_pmid:
            duplicate = True

        elif url and url in seen_url:
            duplicate = True

        elif (
            title_key
            and title_key in seen_title
        ):
            duplicate = True

        if duplicate:

            duplicates += 1
            continue

        if doi:
            seen_doi.add(doi)

        if pmid:
            seen_pmid.add(pmid)

        if url:
            seen_url.add(url)

        if title_key:
            seen_title.add(title_key)

        unique.append(record)

    return unique, duplicates


# ============================================================
# ATOMIC JSON WRITE
# ============================================================

def atomic_write(path, payload):

    directory = (
        os.path.dirname(
            os.path.abspath(path)
        )
    )

    fd, temp_path = tempfile.mkstemp(
        prefix=".research_",
        suffix=".tmp",
        dir=directory,
        text=True,
    )

    try:

        with os.fdopen(
            fd,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                payload,
                file,
                ensure_ascii=False,
                indent=2,
            )

            file.write("\n")

        os.replace(
            temp_path,
            path
        )

    except Exception:

        try:
            os.remove(temp_path)
        except Exception:
            pass

        raise


# ============================================================
# MAIN COLLECTION
# ============================================================

def main():

    print("")
    print("=" * 60)
    print("INDIAN FORENSIC RESEARCH COLLECTION")
    print("=" * 60)

    print(
        f"Date window: "
        f"{START_DATE.isoformat()} "
        f"to "
        f"{TODAY.isoformat()}"
    )

    print(
        f"Maximum papers: {MAX_PAPERS}"
    )

    print("")

    all_records = []

    pubmed_ids = set()

    search_plan = build_search_plan()

    print(
        f"Searches planned: "
        f"{len(search_plan)}"
    )

    print("")

    # ========================================================
    # PUBMED
    # ========================================================

    print("PUBMED")
    print("-" * 60)

    for index, (
        category,
        query
    ) in enumerate(
        search_plan,
        start=1
    ):

        print(
            f"[{index}/{len(search_plan)}] "
            f"{query}"
        )

        ids = pubmed_search(
            query
        )

        before = len(
            pubmed_ids
        )

        pubmed_ids.update(
            ids
        )

        added = (
            len(pubmed_ids)
            - before
        )

        print(
            f"  PubMed returned "
            f"{len(ids)} results; "
            f"{added} new"
        )

        time.sleep(0.15)

    print("")

    print(
        f"Unique PubMed IDs: "
        f"{len(pubmed_ids)}"
    )

    pubmed_records = (
        pubmed_metadata(
            list(pubmed_ids)
        )
    )

    print(
        f"PubMed metadata records: "
        f"{len(pubmed_records)}"
    )

    all_records.extend(
        pubmed_records
    )

    print("")

    # ========================================================
    # CROSSREF
    # ========================================================

    print("CROSSREF")
    print("-" * 60)

    crossref_queries = []

    # Indian journals
    for journal in INDIAN_JOURNALS:

        crossref_queries.append(
            f'"{journal}"'
        )

    # India + forensic
    crossref_queries.extend([
        "forensic medicine India",
        "forensic pathology India",
        "forensic toxicology India",
        "medico-legal India",
        "medicolegal India",
        "legal medicine India",
        "postmortem India",
        "autopsy India",
        "forensic anthropology India",
        "forensic genetics India",
        "forensic DNA India",
        "forensic science India",
    ])

    crossref_seen = set()

    for index, query in enumerate(
        crossref_queries,
        start=1
    ):

        print(
            f"[{index}/{len(crossref_queries)}] "
            f"{query}"
        )

        items = crossref_search(
            query
        )

        added = 0

        for item in items:

            record = (
                crossref_to_record(
                    item
                )
            )

            if not record:
                continue

            key = (
                record.get(
                    "doi",
                    ""
                ).lower()
                or normalize_title(
                    record.get(
                        "title",
                        ""
                    )
                )
            )

            if not key:
                continue

            if key in crossref_seen:
                continue

            crossref_seen.add(
                key
            )

            all_records.append(
                record
            )

            added += 1

        print(
            f"  Crossref returned "
            f"{len(items)} results; "
            f"{added} new"
        )

        time.sleep(0.2)

    print("")

    print(
        f"Total raw records: "
        f"{len(all_records)}"
    )

    # ========================================================
    # FILTER
    # ========================================================

    candidates = []

    rejected_old = 0
    rejected_non_indian = 0
    rejected_non_forensic = 0

    for record in all_records:

        published = record.get(
            "published",
            ""
        )

        if not recent_date(
            published
        ):
            rejected_old += 1
            continue

        india = india_signal(
            record
        )

        forensic = forensic_score(
            record
        )

        # ----------------------------------------------------
        # Require meaningful Indian evidence
        # ----------------------------------------------------

        if india < 3:
            rejected_non_indian += 1
            continue

        # ----------------------------------------------------
        # Require forensic relevance
        # ----------------------------------------------------

        if forensic < 3:

            # Indian forensic journals get a pass
            # because the journal itself is a strong signal.

            if not is_indian_journal(
                record.get(
                    "journal",
                    ""
                )
            ):

                rejected_non_forensic += 1
                continue

        record["_india_score"] = india
        record["_forensic_score"] = forensic

        record["_total_score"] = (
            india
            + forensic
        )

        candidates.append(
            record
        )

    print("")
    print("FILTERING")
    print("-" * 60)

    print(
        f"Recent candidates: "
        f"{len(candidates)}"
    )

    print(
        f"Rejected outside date window: "
        f"{rejected_old}"
    )

    print(
        f"Rejected weak India signal: "
        f"{rejected_non_indian}"
    )

    print(
        f"Rejected weak forensic relevance: "
        f"{rejected_non_forensic}"
    )

    # ========================================================
    # DEDUPLICATE
    # ========================================================

    unique_records, duplicates = (
        deduplicate(
            candidates
        )
    )

    # ========================================================
    # SORT
    # ========================================================

    unique_records.sort(
        key=lambda record: (
            record.get(
                "_total_score",
                0
            ),
            record.get(
                "published",
                ""
            ),
        ),
        reverse=True,
    )

    # ========================================================
    # LIMIT
    # ========================================================

    final_records = (
        unique_records[:MAX_PAPERS]
    )

    # ========================================================
    # REMOVE INTERNAL FIELDS
    # ========================================================

    clean_records = []

    for record in final_records:

        clean_records.append({
            "title": clean_text(
                record.get(
                    "title",
                    ""
                )
            ),

            "journal": clean_text(
                record.get(
                    "journal",
                    ""
                )
            ),

            "published": clean_text(
                record.get(
                    "published",
                    ""
                )
            ),

            "authors": clean_text(
                record.get(
                    "authors",
                    ""
                )
            ),

            "url": clean_text(
                record.get(
                    "url",
                    ""
                )
            ),

            "doi": clean_text(
                record.get(
                    "doi",
                    ""
                )
            ),

            "source": clean_text(
                record.get(
                    "source",
                    ""
                )
            ),

            "country": "India",
        })

    # ========================================================
    # OUTPUT
    # ========================================================

    output = {
        "updated": (
            datetime.datetime.now(
                datetime.timezone.utc
            ).isoformat()
        ),

        "country": "India",

        "collection": (
            "Indian Forensic Research"
        ),

        "window_days": DAYS_BACK,

        "items": clean_records,
    }

    atomic_write(
        OUTPUT_FILE,
        output
    )

    # ========================================================
    # FINAL SUMMARY
    # ========================================================

    print("")
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)

    print(
        f"Raw records collected : "
        f"{len(all_records)}"
    )

    print(
        f"Recent candidates      : "
        f"{len(candidates)}"
    )

    print(
        f"Duplicates removed     : "
        f"{duplicates}"
    )

    print(
        f"Final accepted papers  : "
        f"{len(clean_records)}"
    )

    print("")

    print(
        f"✓ Written "
        f"{OUTPUT_FILE} "
        f"({len(clean_records)} items)"
    )

    print(
        "✓ COLLECTION COMPLETE"
    )

    print("")


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
