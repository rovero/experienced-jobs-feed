"""
Pulls open roles for experienced engineers from public, no-auth job APIs:

  - Greenhouse job boards   (boards-api.greenhouse.io)
  - Lever job boards        (api.lever.co)
  - Ashby job boards        (api.ashbyhq.com)
  - SmartRecruiters boards  (api.smartrecruiters.com)
  - Radancy/TalentBrew sites (e.g. jobs.paloaltonetworks.com)
  - RemoteOK                (remoteok.com/api)
  - Hacker News "Who is Hiring" (hn.algolia.com)

No email, LinkedIn, or Microsoft account access involved anywhere.
Writes the combined, deduped result to listings.json.
"""
import json
import re
import sys
import time
import html
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

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
    cfg.setdefault("workday", [])
    cfg.setdefault("smartrecruiters", [])
    cfg.setdefault("radancy", [])
    cfg.setdefault("workday_max_jobs_per_tenant", 200)
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


# ------------------------------------------------------------------ Radancy
# Radancy (formerly TMP Worldwide / TalentBrew) powers corporate career sites
# such as jobs.paloaltonetworks.com and jobs.citi.com. The search page is a
# JS shell; the data comes from a public, unauthenticated JSON endpoint:
#   POST {base}/{locale}/search-jobs/resultspost   (JSON body, see below)
# which returns {"results": "<html cards>", "filters": "...", "hasJobs": bool}.
# (The GET variant /search-jobs/results answers but returns no cards, and the
# POST requires contentType application/json — form-encoded posts get 400.)
#
# Job cards look like:
#   <li class="section29__search-results-li">
#     <a href="/en/job/<city>/<slug>/<orgId>/<jobId>" data-job-id="...">
#       <h2 class="section29__search-results-job-title">Title</h2>
#       ... <span class="section29__result-location newLoc">City, ST</span>
# Cards carry NO posted date. The job detail page embeds JSON-LD with
# "datePosted" (e.g. "2026-9-16") and the full "description" HTML, so this
# fetcher loads the detail page for title-matched jobs only — one extra
# request per match, which also gives us real sponsorship/years text.
RADANCY_CRITERIA = {
    # Mirrors the `criteria` object the site's own search.js POSTs (all fields
    # matter: trimmed-down payloads return empty results for blank keywords).
    "ActiveFacetID": 0, "Distance": 50, "RadiusUnitType": None,
    "Keywords": "", "Location": "", "Latitude": None, "Longitude": None,
    "ShowRadius": False, "IsPagination": "False",
    "CustomFacetName": "", "FacetTerm": "", "FacetType": 0, "FacetFilters": [],
    "SearchResultsModuleName": "", "SearchFiltersModuleName": None,
    "SortCriteria": 0, "SortDirection": 0, "SearchType": 5,
    "CategoryFacetTerm": "", "CategoryFacetType": "",
    "LocationFacetTerm": "", "LocationFacetType": "",
    "KeywordType": "", "LocationType": "", "LocationPath": "",
    "OrganizationIds": "", "RefinedKeywords": [],
    "TotalContentResults": 0, "AjaxCharLimit": 4096,
    "SearchOrderCriteriaWithNoDirection": [0, 6],
}

RADANCY_CARD_RE = re.compile(
    r'<li class="section29__search-results-li">\s*'
    r'<a[^>]*href="([^"]+)"[^>]*>\s*'
    r'<h2[^>]*>(.*?)</h2>(.*?)</li>', re.S)
RADANCY_LOC_RE = re.compile(
    r'<span class="section29__result-location[^"]*">(.*?)</span>', re.S)
RADANCY_JSONLD_RE = re.compile(
    r'<script type="application/ld\+json">(.*?)</script>', re.S)


