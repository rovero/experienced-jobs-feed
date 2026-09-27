"""
Pulls open roles for experienced engineers from public, no-auth job APIs:

  - Greenhouse job boards   (boards-api.greenhouse.io)
  - Lever job boards        (api.lever.co)
  - Ashby job boards        (api.ashbyhq.com)
  - RemoteOK                (remoteok.com/api)
  - Hacker News "Who is Hiring" (hn.algolia.com)

No email, LinkedIn, or Microsoft account access involved anywhere.
Writes the combined, deduped result to listings.json.
"""
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "companies.yaml"
OUTPUT_PATH = ROOT / "listings.json"

HEADERS = {"User-Agent": "experienced-jobs-feed/1.0 (+personal use, public APIs only)"}
TIMEOUT = 20


def load_config():
    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)
    cfg.setdefault("greenhouse", [])
    cfg.setdefault("lever", [])
    cfg.setdefault("ashby", [])
    cfg.setdefault("seniority_keywords", [])
    cfg.setdefault("exclude_keywords", [])
    return cfg


def matches(title, include_kw, exclude_kw):
    t = title.lower()
    if any(x.lower() in t for x in exclude_kw):
        return False
    if not include_kw:
        return True
    return any(k.lower() in t for k in include_kw)


# ---------------------------------------------------------------- Greenhouse
def fetch_greenhouse(slug, include_kw, exclude_kw):
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    out = []
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            print(f"[greenhouse:{slug}] HTTP {r.status_code}, skipping", file=sys.stderr)
            return out
        for job in r.json().get("jobs", []):
            title = job.get("title", "")
            if not matches(title, include_kw, exclude_kw):
                continue
            location = (job.get("location") or {}).get("name", "Remote/Unspecified")
            out.append({
                "company": slug.replace("-", " ").title(),
                "title": title,
                "location": location,
                "url": job.get("absolute_url"),
                "source": "Greenhouse",
                "posted": job.get("updated_at"),
            })
    except requests.RequestException as e:
        print(f"[greenhouse:{slug}] error: {e}", file=sys.stderr)
    return out


# --------------------------------------------------------------------- Lever
def fetch_lever(slug, include_kw, exclude_kw):
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    out = []
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            print(f"[lever:{slug}] HTTP {r.status_code}, skipping", file=sys.stderr)
            return out
        for job in r.json():
            title = job.get("text", "")
            if not matches(title, include_kw, exclude_kw):
                continue
            categories = job.get("categories", {}) or {}
            location = categories.get("location", "Remote/Unspecified")
            out.append({
                "company": slug.replace("-", " ").title(),
                "title": title,
                "location": location,
                "url": job.get("hostedUrl"),
                "source": "Lever",
                "posted": job.get("createdAt"),
            })
    except requests.RequestException as e:
        print(f"[lever:{slug}] error: {e}", file=sys.stderr)
    return out


# -------------------------------------------------------------------- Ashby
def fetch_ashby(slug, include_kw, exclude_kw):
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    out = []
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            print(f"[ashby:{slug}] HTTP {r.status_code}, skipping", file=sys.stderr)
            return out
        for job in r.json().get("jobs", []):
            title = job.get("title", "")
            if not matches(title, include_kw, exclude_kw):
                continue
            out.append({
                "company": slug.replace("-", " ").title(),
                "title": title,
                "location": job.get("location", "Remote/Unspecified"),
                "url": job.get("jobUrl") or job.get("applyUrl"),
                "source": "Ashby",
                "posted": job.get("publishedAt"),
            })
    except requests.RequestException as e:
        print(f"[ashby:{slug}] error: {e}", file=sys.stderr)
    return out


# ------------------------------------------------------------------ RemoteOK
def fetch_remoteok(include_kw, exclude_kw):
    url = "https://remoteok.com/api"
    out = []
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            print(f"[remoteok] HTTP {r.status_code}, skipping", file=sys.stderr)
            return out
        data = r.json()
        for job in data:
            if not isinstance(job, dict) or "position" not in job:
                continue
            title = job.get("position", "")
            tags = " ".join(job.get("tags", []))
            if not matches(f"{title} {tags}", include_kw, exclude_kw):
                continue
            out.append({
                "company": job.get("company", "Unknown"),
                "title": title,
                "location": job.get("location") or "Remote",
                "url": job.get("url"),
                "source": "RemoteOK",
                "posted": job.get("date"),
            })
    except requests.RequestException as e:
        print(f"[remoteok] error: {e}", file=sys.stderr)
    return out


# --------------------------------------------------- Hacker News "Who's Hiring"
def fetch_hn_whoishiring(include_kw, exclude_kw):
    """Finds the most recent monthly 'Who is Hiring?' thread and scans its
    top-level comments for postings matching the seniority keywords."""
    out = []
    try:
        search_url = (
            "https://hn.algolia.com/api/v1/search_by_date"
            "?tags=story&query=Who%20is%20hiring"
        )
        r = requests.get(search_url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        hits = [h for h in r.json().get("hits", []) if h.get("title", "").startswith("Ask HN: Who is hiring?")]
        if not hits:
            return out
        thread_id = hits[0]["objectID"]
        thread_url = f"https://hn.algolia.com/api/v1/items/{thread_id}"
        r2 = requests.get(thread_url, headers=HEADERS, timeout=TIMEOUT)
        r2.raise_for_status()
        thread = r2.json()

        for c in thread.get("children", []) or []:
            text = (c.get("text") or "")
            if not text:
                continue
            plain = re.sub("<[^<]+?>", " ", text)
            if not matches(plain, include_kw, exclude_kw):
                continue
            first_line = plain.strip().split("\n")[0][:140]
            out.append({
                "company": "See posting",
                "title": first_line,
                "location": "See posting",
                "url": f"https://news.ycombinator.com/item?id={c.get('id')}",
                "source": "HN Who's Hiring",
                "posted": datetime.fromtimestamp(c.get("created_at_i", 0), tz=timezone.utc).isoformat()
                if c.get("created_at_i") else None,
            })
    except requests.RequestException as e:
        print(f"[hn] error: {e}", file=sys.stderr)
    return out


def dedupe(listings):
    seen = set()
    result = []
    for job in listings:
        key = (job.get("url") or "", job.get("title") or "")
        if key in seen:
            continue
        seen.add(key)
        result.append(job)
    return result


def main():
    cfg = load_config()
    include_kw = cfg["seniority_keywords"]
    exclude_kw = cfg["exclude_keywords"]

    all_jobs = []
    for slug in cfg["greenhouse"]:
        all_jobs += fetch_greenhouse(slug, include_kw, exclude_kw)
        time.sleep(0.3)
    for slug in cfg["lever"]:
        all_jobs += fetch_lever(slug, include_kw, exclude_kw)
        time.sleep(0.3)
    for slug in cfg["ashby"]:
        all_jobs += fetch_ashby(slug, include_kw, exclude_kw)
        time.sleep(0.3)

    all_jobs += fetch_remoteok(include_kw, exclude_kw)
    all_jobs += fetch_hn_whoishiring(include_kw, exclude_kw)

    all_jobs = dedupe(all_jobs)
    all_jobs.sort(key=lambda j: j.get("posted") or "", reverse=True)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(all_jobs),
        "jobs": all_jobs,
    }
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2))
    print(f"Wrote {len(all_jobs)} listings to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
