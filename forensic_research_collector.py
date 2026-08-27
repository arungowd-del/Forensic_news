"""
Forensic Research Paper Collector
Fetches recent genuine research papers from PubMed/NCBI E-utilities
relevant to forensic medicine and related disciplines.

Rolling 30-day window, deduplication by PMID + normalized title,
sorted newest first. Max 30 high-quality papers.
"""

import requests
import json
import datetime
import re
import time
import os
import urllib.parse

# --- CONFIGURATION ---
MAX_AGE_DAYS = 30
MAX_RESULTS = 30

# PubMed E-utilities base URL
EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

# Forensic-relevant MeSH terms and keywords for PubMed
# These directly target forensic medicine, pathology, toxicology, genetics, etc.
PUBMED_QUERIES = [
    # Forensic Medicine & Pathology
    'forensic medicine OR forensic pathology OR forensic autopsy',
    'autopsy postmortem examination pathology',
    'virtual autopsy virtopsy postmortem imaging',
    'postmortem CT "postmortem computed tomography"',
    
    # Forensic Toxicology & Drugs
    'forensic toxicology toxicological analysis',
    'postmortem toxicology postmortem drug analysis',
    'novel psychoactive substances NPS forensic toxicology',
    'blood alcohol postmortem forensic',
    
    # Forensic Genetics & DNA
    'forensic genetics DNA profiling identification',
    'investigative genetic genealogy DNA matching',
    'genetic genealogy criminal investigation',
    'DNA sequence variation forensic',
    
    # Forensic Anthropology & DVI
    'forensic anthropology skeleton identification',
    'disaster victim identification DVI mass fatality',
    'mass grave forensic anthropology',
    'human remains decomposition taphonomy',
    
    # Forensic Odontology & Entomology
    'forensic odontology dental identification',
    'forensic entomology postmortem interval',
    'insect colonization decomposition',
    
    # Forensic Imaging & Digital
    'forensic imaging radiography postmortem',
    'digital forensics crime scene investigation',
    'forensic image analysis pattern recognition',
    
    # Medicolegal & Forensic Science General
    'medicolegal medico-legal forensic',
    'forensic science research methodology',
    'postmortem examination medico-legal',
]

# MeSH qualifiers to enhance specificity
MESH_QUALIFIERS = [
    'forensic pathology[MeSH Terms]',
    'autopsy[MeSH Terms]',
    'toxicology[MeSH Terms]',
    'forensic dentistry[MeSH Terms]',
    'anthropology[MeSH Terms]',
    'DNA[MeSH Terms]',
    'radiography[MeSH Terms]',
]

STOPWORDS = set([
    "the","a","an","of","in","on","for","and","to","with","after","at","by","from",
    "or","is","are","was","were","be","been","have","has","do","does","did","will",
    "would","could","should","may","might","can","clinical","case","study","method"
])

def normalize_title(t):
    """Normalize title for deduplication."""
    if not t:
        return ""
    t = t.lower()
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    parts = [w for w in t.split() if w not in STOPWORDS and len(w) > 2]
    return " ".join(parts)


def parse_pubmed_date(date_str):
    """
    Parse PubMed date strings (YYYY, YYYY MM, YYYY MM DD).
    Return datetime at UTC, treating incomplete dates as first day of period.
    """
    if not date_str:
        return None
    
    date_str = date_str.strip()
    
    # Try YYYY-MM-DD ISO format (some APIs return this)
    try:
        dt = datetime.datetime.fromisoformat(date_str)
        return dt.replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        pass
    
    # Parse space-separated formats: "YYYY MM DD" or "YYYY MM" or "YYYY"
    parts = date_str.split()
    try:
        year = int(parts[0])
        month = int(parts[1]) if len(parts) > 1 else 1
        day = int(parts[2]) if len(parts) > 2 else 1
        dt = datetime.datetime(year, month, day, 0, 0, 0, tzinfo=datetime.timezone.utc)
        return dt
    except (ValueError, IndexError):
        return None


def is_within_window(pub_date, now, max_days=MAX_AGE_DAYS):
    """Check if publication date is within the rolling window."""
    if not pub_date:
        return False
    age = (now - pub_date).days
    return 0 <= age <= max_days