def _radancy_job_detail(session, url, label):
    """Fetch a Radancy job page; return (date_posted_iso, description_html)."""
    try:
        r = session.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            return None, ""
        m = RADANCY_JSONLD_RE.search(r.text)
        if not m:
            return None, ""
        data = json.loads(m.group(1))
        posted = None
        raw_date = data.get("datePosted") or ""
        dm = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", str(raw_date))
        if dm:
            posted = f"{dm.group(1)}-{int(dm.group(2)):02d}-{int(dm.group(3)):02d}"
        return posted, data.get("description") or ""
    except (requests.RequestException, ValueError) as e:
        print(f"[radancy:{label}] detail error: {e}", file=sys.stderr)
        return None, ""


def fetch_radancy(entry, include_kw, exclude_kw, known_sponsors,
                  max_jobs=500):
    if isinstance(entry, dict):
        base = (entry.get("base") or "").rstrip("/")
        locale = entry.get("locale") or "en"
        display = entry.get("name")
    else:
        base = entry.rstrip("/")
        locale = "en"
        display = None
    label = display or base
    out = []
    try:
        session = requests.Session()
        session.headers.update(HEADERS)
        # The SearchResultsModuleName must match the site's search module or
        # blank-keyword searches come back empty. Discover it from the page
        # (data-search-results-module-name="..."); allow a config override.
        module_name = (entry.get("module") if isinstance(entry, dict)
                       else None) or ""
        if not module_name:
            try:
                pg = session.get(f"{base}/{locale}/search-jobs",
                                 headers=HEADERS, timeout=TIMEOUT)
                m = re.search(r'data-search-results-module-name="([^"]+)"',
                              pg.text)
                if m:
                    module_name = m.group(1)
            except requests.RequestException as e:
                print(f"[radancy:{label}] module discovery failed: {e}",
                      file=sys.stderr)
        endpoint = f"{base}/{locale}/search-jobs/resultspost"
        page = 1
        per_page = 50
        fetched = 0
        total_pages = None
        while fetched < max_jobs:
            criteria = dict(RADANCY_CRITERIA,
                            SearchResultsModuleName=module_name,
                            CurrentPage=page, RecordsPerPage=per_page)
            r = session.post(
                endpoint, json=criteria,
                headers={"Accept": "application/json",
                         "X-Requested-With": "XMLHttpRequest",
                         "Referer": f"{base}/{locale}/search-jobs"},
                timeout=TIMEOUT)
            if r.status_code != 200:
                print(f"[radancy:{label}] HTTP {r.status_code} on page {page}, "
                      f"stopping", file=sys.stderr)
                break
            payload = r.json()
            cards_html = payload.get("results") or ""
            if total_pages is None:
                tm = re.search(r'data-total-pages="(\d+)"', cards_html)
                total_pages = int(tm.group(1)) if tm else 1
            cards = RADANCY_CARD_RE.findall(cards_html)
            if not cards:
                break
            for href, title_html, rest in cards:
                title = html.unescape(
                    re.sub(r"<[^>]+>", "", title_html).strip())
                if not matches(title, include_kw, exclude_kw):
                    continue
                loc_m = RADANCY_LOC_RE.search(rest)
                location = html.unescape(
                    re.sub(r"\s+",
                           " ", re.sub(r"<[^>]+>", "", loc_m.group(1))).strip()
                    if loc_m else "Unspecified")
                job_url = href if href.startswith("http") else base + href
                posted, description = _radancy_job_detail(session, job_url,
                                                         label)
                time.sleep(0.3)
                record = {
                    "company": display or base.split("//")[-1].split(".")[0].title(),
                    "title": title,
                    "location": location,
                    "url": job_url,
                    "source": "Radancy",
                    "posted": posted,
                }
                enrich_job(record, description, known_sponsors)
                out.append(record)
            fetched += len(cards)
            page += 1
            if total_pages is not None and page > total_pages:
                break
            time.sleep(0.3)
    except requests.RequestException as e:
        print(f"[radancy:{label}] error: {e}", file=sys.stderr)
    except (ValueError, KeyError) as e:
        print(f"[radancy:{label}] unexpected response shape: {e}",
              file=sys.stderr)
    return out


