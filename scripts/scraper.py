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


# ---------------------------------------------------------------- H1B tagging
# Best-effort text detection. Job descriptions are inconsistent about stating
# sponsorship status at all, so "unknown" is a very common — and honest —
# outcome, not a bug.
SPONSOR_YES_PATTERNS = [
    r"\bwe (?:do |can |are able to |)sponsor\b",
    r"\bvisa sponsorship (?:is )?(?:available|offered|provided)\b",
    r"\bwill sponsor\b",
    r"\bopen to (?:visa )?sponsorship\b",
    r"\bh-?1b sponsorship (?:is )?available\b",
    r"\bsponsor(?:ship)? for (?:work authorization|visas?)\b",
]
SPONSOR_NO_PATTERNS = [
    r"\b(?:not|unable to|cannot|can't|do not|does not|doesn't) (?:currently )?"
    r"(?:provide|offer|sponsor)[\w\s]{0,20}(?:visas?|sponsorships?|h-?1bs?)\b",
    r"\bwithout (?:the need for )?(?:visa )?sponsorship\b",
    r"\bno (?:visa )?sponsorship\b",
    r"\bmust be authorized to work[\w\s,]{0,40}without sponsorship\b",
    r"\bnot eligible for (?:visa )?sponsorship\b",
    r"\bnot able to sponsor\b",
]


def detect_sponsorship(text):
    if not text:
        return "unknown"
    t = re.sub("<[^<]+?>", " ", text).lower()
    for pat in SPONSOR_NO_PATTERNS:
        if re.search(pat, t):
            return "no"
    for pat in SPONSOR_YES_PATTERNS:
        if re.search(pat, t):
            return "yes"
    return "unknown"


def is_known_sponsor(company, known_sponsors):
    if not company or not known_sponsors:
        return False
    c = company.lower()
    return any(k.lower() in c for k in known_sponsors)


def apply_h1b_tag(job, description_text, known_sponsors):
    tag = detect_sponsorship(description_text)
    if tag == "unknown" and is_known_sponsor(job.get("company"), known_sponsors):
        tag = "yes"
    job["h1b_sponsor"] = tag
    return job


# --------------------------------------------------------- YOE (years) tagging
# Also best-effort text parsing — same caveat as H1B: many postings state no
# number at all, and that's tagged years_min/years_max = null (unknown), not
# forced into a bucket.
YOE_RANGE_PATTERNS = [
    # "3-5 years", "3 to 5 years"
    (re.compile(r"\b(\d{1,2})\s*(?:-|–|to)\s*(\d{1,2})\s*\+?\s*years?\b"), "range"),
    # "5+ years"
    (re.compile(r"\b(\d{1,2})\s*\+\s*years?\b"), "plus"),
    # "minimum of 5 years", "at least 5 years", "min. 5 years"
    (re.compile(r"\b(?:minimum(?: of)?|at least|min\.?)\s*(\d{1,2})\s*years?\b"), "plus"),
    # plain "5 years of experience" / "5 years experience"
    (re.compile(r"\b(\d{1,2})\s*years?(?:\s*of)?\s*(?:relevant\s*|professional\s*|industry\s*)?experience\b"), "plus"),
]


def extract_years(text):
    """Returns (min_years, max_years) as ints, or (None, None) if no
    experience figure was found. A '+' or 'minimum'/'at least' phrasing has
    no stated upper bound, so max_years stays None for those."""
    if not text:
        return None, None
    t = re.sub("<[^<]+?>", " ", text).lower()
    for pattern, kind in YOE_RANGE_PATTERNS:
        m = pattern.search(t)
        if not m:
            continue
        if kind == "range":
            lo, hi = int(m.group(1)), int(m.group(2))
            return min(lo, hi), max(lo, hi)
        else:
            return int(m.group(1)), None
    return None, None


def apply_years_tag(job, description_text):
    lo, hi = extract_years(description_text)
    job["years_min"] = lo
    job["years_max"] = hi
    return job


def enrich_job(job, description_text, known_sponsors):
    apply_h1b_tag(job, description_text, known_sponsors)
    apply_years_tag(job, description_text)
    return job


def h1b_filter(jobs, h1b_cfg):
    mode = (h1b_cfg or {}).get("mode", "off")
    if not (h1b_cfg or {}).get("enabled", False) or mode == "off":
        return jobs
    known = h1b_cfg.get("known_sponsors", [])
    out = []
    for j in jobs:
        tag = j.get("h1b_sponsor", "unknown")
        if mode == "exclude_no":
            if tag != "no":
                out.append(j)
        elif mode == "known_sponsors_only":
            if tag == "yes" or is_known_sponsor(j.get("company"), known):
                out.append(j)
        else:
            out.append(j)
    return out


