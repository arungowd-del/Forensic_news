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
    "digital forensics", "cyber forensics", "mobile forensic", "forensic genetics", "toxicological",
    "dna profiling", "genetic genealogy", "investigative genetic genealogy", "mass fatality", "dvi"
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

# Broader queries for a high-recall collection (both India and Global)
QUERIES = [
    # India-focused
    ("site:pib.gov.in forensic OR forensic lab OR cfsl", True),
    ("site:nfsu.edu.in forensic OR dna OR training", True),
    ("NFSU forensic India 2026", True),
    ("India FSL forensic lab 2026", True),
    ("High Court DNA evidence India 2026", True),
    ("Supreme Court DNA evidence 2026 India", True),
    ("forensic medicine India 2026 postmortem autopsy", True),
    ("forensic toxicology India 2026 poisoning lab", True),
    ("digital forensics India 2026 cyber forensic lab", True),
    ("CBI forensic evidence 2026 India", True),
    ("state forensic laboratory India 2026 FSL CFSL", True),
    # Global-focused
    ("forensic DNA identification 2026", False),
    ("investigative genetic genealogy 2026 identification", False),
    ("disaster victim identification 2026 DVI", False),
    ("forensic toxicology 2026 novel toxin", False),
    ("digital forensics 2026 mobile forensic memory extraction", False),
    ("forensic pathology 2026 autopsy identification", False),
    ("forensic anthropology 2026 mass fatality", False),
    ("interpol forensic 2026 dna matching", False),
    ("FBI OCME identification 2026", False),
]

# Freshness window: 30 days (dynamic)
MAX_AGE_DAYS = 30
MAX_AGE_HOURS = MAX_AGE_DAYS * 24
MAX_RESULTS = 60
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
    # Prefer RFC-parsed published_parsed or updated_parsed
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
        m = re.match(r"^(\d{4}-\d{2}-\d{2})$", s)
        if m:
            try:
                d = datetime.datetime.fromisoformat(m.group(1))
                return datetime.datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=datetime.timezone.utc)
            except Exception:
                return None
        # Try common RFC formats
        try:
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            return dt.astimezone(datetime.timezone.utc)
        except Exception:
            pass
    return None


def is_within_window(pub_dt, now):
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

    # Recency tiers
    if age_hours < 72:
        s += 40
    elif age_hours < 168:
        s += 30
    elif age_hours < 336:
        s += 20
    elif age_hours < MAX_AGE_HOURS:
        s += 5

    matches = sum(1 for k in FORENSIC_KEYWORDS if k in text)
    s += matches * 6

    dp = domain_priority(item.get('url',''))
    if is_india_query:
        s += 8
    s += dp

    # Penalize generic crime stories with no forensic signals
    if any(k in text for k in ["arrested", "suspect", "murdered", "killed"]) and not is_forensic_relevant(text):
        s -= 40

    return s


def fetch_and_validate_url(url):
    try:
        r = requests.head(url, allow_redirects=True, timeout=6)
        if r.status_code >= 400:
            r = requests.get(url, timeout=8)
            if r.status_code >= 400:
                return False
        return True
    except Exception:
        return False