# ------------------------------------------------------- SmartRecruiters
# Public, unauthenticated API:
#   GET https://api.smartrecruiters.com/v1/companies/{company}/postings?limit=100&offset=0
# Response shape:
#   {"content": [{"id": "744000001", "name": "Software Engineer",
#                 "releasedDate": "2026-01-01T00:00:00.000Z",
#                 "location": {"city": "Austin", "region": "TX", "country": "us",
#                              "remote": false},
#                 "company": {"name": "Acme Corp"}}],
#    "totalFound": N, "offset": 0, "limit": 100}
# Posting page: https://jobs.smartrecruiters.com/{web_slug}/{id}
# (resolves to the canonical posting URL).
#
# IMPORTANT: {company} is SmartRecruiters' internal company identifier, which
# is NOT always the same as the jobs.smartrecruiters.com/{slug} web slug.
# The API answers HTTP 200 with an empty list for unknown identifiers, so a
# typo fails silently — this fetcher logs a warning when totalFound == 0.
# To find the identifier, open any live posting for the company and check
# the apply URL, or ask the company's recruiting team.
#
# Like Workday/Ashby, the list endpoint doesn't return full description text
# (that would need one extra request per posting:
#  GET /v1/companies/{company}/postings/{id} -> jobAd.sections), so
# h1b_sponsor/years default to "unknown" here unless the company is in
# known_sponsors.
def fetch_smartrecruiters(entry, include_kw, exclude_kw, known_sponsors,
                           max_jobs=500):
    if isinstance(entry, dict):
        company_id = entry.get("id", "")
        web_slug = entry.get("slug") or company_id
        display = entry.get("name")
    else:
        company_id = web_slug = entry
        display = None
    out = []
    try:
        offset = 0
        limit = 100
        total = None
        while offset < max_jobs:
            url = (f"https://api.smartrecruiters.com/v1/companies/{company_id}"
                   f"/postings?limit={limit}&offset={offset}")
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code != 200:
                print(f"[smartrecruiters:{company_id}] HTTP {r.status_code}, "
                      f"stopping", file=sys.stderr)
                break
            data = r.json()
            if total is None:
                total = data.get("totalFound", 0)
                if total == 0:
                    # The API returns 200 + empty for unknown identifiers too,
                    # so this may mean a wrong company id rather than no jobs.
                    print(f"[smartrecruiters:{company_id}] 0 postings returned "
                          f"-- verify the company identifier", file=sys.stderr)
            postings = data.get("content", [])
            if not postings:
                break
            for job in postings:
                title = job.get("name", "")
                if not matches(title, include_kw, exclude_kw):
                    continue
                loc = job.get("location") or {}
                parts = [loc.get("city"), loc.get("region"), loc.get("country")]
                location = ", ".join(p for p in parts if p) or (
                    "Remote" if loc.get("remote") else "Unspecified")
                company = (display or (job.get("company") or {}).get("name")
                           or company_id)
                record = {
                    "company": company,
                    "title": title,
                    "location": location,
                    "url": f"https://jobs.smartrecruiters.com/{web_slug}/{job.get('id')}",
                    "source": "SmartRecruiters",
                    "posted": job.get("releasedDate"),
                }
                enrich_job(record, "", known_sponsors)
                out.append(record)
            offset += limit
            if total is not None and offset >= total:
                break
            time.sleep(0.3)
    except requests.RequestException as e:
        print(f"[smartrecruiters:{company_id}] error: {e}", file=sys.stderr)
    except (ValueError, KeyError) as e:
        print(f"[smartrecruiters:{company_id}] unexpected response shape: {e}",
              file=sys.stderr)
    return out