# ---------------------------------------------------------------- Greenhouse
def fetch_greenhouse(slug, include_kw, exclude_kw, known_sponsors):
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
            record = {
                "company": slug.replace("-", " ").title(),
                "title": title,
                "location": location,
                "url": job.get("absolute_url"),
                "source": "Greenhouse",
                "posted": job.get("updated_at"),
            }
            enrich_job(record, job.get("content", ""), known_sponsors)
            out.append(record)
    except requests.RequestException as e:
        print(f"[greenhouse:{slug}] error: {e}", file=sys.stderr)
    return out


# --------------------------------------------------------------------- Lever
def fetch_lever(slug, include_kw, exclude_kw, known_sponsors):
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
            description = job.get("descriptionPlain") or job.get("description", "")
            lists_text = " ".join(
                (item.get("content") or "") for lst in (job.get("lists") or []) for item in [lst]
            )
            record = {
                "company": slug.replace("-", " ").title(),
                "title": title,
                "location": location,
                "url": job.get("hostedUrl"),
                "source": "Lever",
                "posted": job.get("createdAt"),
            }
            enrich_job(record, f"{description} {lists_text}", known_sponsors)
            out.append(record)
    except requests.RequestException as e:
        print(f"[lever:{slug}] error: {e}", file=sys.stderr)
    return out


# -------------------------------------------------------------------- Ashby
def fetch_ashby(slug, include_kw, exclude_kw, known_sponsors):
    # Note: Ashby's public list endpoint doesn't reliably include full
    # description text, so h1b_sponsor for Ashby jobs will often fall back
    # to "unknown" unless the company is in known_sponsors.
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
            description = job.get("descriptionPlain") or job.get("descriptionHtml", "")
            record = {
                "company": slug.replace("-", " ").title(),
                "title": title,
                "location": job.get("location", "Remote/Unspecified"),
                "url": job.get("jobUrl") or job.get("applyUrl"),
                "source": "Ashby",
                "posted": job.get("publishedAt"),
            }
            enrich_job(record, description, known_sponsors)
            out.append(record)
    except requests.RequestException as e:
        print(f"[ashby:{slug}] error: {e}", file=sys.stderr)
    return out


# ------------------------------------------------------------------ RemoteOK
def fetch_remoteok(include_kw, exclude_kw, known_sponsors):
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
            record = {
                "company": job.get("company", "Unknown"),
                "title": title,
                "location": job.get("location") or "Remote",
                "url": job.get("url"),
                "source": "RemoteOK",
                "posted": job.get("date"),
            }
            enrich_job(record, job.get("description", ""), known_sponsors)
            out.append(record)
    except requests.RequestException as e:
        print(f"[remoteok] error: {e}", file=sys.stderr)
    return out


# --------------------------------------------------- Hacker News "Who's Hiring"
def fetch_hn_whoishiring(include_kw, exclude_kw, known_sponsors):
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
            record = {
                "company": "See posting",
                "title": first_line,
                "location": "See posting",
                "url": f"https://news.ycombinator.com/item?id={c.get('id')}",
                "source": "HN Who's Hiring",
                "posted": datetime.fromtimestamp(c.get("created_at_i", 0), tz=timezone.utc).isoformat()
                if c.get("created_at_i") else None,
            }
            enrich_job(record, plain, known_sponsors)
            out.append(record)
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
    h1b_cfg = cfg.get("h1b", {}) or {}
    known_sponsors = h1b_cfg.get("known_sponsors", [])

    all_jobs = []
    for slug in cfg["greenhouse"]:
        all_jobs += fetch_greenhouse(slug, include_kw, exclude_kw, known_sponsors)
        time.sleep(0.3)
    for slug in cfg["lever"]:
        all_jobs += fetch_lever(slug, include_kw, exclude_kw, known_sponsors)
        time.sleep(0.3)
    for slug in cfg["ashby"]:
        all_jobs += fetch_ashby(slug, include_kw, exclude_kw, known_sponsors)
        time.sleep(0.3)

    all_jobs += fetch_remoteok(include_kw, exclude_kw, known_sponsors)
    all_jobs += fetch_hn_whoishiring(include_kw, exclude_kw, known_sponsors)

    all_jobs = dedupe(all_jobs)
    pre_filter_count = len(all_jobs)
    all_jobs = h1b_filter(all_jobs, h1b_cfg)
    dropped = pre_filter_count - len(all_jobs)
    all_jobs.sort(key=lambda j: j.get("posted") or "", reverse=True)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(all_jobs),
        "jobs": all_jobs,
    }
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2))
    print(f"Wrote {len(all_jobs)} listings to {OUTPUT_PATH} "
          f"({dropped} dropped by h1b filter, mode={h1b_cfg.get('mode', 'off')})")


if __name__ == "__main__":
    main()