def fetch_pubmed_papers(query_str, retmax=100):
    """
    Fetch papers from PubMed using E-utilities.
    Returns list of paper dicts with title, authors, date, journal, pmid, url.
    """
    papers = []
    
    # Calculate date range (last 30 days)
    now = datetime.datetime.now(datetime.timezone.utc)
    start_date = (now - datetime.timedelta(days=MAX_AGE_DAYS)).strftime("%Y/%m/%d")
    end_date = now.strftime("%Y/%m/%d")
    
    # Construct PubMed query with date filter and article type constraints
    # Prioritize research articles, reviews, and case reports
    search_query = f'({query_str}) AND ({start_date}[PDat] : {end_date}[PDat]) AND (research[Publication Type] OR review[Publication Type] OR systematic review[Publication Type] OR case report[Publication Type] OR "journal article"[Publication Type])'
    
    try:
        # Step 1: Search for matching UIDs
        search_url = f"{EUTILS_BASE}/esearch.fcgi"
        search_params = {
            'db': 'pubmed',
            'term': search_query,
            'retmax': retmax,
            'rettype': 'json',
            'api_key': os.environ.get('NCBI_API_KEY', ''),  # Optional: use API key if available
        }
        
        search_resp = requests.get(search_url, params=search_params, timeout=10)
        search_resp.raise_for_status()
        search_data = search_resp.json()
        
        uids = search_data.get('esearchresult', {}).get('idlist', [])
        if not uids:
            return papers
        
        # Add small delay to respect rate limiting
        time.sleep(0.5)
        
        # Step 2: Fetch full records for these UIDs
        fetch_url = f"{EUTILS_BASE}/efetch.fcgi"
        fetch_params = {
            'db': 'pubmed',
            'id': ','.join(uids),
            'rettype': 'json',
            'api_key': os.environ.get('NCBI_API_KEY', ''),
        }
        
        fetch_resp = requests.get(fetch_url, params=fetch_params, timeout=15)
        fetch_resp.raise_for_status()
        fetch_data = fetch_resp.json()
        
        # Parse articles from response
        articles = fetch_data.get('result', {}).get('uids', [])
        for uid in articles:
            if uid == 'uids':
                continue
            
            article = fetch_data.get('result', {}).get(uid, {})
            if not article:
                continue
            
            try:
                # Extract fields
                pmid = uid
                title = None
                authors = None
                published = None
                journal = None
                
                # Try to get title
                if 'title' in article:
                    title = article['title']
                elif 'headline' in article:
                    title = article['headline']
                
                if not title or len(title) < 10:
                    continue
                
                # Get authors
                author_list = article.get('authors', [])
                if author_list:
                    author_names = [a.get('name') for a in author_list if a.get('name')]
                    authors = ', '.join(author_names[:5])  # First 5 authors
                else:
                    authors = 'N/A'
                
                # Get publication date
                pub_date_str = article.get('pubdate')
                if not pub_date_str and 'sortpubdate' in article:
                    pub_date_str = article['sortpubdate'].split(' ')[0] if article['sortpubdate'] else None
                
                if not pub_date_str:
                    continue
                
                published = parse_pubmed_date(pub_date_str)
                if not published or not is_within_window(published, now):
                    continue
                
                # Get journal
                journal = article.get('source', 'PubMed')
                
                # Build URL
                url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
                
                paper = {
                    'title': title,
                    'authors': authors,
                    'published': published.strftime("%Y-%m-%d"),
                    'journal': journal,
                    'pmid': pmid,
                    'url': url,
                    'dt': published,
                }
                
                papers.append(paper)
            
            except Exception as e:
                continue
        
        return papers
    
    except requests.RequestException as e:
        print(f"  ✗ Query '{query_str}' failed: {e}")
        return []


