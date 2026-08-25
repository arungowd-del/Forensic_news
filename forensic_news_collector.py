import feedparser
import json
import datetime
import re
import hashlib
import os
import difflib
import urllib.parse

# --- ENHANCED CONFIG ---
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

# Domains that should be treated as high-priority India sources
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

# Google News query templates we will use (India-focused and global forensic queries)
QUERIES = [
    # High-priority institutional/government queries
    ("site:nfsu.edu.in forensic OR "forensic"", True),
    ("site:nfsu.ac.in forensic OR "forensic"", True),
    ("site:pib.gov.in forensic OR "forensic"", True),
    ("site:supremecourtofindia.nic.in dna OR forensic OR 'expert evidence'", True),

    # Judicial & legal reporting
    ("dna evidence supreme court india", True),
    ("forensic laboratory cfsl fsl india", True),

    # Digital forensics and cyber
    ("digital forensics India cyber forensics ncf l meity", True),

    # Broader forensic topics (India-focused, but allow global fallback)
    ("forensic medicine India", True),
    ("dna identification India", True),
    ("forensic toxicology India", True),
    ("mobile forensic van India", True),

    # Global forensic queries (keep separate, flagged as global)
    ("forensic science dna", False),
    ("forensic toxicology research", False),
    ("digital forensics tools", False),
]

MAX_AGE_HOURS = 168  # 7 days
MAX_RESULTS = 40

# Stopwords for naive normalization/dedupe
STOPWORDS = set(["the","a","an","of","in","on","for","and","to","with","after","at","by","from","india","indian"])


def normalize_title(t):
    """Lowercase, remove punctuation, collapse whitespace, remove stopwords. Return normalized string."""
    if not t:
        return ""
    t = t.lower()
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    parts = [w for w in t.split() if w not in STOPWORDS]
    return " ".join(parts)


def event_key(item):
    """Create an event-level key for deduplication using normalized title + domain + forensic keyword fingerprint."""
    nt = normalize_title(item.get('title',''))
    domain = urllib.parse.urlparse(item.get('url','')).netloc.lower()
    # fingerprint of forensic keywords present
    kws = [k for k in FORENSIC_KEYWORDS if k in (item.get('title','') + ' ' + item.get('summary','')).lower()]
    return hashlib.md5((nt + '|' + domain + '|' + ','.join(kws)).encode()).hexdigest()


def similar(a, b):
    return difflib.SequenceMatcher(None, a, b).ratio()


def parse_publication_date(entry):
    try:
        if hasattr(entry, 'published_parsed') and entry.published_parsed:
            return datetime.datetime(*entry.published_parsed[:6], tzinfo=datetime.timezone.utc)
        elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
            return datetime.datetime(*entry.updated_parsed[:6], tzinfo=datetime.timezone.utc)
    except (TypeError, ValueError):
        pass
    # try to parse 'published' string loosely
    if hasattr(entry, 'published') and entry.published:
        try:
            return datetime.datetime.fromisoformat(entry.published)
        except Exception:
            pass
    return None


def is_within_7_days(pub_dt):
    if not pub_dt:
        return False
    now = datetime.datetime.now(datetime.timezone.utc)
    age_hours = (now - pub_dt).total_seconds() / 3600
    return age_hours <= MAX_AGE_HOURS


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

    # Recency
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

    # Forensic relevance
    matches = sum(1 for k in FORENSIC_KEYWORDS if k in text)
    s += matches * 8

    # India/source priority
    dp = domain_priority(item.get('url',''))
    if is_india_query:
        s += 10
    s += dp

    # Penalize generic crime headlines without forensic keywords
    if any(k in text for k in ["arrested", "suspect", "murdered", "killed"]) and not is_forensic_relevant(text):
        s -= 60

    return s


def run():
    all_items = []
    now = datetime.datetime.now(datetime.timezone.utc)
    seen_event_keys = {}
    failed_sources = []

    for q, is_india_query in QUERIES:
        try:
            rss_url = f"https://news.google.com/rss/search?q={urllib.parse.quote_plus(q)}&hl=en-IN&gl=IN&ceid=IN:en"
            f = feedparser.parse(rss_url)
            if getattr(f, 'bozo', False):
                # feedparser sets bozo if feed parsing had problems, but continue
                pass
            for e in f.entries:
                try:
                    title = e.title.split(' - ')[0].strip() if hasattr(e, 'title') else "Unknown"
                    pub_dt = parse_publication_date(e)
                    # hard filter by date
                    if not is_within_7_days(pub_dt):
                        continue
                    summary = e.summary[:400] if hasattr(e, 'summary') else ""

                    # optional relevance filter for non-India queries
                    if not is_india_query and not is_forensic_relevant(title + ' ' + summary):
                        continue

                    url = e.link if hasattr(e, 'link') else ""

                    itm = {
                        'title': title,
                        'summary': summary,
                        'source': e.source.title if hasattr(e, 'source') else (urllib.parse.urlparse(url).netloc or 'News'),
                        'published': e.published if hasattr(e, 'published') else '',
                        'url': url,
                        'dt': pub_dt,
                    }
                    itm['age_hours'] = (now - pub_dt).total_seconds() / 3600 if pub_dt else 999999
                    itm['category'] = '🇮🇳 INDIA' if any(k in (title + ' ' + summary).lower() for k in KEYWORDS['INDIA']) or ('india' in url.lower()) else '🌎 GLOBAL'
                    itm['score'] = score(itm, is_india_query)

                    # Event-level deduplication: compute event key and collapse similar titles
                    ek = event_key(itm)
                    if ek in seen_event_keys:
                        # merge by preferring higher-score source
                        existing = seen_event_keys[ek]
                        if itm['score'] > existing['score']:
                            seen_event_keys[ek] = itm
                        continue
                    else:
                        # also check for high similarity to existing normalized titles (catch near-duplicates)
                        nt = normalize_title(title)
                        duplicate_found = False
                        for k, existing in list(seen_event_keys.items()):
                            sim = similar(nt, normalize_title(existing['title']))
                            if sim > 0.85:
                                # consider same event; keep best source
                                duplicate_found = True
                                if itm['score'] > existing['score']:
                                    seen_event_keys[k] = itm
                                break
                        if duplicate_found:
                            continue
                        seen_event_keys[ek] = itm

                except Exception:
                    continue
        except Exception as e:
            failed_sources.append(q)
            continue

    # Prepare list
    all_items = list(seen_event_keys.values())
    # final sort
    all_items.sort(key=lambda x: x['score'], reverse=True)
    final_items = all_items[:MAX_RESULTS]

    # If nothing found, keep existing file unchanged
    if not final_items:
        print("No items found within filters; aborting JSON write to avoid breaking existing feed.")
        return

    out = {
        'updated': datetime.datetime.now(datetime.timezone.utc).isoformat(),
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

    # safe write
    tmp = 'forensic_news.tmp.json'
    with open(tmp, 'w') as f:
        json.dump(out, f, indent=2)
    os.replace(tmp, 'forensic_news.json')
    print(f"Wrote forensic_news.json with {len(final_items)} items. Failed sources: {failed_sources}")


if __name__ == '__main__':
    run()
