import feedparser
import json
import datetime
import re
import hashlib
import os
import difflib
import urllib.parse
import requests

# --- ENHANCED CONFIG (Strict Date & Integrity Checks) ---
KEYWORDS = {
    "INDIA": [
        "india", "indian", "supreme court", "high court", "nfsu", "pib", "cfsl", "fsl",
        "forensic lab", "aiims", "state fsl", "national forensic", "forensic science", "mha",
        "dna", "forensic", "medico-legal", "medicolegal"
    ],
}

FORENSIC_KEYWORDS = [
    "forensic", "pathology", "autopsy", "dna", "identification", "toxicology", "evidence",
    "postmortem", "medico-legal", "victim identification", "virtopsy", "taphonomy",
    "anthropology", "odontology", "decomposition", "dvi", "forensic laboratory", "cfsl", "fsl",
    "digital forensics", "cyber forensics", "mobile forensic", "forensic genetics", "toxicological"
]

INDIA_PRIMARY_DOMAINS = {
    "pib.gov.in": 30,
    "nfsu.edu.in": 25,
    "nfsu.ac.in": 25,
    "supremecourtofindia.nic.in": 30,
    "indiankanoon.org": 20,
    "aiims.edu": 18,
    "aiims.ac.in": 18,
}

MAINSTREAM_INDIA_DOMAINS = {
    "thehindu.com": 15,
    "indianexpress.com": 15,
    "hindustantimes.com": 15,
    "timesofindia.indiatimes.com": 12,
    "ndtv.com": 12,
    "pti.in": 12,
    "ani.com": 10,
    "economictimes.indiatimes.com": 10,
}

QUERIES = [
    ("site:pib.gov.in forensic", True),
    ("site:nfsu.edu.in forensic", True),
    ("site:nfsu.ac.in forensic", True),
    ("site:supremecourtofindia.nic.in dna OR forensic", True),
    ("forensic medicine India", True),
    ("dna identification India", True),
    ("forensic toxicology India", True),
    ("digital forensics India", True),
    ("forensic science dna", False),
    ("forensic toxicology research", False),
]

MAX_AGE_HOURS = 168  # 7 days
MAX_RESULTS = 40
STOPWORDS = set(["the","a","an","of","in","on","for","and","to","with","after","at","by","from","india","indian"])


def normalize_title(t):
    if not t:
        return ""
    t = t.lower()
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    parts = [w for w in t.split() if w not in STOPWORDS]
    return " ".join(parts)


def event_key(item):
    nt = normalize_title(item.get('title',''))
    domain = urllib.parse.urlparse(item.get('url','')).netloc.lower()
    kws = [k for k in FORENSIC_KEYWORDS if k in (item.get('title','') + ' ' + item.get('summary','')).lower()]
    return hashlib.md5((nt + '|' + domain + '|' + ','.join(kws)).encode()).hexdigest()


def similar(a, b):
    return difflib.SequenceMatcher(None, a, b).ratio()


def parse_publication_date(entry):
    # Prefer RFC-parsed published_parsed
    try:
        if hasattr(entry, 'published_parsed') and entry.published_parsed:
            return datetime.datetime(*entry.published_parsed[:6], tzinfo=datetime.timezone.utc)
        if hasattr(entry, 'updated_parsed') and entry.updated_parsed:
            return datetime.datetime(*entry.updated_parsed[:6], tzinfo=datetime.timezone.utc)
    except Exception:
        pass

    # Try explicit 'published' ISO parse
    if hasattr(entry, 'published') and entry.published:
        s = entry.published.strip()
        # If it's date-only (YYYY-MM-DD), treat as midnight UTC of that date
        m = re.match(r"^(\\d{4}-\\d{2}-\\d{2})$", s)
        if m:
            try:
                d = datetime.datetime.fromisoformat(m.group(1))
                return datetime.datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=datetime.timezone.utc)
            except Exception:
                return None
        # Try common RFC formats
        try:
            from email.utils import parsedate_to_datetime