def run():
    now = datetime.datetime.now(datetime.timezone.utc)
    raw_candidates = {'INDIA': [], 'GLOBAL': []}
    accepted = []
    rejected = {
        'stale': [], 'future': [], 'invalid_date': [], 'duplicate': [], 'insufficient_relevance': [],
        'inaccessible_url': [], 'placeholder_url': [], 'other': []
    }

    seen_event_keys = {}
    failed_sources = []
    raw_fetched = 0

    # Attempt live fetch; if the environment has no network or fails repeatedly, fall back to existing forensic_news.json
    live_fetch_ok = True
    try:
        # Try a lightweight network check
        requests.get('https://news.google.com', timeout=5)
    except Exception:
        live_fetch_ok = False

    if not live_fetch_ok:
        # OFFLINE: load current forensic_news.json and treat those as validated raw candidates
        try:
            with open('forensic_news.json','r') as f:
                existing = json.load(f)
                for itm in existing.get('items',[]):
                    cat = itm.get('category','')
                    if cat == '🇮🇳 INDIA':
                        raw_candidates['INDIA'].append(itm)
                    else:
                        raw_candidates['GLOBAL'].append(itm)
        except Exception:
            pass
    else:
        # Live collection via Google News RSS for each query
        for q, is_india_query in QUERIES:
            try:
                rss_url = f"https://news.google.com/rss/search?q={urllib.parse.quote_plus(q)}&hl=en-US&gl=US&ceid=US:en"
                f = feedparser.parse(rss_url)
                if getattr(f, 'bozo', False) and not f.entries:
                    # feed parse problem
                    pass
                for e in f.entries:
                    raw_fetched += 1
                    try:
                        title = e.title.split(' - ')[0].strip() if hasattr(e, 'title') else None
                        if not title:
                            continue
                        pub_dt = parse_publication_date(e)
                        if not pub_dt:
                            rejected['invalid_date'].append(e.get('link',''))
                            continue
                        if pub_dt > now:
                            rejected['future'].append(e.get('link',''))
                            continue
                        if not is_within_window(pub_dt, now):
                            rejected['stale'].append(e.get('link',''))
                            continue
                        summary = e.summary[:400] if hasattr(e, 'summary') else ""
                        # Semantic relevance check
                        if not is_india_query and not is_forensic_relevant(title + ' ' + summary):
                            rejected['insufficient_relevance'].append(e.get('link',''))
                            continue
                        url = e.link if hasattr(e, 'link') else None
                        if not url or any(x in url for x in ["example-", "example.com"]):
                            rejected['placeholder_url'].append(url)
                            continue
                        # build candidate
                        itm = {
                            'title': title,
                            'summary': summary,
                            'source': e.source.title if hasattr(e, 'source') else (urllib.parse.urlparse(url).netloc or 'News'),
                            'url': url,
                            'dt': pub_dt,
                        }
                        itm['age_hours'] = (now - pub_dt).total_seconds() / 3600
                        itm['published'] = pub_dt.isoformat().replace('+00:00','Z')
                        itm['category'] = '🇮🇳 INDIA' if any(k in (title + ' ' + summary).lower() for k in KEYWORDS['INDIA']) or ('india' in (url or '').lower()) else '🌎 GLOBAL'

                        # verify accessibility but do not block on occasional failures (best-effort)
                        accessible = fetch_and_validate_url(url)
                        if not accessible:
                            rejected['inaccessible_url'].append(url)
                            continue

                        raw_candidates[ 'INDIA' if itm['category']=='🇮🇳 INDIA' else 'GLOBAL' ].append(itm)

                    except Exception:
                        continue
            except Exception as e:
                failed_sources.append(q)
                continue

    # Deduplication & acceptance
    for scope in ['INDIA','GLOBAL']:
        for itm in raw_candidates.get(scope,[]):
            try:
                title = itm.get('title')
                pub_iso = itm.get('published') if itm.get('published') else (itm.get('dt').isoformat().replace('+00:00','Z') if itm.get('dt') else None)
                if not pub_iso:
                    rejected['invalid_date'].append(itm.get('url'))
                    continue
                # parse published timestamp
                try:
                    pub_dt = datetime.datetime.fromisoformat(pub_iso.replace('Z','+00:00'))
                except Exception:
                    rejected['invalid_date'].append(itm.get('url'))
                    continue
                now = datetime.datetime.now(datetime.timezone.utc)
                if pub_dt > now:
                    rejected['future'].append(itm.get('url'))
                    continue
                if not is_within_window(pub_dt, now):
                    rejected['stale'].append(itm.get('url'))
                    continue

                itm['age_hours'] = (now - pub_dt).total_seconds() / 3600
                # Scoring — assume India query if scope==INDIA
                is_india_query = (scope=='INDIA')
                itm['score'] = score(itm, is_india_query)

                ek = event_key(itm)
                if ek in seen_event_keys:
                    existing = seen_event_keys[ek]
                    # if event duplicate, keep higher score
                    if itm['score'] > existing['score']:
                        seen_event_keys[ek] = itm
                    else:
                        rejected['duplicate'].append(itm.get('url'))
                else:
                    # fuzzy duplicate by title
                    nt = normalize_title(title)
                    dup_found = False
                    for k, existing in list(seen_event_keys.items()):
                        sim = similar(nt, normalize_title(existing.get('title','')))
                        if sim > 0.92:
                            dup_found = True
                            if itm['score'] > existing['score']:
                                seen_event_keys[k] = itm
                            else:
                                rejected['duplicate'].append(itm.get('url'))
                            break
                    if not dup_found:
                        seen_event_keys[ek] = itm

            except Exception:
                rejected['other'].append(itm.get('url'))
                continue

    final_items = list(seen_event_keys.values())
    final_items.sort(key=lambda x: x['score'], reverse=True)
    final_items = final_items[:MAX_RESULTS]

    # Build output JSON
    out = {
        'updated': now.isoformat().replace('+00:00','Z'),
        'total_found': len(final_items),
        'items': []
    }

    for i, itm in enumerate(final_items):
        out_item = {
            'title': itm.get('title'),
            'summary': itm.get('summary'),
            'source': itm.get('source'),
            'published': itm.get('published'),
            'url': itm.get('url'),
            'category': itm.get('category'),
            'score': itm.get('score'),
            'rank': i+1
        }
        out['items'].append(out_item)

    # Write JSON
    tmp = 'forensic_news.tmp.json'
    with open(tmp, 'w') as f:
        json.dump(out, f, indent=2)
    os.replace(tmp, 'forensic_news.json')

    # Diagnostics
    raw_india = [{'title':x.get('title'),'source':x.get('source'),'published':x.get('published'),'url':x.get('url')} for x in raw_candidates.get('INDIA',[])]
    raw_global = [{'title':x.get('title'),'source':x.get('source'),'published':x.get('published'),'url':x.get('url')} for x in raw_candidates.get('GLOBAL',[])]

    accepted_india = [x for x in final_items if x['category']=='🇮🇳 INDIA']
    accepted_global = [x for x in final_items if x['category']=='🌎 GLOBAL']

    diagnostics = {
        'RAW_CANDIDATES': {
            'INDIA': raw_india,
            'GLOBAL': raw_global
        },
        'ACCEPTED': {
            'INDIA': len(accepted_india),
            'GLOBAL': len(accepted_global),
            'TOTAL': len(final_items)
        },
        'REJECTED_COUNTS': {k: len(v) for k,v in rejected.items()},
        'DUPLICATES_REMOVED': len(rejected['duplicate']),
        'FAILED_SOURCES': failed_sources,
        'OLDEST_ACCEPTED': min((x['published'] for x in final_items), default=None),
        'NEWEST_ACCEPTED': max((x['published'] for x in final_items), default=None),
        'ACCEPTED_ITEMS': [
            {
                'rank': i+1,
                'title': itm.get('title'),
                'source': itm.get('source'),
                'published': itm.get('published'),
                'category': itm.get('category'),
                'url': itm.get('url')
            } for i, itm in enumerate(final_items)
        ]
    }

    print(json.dumps(diagnostics, indent=2))


if __name__ == '__main__':
    run()