# ------------------------------------------------------------------- Workday
# Microsoft, Salesforce, and a large share of Fortune 500s run their careers
# site on Workday rather than Greenhouse/Lever/Ashby. Workday career pages
# are a JS shell that reads from a public, unauthenticated search endpoint —
# this is that endpoint, not an official/documented Workday API.
def parse_workday_url(url):
    """https://microsoft.wd1.myworkdayjobs.com/en-US/microsoftcareers ->
    (scheme, host, tenant, site_name, path_prefix). path_prefix keeps any
    locale segment (en-US) so job URLs can be reconstructed correctly."""
    parsed = urlparse(url)
    host = parsed.netloc
    tenant = host.split(".")[0]
    path_parts = [p for p in parsed.path.split("/") if p]
    site_name = path_parts[-1] if path_parts else ""
    path_prefix = "/" + "/".join(path_parts) if path_parts else ""
    return parsed.scheme, host, tenant, site_name, path_prefix


def parse_relative_posted(text):
    """Workday only gives relative labels like 'Posted 3 Days Ago', not an
    ISO date — this approximates one so sorting/age-display still work."""
    if not text:
        return None
    t = text.lower()
    now = datetime.now(timezone.utc)
    if "today" in t:
        return now.isoformat()
    if "yesterday" in t:
        return (now - timedelta(days=1)).isoformat()
    m = re.search(r"(\d+)\+?\s*day", t)
    if m:
        return (now - timedelta(days=int(m.group(1)))).isoformat()
    m = re.search(r"(\d+)\+?\s*month", t)
    if m:
        return (now - timedelta(days=int(m.group(1)) * 30)).isoformat()
    return None


def fetch_workday(url, include_kw, exclude_kw, known_sponsors, max_jobs=200):
    out = []
    try:
        scheme, host, tenant, site_name, path_prefix = parse_workday_url(url)
        if not site_name:
            print(f"[workday:{url}] couldn't parse a site name from this URL, skipping", file=sys.stderr)
            return out
        api_url = f"{scheme}://{host}/wday/cxs/{tenant}/{site_name}/jobs"
        company_label = tenant.replace("-", " ").title()
        offset = 0
        limit = 20
        while offset < max_jobs:
            body = {"appliedFacets": {}, "limit": limit, "offset": offset, "searchText": ""}
            r = requests.post(api_url, json=body,
                               headers={**HEADERS, "Content-Type": "application/json"},
                               timeout=TIMEOUT)
            if r.status_code != 200:
                print(f"[workday:{tenant}] HTTP {r.status_code}, stopping", file=sys.stderr)
                break
            data = r.json()
            postings = data.get("jobPostings", [])
            if not postings:
                break
            for job in postings:
                title = job.get("title", "")
                if not matches(title, include_kw, exclude_kw):
                    continue
                bullets = job.get("bulletFields") or []
                location = bullets[0] if bullets else "Unspecified"
                ext_path = job.get("externalPath", "")
                job_url = f"{scheme}://{host}{path_prefix}{ext_path}" if ext_path else url
                record = {
                    "company": company_label,
                    "title": title,
                    "location": location,
                    "url": job_url,
                    "source": "Workday",
                    "posted": parse_relative_posted(job.get("postedOn")),
                }
                # Workday's list endpoint doesn't return full description
                # text (would need one extra request per job), so h1b/years
                # default to unknown here unless the company is in
                # known_sponsors — same tradeoff as Ashby.
                enrich_job(record, "", known_sponsors)
                out.append(record)
            total = data.get("total", 0)
            offset += limit
            if offset >= total:
                break
            time.sleep(0.3)
    except requests.RequestException as e:
        print(f"[workday:{url}] error: {e}", file=sys.stderr)
    except (ValueError, KeyError) as e:
        print(f"[workday:{url}] unexpected response shape: {e}", file=sys.stderr)
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
    for url in cfg["workday"]:
        all_jobs += fetch_workday(url, include_kw, exclude_kw, known_sponsors,
                                   max_jobs=cfg["workday_max_jobs_per_tenant"])
        time.sleep(0.3)
    for entry in cfg["smartrecruiters"]:
        all_jobs += fetch_smartrecruiters(entry, include_kw, exclude_kw,
                                          known_sponsors)
        time.sleep(0.3)
    for entry in cfg["radancy"]:
        all_jobs += fetch_radancy(entry, include_kw, exclude_kw,
                                  known_sponsors)
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
