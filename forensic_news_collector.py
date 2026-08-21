import feedparser
import json
import datetime
import re
import hashlib
import os

# --- CONFIG ---
KEYWORDS = {
    "INDIA": ["india", "indian", "supreme court", "high court", "nmc", "mohfw", "hyderabad", "delhi", "mumbai", "bengaluru"],
    "PMI": ["postmortem interval", "time since death", "taphonomy", "decomposition"],
    "DNA": ["dna identification", "forensic genetics", "skeletal remains", "disaster victim identification", "dvi"],
    "TOX": ["toxicology", "poisoning", "drug detection", "psychoactive"],
    "LEGAL": ["medico-legal", "medicolegal", "autopsy report", "court evidence", "forensic evidence"],
    "PMCT": ["virtopsy", "postmortem ct", "forensic imaging", "radiology"],
    "TECH": ["ai forensic", "machine learning forensic", "forensic technology", "new forensic method", "invention"]
}

FORENSIC_KEYWORDS = [
    "forensic", "pathology", "autopsy", "dna", "identification", "toxicology", "evidence",
    "postmortem", "medico-legal", "victim identification", "virtopsy", "taphonomy",
    "anthropology", "odontology", "decomposition", "time since death", "dvi"
]

def get_category(text):
    text = text.lower()
    if any(k in text for k in KEYWORDS["INDIA"]): return "🇮🇳 INDIA"
    if any(k in text for k in KEYWORDS["LEGAL"]): return "⚖️ LEGAL"
    if any(k in text for k in KEYWORDS["PMI"]): return "⏱️ PMI"
    if any(k in text for k in KEYWORDS["DNA"]): return "🧬 DNA"
    if any(k in text for k in KEYWORDS["TOX"]): return "🧪 TOX"
    if any(k in text for k in KEYWORDS["PMCT"]): return "🪻 PMCT"
    if any(k in text for k in KEYWORDS["TECH"]): return "🔬 TECH"
    return "🇳🇴 GLOBAL"

def is_forensic_relevant(text):
    """Check if article is actually forensic/medico-legal related."""
    text_lower = text.lower()
    return any(k in text_lower for k in FORENSIC_KEYWORDS)

def parse_publication_date(entry):
    """Extract and parse publication date from RSS entry."""
    try:
        if hasattr(entry, 'published_parsed') and entry.published_parsed:
            return datetime.datetime(*entry.published_parsed[:6], tzinfo=datetime.timezone.utc)
        elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
            return datetime.datetime(*entry.updated_parsed[:6], tzinfo=datetime.timezone.utc)
    except (TypeError, ValueError):
        pass
    return None

def is_within_7_days(pub_dt):
    """HARD FILTER: Reject articles older than 7 days."""
    if not pub_dt:
        return False
    now = datetime.datetime.now(datetime.timezone.utc)
    age_hours = (now - pub_dt).total_seconds() / 3600
    return age_hours <= 168  # 7 days = 168 hours

def score(item, is_india):
    """Score article based on recency, content, and relevance."""
    s = 0
    text = (item['title'] + " " + item['summary']).lower()
    age_hours = item['age_hours']
    
    # Recency scoring (strongly prioritize recent)
    if age_hours < 24: 
        s += 30
    elif age_hours < 48: 
        s += 20
    elif age_hours < 72: 
        s += 15
    elif age_hours < 168:
        s += 5
    
    # Forensic relevance
    if "forensic" in text: 
        s += 25
    if any(k in text for k in ["pathology", "autopsy", "medico-legal", "evidence"]):
        s += 15
    
    # India bonus (but not overwhelming)
    if is_india: 
        s += 10
    
    # Penalize generic crime without forensic angle
    if any(k in text for k in ["arrested", "murdered", "killed", "death"]) and "forensic" not in text:
        s -= 50
    
    return s

def run():
    queries = [
        ("forensic+medicine+India", True),
        ("medico-legal+India", True),
        ("forensic+pathology", False),
        ("forensic+identification", False),
        ("dna+identification", False),
        ("taphonomy", False),
        ("virtopsy", False),
        ("forensic+anthropology", False),
        ("postmortem+interval", False),
    ]
    
    all_items = []
    seen = set()
    
    now = datetime.datetime.now(datetime.timezone.utc)
    
    for q, is_india_query in queries:
        try:
            f = feedparser.parse(f"https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en")
            for e in f.entries:
                try:
                    # Extract title and publication date
                    t = e.title.split(' - ')[0].strip() if e.title else "Unknown"
                    pub_dt = parse_publication_date(e)
                    
                    # HARD REJECT >7 DAYS
                    if not is_within_7_days(pub_dt):
                        continue
                    
                    # HARD REJECT IRRELEVANT (unless India query)
                    summary = e.summary[:200] if hasattr(e, 'summary') else ""
                    if not is_india_query and not is_forensic_relevant(t + " " + summary):
                        continue
                    
                    # DEDUPLICATION
                    uid = hashlib.md5(t.encode()).hexdigest()
                    if uid in seen:
                        continue
                    seen.add(uid)
                    
                    # NORMALIZE & PARSE
                    age_hours = (now - pub_dt).total_seconds() / 3600 if pub_dt else 999999
                    
                    itm = {
                        "title": t,
                        "summary": e.summary[:200] if hasattr(e, 'summary') else "",
                        "source": e.source.title if hasattr(e, 'source') else "News",
                        "published": e.published if hasattr(e, 'published') else "",
                        "url": e.link if hasattr(e, 'link') else "",
                        "dt": pub_dt,
                        "age_hours": age_hours,
                    }
                    
                    # CATEGORIZE
                    itm["category"] = get_category(itm["title"] + itm["summary"])
                    
                    # SCORE
                    itm["score"] = score(itm, is_india_query)
                    
                    all_items.append(itm)
                    
                except (ValueError, TypeError, AttributeError):
                    continue
        except Exception as e:
            continue
    
    # SORT BY SCORE
    all_items.sort(key=lambda x: x['score'], reverse=True)
    
    # SELECT UP TO 40 (but don't force it)
    final_items = all_items[:40]
    
    # Preserve existing feed if no valid stories
    if not final_items:
        return
    
    # BUILD OUTPUT
    out = {
        "updated": datetime.datetime.now().isoformat(),
        "total_found": len(final_items),
        "items": []
    }
    
    for i, itm in enumerate(final_items):
        output_item = {
            "title": itm['title'],
            "summary": itm['summary'],
            "source": itm['source'],
            "published": itm['published'],
            "url": itm['url'],
            "category": itm['category'],
            "score": itm['score'],
            "rank": i + 1
        }
        out['items'].append(output_item)
    
    # WRITE JSON
    with open('forensic_news.json', 'w') as f:
        json.dump(out, f, indent=2)

if __name__ == "__main__":
    run()