def run():
    """Main collector: fetch, deduplicate, sort, and save research papers."""
    
    now = datetime.datetime.now(datetime.timezone.utc)
    all_papers = []
    seen_pmids = set()
    seen_titles = {}
    
    print(f"[{now.isoformat()}] Starting forensic research paper collection...")
    print(f"  Collection window: last {MAX_AGE_DAYS} days")
    print(f"  Target: up to {MAX_RESULTS} high-quality papers\n")
    
    # Fetch papers for each query
    total_fetched = 0
    failed_queries = []
    
    for i, query in enumerate(PUBMED_QUERIES, 1):
        print(f"[{i}/{len(PUBMED_QUERIES)}] Query: {query[:60]}...")
        
        papers = fetch_pubmed_papers(query, retmax=50)
        total_fetched += len(papers)
        
        if papers:
            print(f"  ✓ Found {len(papers)} papers")
        else:
            print(f"  ✗ No papers found")
            failed_queries.append(query)
        
        # Add small delay between queries to respect rate limiting
        if i < len(PUBMED_QUERIES):
            time.sleep(1.0)
    
    print(f"\n  Total papers fetched: {total_fetched}")
    print(f"  Queries that returned no results: {len(failed_queries)}")
    
    # Deduplication phase
    print(f"\n[Deduplication]")
    rejected = {
        'duplicate_pmid': 0,
        'duplicate_title': 0,
        'other': 0,
    }
    
    for paper in sorted(all_papers, key=lambda p: p['dt'], reverse=True):
        pmid = paper.get('pmid')
        title = paper.get('title', '')
        
        # Deduplicate by PMID first
        if pmid and pmid in seen_pmids:
            rejected['duplicate_pmid'] += 1
            continue
        
        if pmid:
            seen_pmids.add(pmid)
        
        # Deduplicate by normalized title
        norm_title = normalize_title(title)
        if norm_title in seen_titles:
            rejected['duplicate_title'] += 1
            continue
        
        seen_titles[norm_title] = pmid or title
        all_papers.append(paper)
    
    # Fetch papers and build all_papers list during the loop
    all_papers = []
    seen_pmids_actual = set()
    seen_titles_actual = {}
    
    for i, query in enumerate(PUBMED_QUERIES, 1):
        print(f"[{i}/{len(PUBMED_QUERIES)}] Query: {query[:60]}...")
        
        papers = fetch_pubmed_papers(query, retmax=50)
        
        if papers:
            print(f"  ✓ Found {len(papers)} papers")
            
            for paper in papers:
                pmid = paper.get('pmid')
                title = paper.get('title', '')
                
                # Deduplicate by PMID first
                if pmid and pmid in seen_pmids_actual:
                    rejected['duplicate_pmid'] += 1
                    continue
                
                if pmid:
                    seen_pmids_actual.add(pmid)
                
                # Deduplicate by normalized title
                norm_title = normalize_title(title)
                if norm_title in seen_titles_actual:
                    rejected['duplicate_title'] += 1
                    continue
                
                seen_titles_actual[norm_title] = pmid or title
                all_papers.append(paper)
                total_fetched += 1
        else:
            print(f"  ✗ No papers found")
            failed_queries.append(query)
        
        # Add small delay between queries to respect rate limiting
        if i < len(PUBMED_QUERIES):
            time.sleep(1.0)
    
    # Sort by publication date (newest first)
    all_papers.sort(key=lambda p: p['dt'], reverse=True)
    all_papers = all_papers[:MAX_RESULTS]
    
    print(f"\n[Summary]")
    print(f"  Total papers fetched: {total_fetched}")
    print(f"  Duplicates by PMID: {rejected['duplicate_pmid']}")
    print(f"  Duplicates by title: {rejected['duplicate_title']}")
    print(f"  Final accepted papers: {len(all_papers)}")
    
    if all_papers:
        oldest = min(p['dt'] for p in all_papers)
        newest = max(p['dt'] for p in all_papers)
        print(f"  Date range: {oldest.strftime('%Y-%m-%d')} to {newest.strftime('%Y-%m-%d')}")
    
    # Build output JSON
    output = {
        'updated': now.isoformat().replace('+00:00', 'Z'),
        'source': 'PubMed',
        'items': []
    }
    
    for paper in all_papers:
        output['items'].append({
            'title': paper['title'],
            'authors': paper['authors'],
            'published': paper['published'],
            'journal': paper['journal'],
            'pmid': paper['pmid'],
            'url': paper['url'],
        })
    
    # Write atomically
    tmp_file = 'forensic_research.tmp.json'
    try:
        with open(tmp_file, 'w') as f:
            json.dump(output, f, indent=2)
        os.replace(tmp_file, 'forensic_research.json')
        print(f"\n✓ Written forensic_research.json ({len(all_papers)} items)")
    except Exception as e:
        print(f"\n✗ Failed to write JSON: {e}")
        if os.path.exists(tmp_file):
            os.remove(tmp_file)
        raise


if __name__ == '__main__':
    run()