n            dt = parsedate_to_datetime(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            return dt.astimezone(datetime.timezone.utc)
        except Exception:
            pass
    return None


def is_within_7_days(pub_dt, now):
    if not pub_dt:
        return False
    age_hours = (now - pub_dt).total_seconds() / 3600
    return 0 <= age_hours <= MAX_AGE_HOURS


def is_forensic_relevant(text):
    text_lower = text.lower()
    return any(k in text_lower for k in FORENSIC_KEYWORDS)


def domain_priority(url):
    try:
        d = urllib.parse.urlparse(url).netloc.lower()
    except Exception:
        return 0
    for k,v in INDIA_PRIMARY_DOMAINS.items():
        if k in d:
            return v
    for k,v in MAINSTREAM_INDIA_DOMAINS.items():
        if k in d:
            return v
    return 0


def score(item, is_india_query):
    s = 0
    text = (item.get('title','') + " " + item.get('summary','')).lower()
    age_hours = item.get('age_hours', 999999)

    if age_hours < 6:
        s += 40
    elif age_hours < 24:
        s += 30
    elif age_hours < 48:
        s += 20
    elif age_hours < 72:
        s += 10
    elif age_hours < MAX_AGE_HOURS:
        s += 5

    matches = sum(1 for k in FORENSIC_KEYWORDS if k in text)
    s += matches * 8

    dp = domain_priority(item.get('url',''))
    if is_india_query:
        s += 10
    s += dp

    if any(k in text for k in ["arrested", "suspect", "murdered", "killed"]) and not is_forensic_relevant(text):
        s -= 60

    return s


def fetch_and_validate_url(url):
    try:
        r = requests.head(url, allow_redirects=True, timeout=8)
        if r.status_code >= 400:
            r = requests.get(url, timeout=10)
            if r.status_code >= 400:
                return False
        return True
    except Exception:
        return False


def run():
    now = datetime.datetime.now(datetime.timezone.utc)
    raw_fetched = 0
    rejected_stale = 0
    rejected_future = 0
    rejected_invalid_date = 0
    rejected_placeholder_url = 0
    duplicates_removed = 0
    failed_sources = []

    seen_event_keys = {}

    for q, is_india_query in QUERIES:
        try:
            rss_url = f"https://news.google.com/rss/search?q={urllib.parse.quote_plus(q)}&hl=en-IN&gl=IN&ceid=IN:en"
            f = feedparser.parse(rss_url)
            if getattr(f, 'bozo', False):
                pass
            for e in f.entries:
                raw_fetched += 1
                try:
                    title = e.title.split(' - ')[0].strip() if hasattr(e, 'title') else None
                    if not title:
                        continue
                    pub_dt = parse_publication_date(e)
                    if not pub_dt:
                        rejected_invalid_date += 1
                        continue
                    if pub_dt > now:
                        rejected_future += 1
                        continue
                    if not is_within_7_days(pub_dt, now):
                        rejected_stale += 1
                        continue
                    summary = e.summary[:400] if hasattr(e, 'summary') else ""
                    if not is_india_query and not is_forensic_relevant(title + ' ' + summary):
                        continue
                    url = e.link if hasattr(e, 'link') else None
                    if not url or any(x in url for x in ["example-", "example.com"]):
                        rejected_placeholder_url += 1
                        continue
                    # validate URL accessible
                    if not fetch_and_validate_url(url):
                        failed_sources.append(url)
                        continue
                    itm = {
                        'title': title,
                        'summary': summary,
                        'source': e.source.title if hasattr(e, 'source') else (urllib.parse.urlparse(url).netloc or 'News'),
                        'url': url,
                        'dt': pub_dt,
                    }
                    itm['age_hours'] = (now - pub_dt).total_seconds() / 3600
                    itm['published'] = pub_dt.isoformat().replace('+00:00','Z')
                    itm['category'] = '🇮🇳 INDIA' if any(k in (title + ' ' + summary).lower() for k in KEYWORDS['INDIA']) or ('india' in url.lower()) else '🌎 GLOBAL'
                    if itm['category'] not in ['🇮🇳 INDIA', '🌎 GLOBAL']:
                        continue
                    itm['score'] = score(itm, is_india_query)
                    ek = event_key(itm)
                    if ek in seen_event_keys:
                        existing = seen_event_keys[ek]
                        duplicates_removed += 1
                        if itm['score'] > existing['score']:
                            seen_event_keys[ek] = itm
                        continue
                    else:
                        nt = normalize_title(title)
                        duplicate_found = False
                        for k, existing in list(seen_event_keys.items()):
                            sim = similar(nt, normalize_title(existing['title']))
                            if sim > 0.9:
                                duplicate_found = True
                                duplicates_removed += 1
                                if itm['score'] > existing['score']:
                                    seen_event_keys[k] = itm
                                break
                        if duplicate_found:
                            continue
                        seen_event_keys[ek] = itm

                except Exception:
                    continue
        except Exception:
            failed_sources.append(q)
            continue

    final_items = list(seen_event_keys.values())
    final_items.sort(key=lambda x: x['score'], reverse=True)
    final_items = final_items[:MAX_RESULTS]

    # Build output
    out = {
        'updated': now.isoformat().replace('+00:00','Z'),
        'total_found': len(final_items),
        'items': []
    }

    for i, itm in enumerate(final_items):
        out_item = {
            'title': itm['title'],
            'summary': itm['summary'],
            'source': itm['source'],
            'published': itm['published'],
            'url': itm['url'],
            'category': itm['category'],
            'score': itm['score'],
            'rank': i + 1
        }
        out['items'].append(out_item)

    # Write file safely
    tmp = 'forensic_news.tmp.json'
    with open(tmp, 'w') as f:
        json.dump(out, f, indent=2)
    os.replace(tmp, 'forensic_news.json')

    # Print run stats
    print(json.dumps({
        'raw_fetched': raw_fetched,
        'rejected_stale': rejected_stale,
        'rejected_future': rejected_future,
        'rejected_invalid_date': rejected_invalid_date,
        'rejected_placeholder_url': rejected_placeholder_url,
        'duplicates_removed': duplicates_removed,
        'final_india_count': sum(1 for x in final_items if x['category']=='🇮🇳 INDIA'),
        'final_global_count': sum(1 for x in final_items if x['category']=='🌎 GLOBAL'),
        'final_total': len(final_items),
        'date_range': [min((x['published'] for x in final_items), default=None), max((x['published'] for x in final_items), default=None)],
        'failed_sources': failed_sources
    }, indent=2))


if __name__ == '__main__':
    run()
