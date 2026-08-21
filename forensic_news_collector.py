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

def score(item, is_india):
    s = 0; text = (item['title'] + " " + item['summary']).lower()
    now = datetime.datetime.now(datetime.timezone.utc)
    diff = (now - item['dt']).total_seconds() / 3600
    if diff < 24: s += 25
    elif diff < 48: s += 15
    if "forensic" in text: s += 20
    if is_india: s += 15
    if any(k in text for k in ["arrested", "murdered"]) and "forensic" not in text: s -= 45
    return s

def run():
    queries = [("forensic+medicine+India", True), ("medico-legal", True), ("taphonomy", False), ("virtopsy", False), ("forensic+pathology", False)]
    all_items = []; seen = set()
    for q, ind in queries:
        try:
            f = feedparser.parse(f"https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en")
            for e in f.entries:
                t = e.title.split(' - ')[0]
                uid = hashlib.md5(t.encode()).hexdigest()
                if uid in seen: continue
                seen.add(uid)
                dt = datetime.datetime(*e.published_parsed[:6], tzinfo=datetime.timezone.utc)
                itm = {"title": t, "summary": e.summary[:180], "source": e.source.title if 'source' in e else "News", "published": e.published, "dt": dt, "url": e.link}
                itm["category"] = get_category(itm["title"] + itm["summary"])
                itm["score"] = score(itm, ind)
                all_items.append(itm)
        except: continue
    
    if not all_items: return
    all_items.sort(key=lambda x: x['score'], reverse=True)
    out = {"updated": datetime.datetime.now().isoformat(), "total_found": len(all_items), "items": []}
    for i, itm in enumerate(all_items[:40]):
        itm['rank'] = i + 1; itm.pop('dt'); out['items'].append(itm)
    
    with open('forensic_news.json', 'w') as f: json.dump(out, f, indent=2)

if __name__ == "__main__": run()