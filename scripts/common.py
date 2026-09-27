"""Shared helpers for the generator scripts: loading config/listings,
categorizing roles, and the years-of-experience overlap filter that powers
the multiple per-level feeds."""
import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
LISTINGS_PATH = ROOT / "listings.json"
CONFIG_PATH = ROOT / "companies.yaml"

CATEGORIES = [
    ("💻 Software Engineering", ["software engineer", "swe", "backend", "frontend",
                                  "full stack", "fullstack", "mobile", "ios", "android"]),
    ("🤖 Data, AI & Machine Learning", ["machine learning", "ml engineer", "ai engineer",
                                         "data scientist", "data engineer", "applied scientist",
                                         "research engineer"]),
    ("🛠️ Infrastructure, Platform & DevOps", ["infrastructure", "platform engineer", "devops",
                                                "sre", "site reliability", "security engineer",
                                                "cloud engineer"]),
    ("🧭 Engineering Management & Leadership", ["engineering manager", "eng manager",
                                                  "director of engineering", "vp of engineering",
                                                  "head of engineering"]),
]
OTHER_LABEL = "💼 Other Engineering Roles"

H1B_ICON = {"yes": "🟢", "no": "🔴", "unknown": "❔"}


def load_listings():
    if LISTINGS_PATH.exists():
        return json.loads(LISTINGS_PATH.read_text())
    return {"generated_at": None, "count": 0, "jobs": []}


def load_feed_config():
    cfg = {}
    if CONFIG_PATH.exists():
        cfg = yaml.safe_load(CONFIG_PATH.read_text()) or {}
    keep_unknown = cfg.get("keep_unknown_years", True)
    feeds = cfg.get("feeds", []) or []
    # "all" is always generated at the repo root, unfiltered by years.
    profiles = [{
        "slug": "",  # root
        "label": "All experience levels",
        "min_years": None,
        "max_years": None,
        "keep_unknown": True,
    }]
    for f in feeds:
        profiles.append({
            "slug": f["slug"],
            "label": f.get("label", f["slug"]),
            "min_years": f.get("min_years"),
            "max_years": f.get("max_years"),
            "keep_unknown": keep_unknown,
        })
    return profiles


def categorize(title):
    t = (title or "").lower()
    for label, keywords in CATEGORIES:
        if any(k in t for k in keywords):
            return label
    return OTHER_LABEL


def age_str(posted_iso):
    if not posted_iso:
        return "—"
    try:
        posted = datetime.fromisoformat(posted_iso.replace("Z", "+00:00"))
    except ValueError:
        return "—"
    days = (datetime.now(timezone.utc) - posted).days
    return "0d" if days < 1 else f"{days}d"


def yoe_matches(job_min, job_max, profile_min, profile_max, keep_unknown):
    """True if a job's detected experience range overlaps a feed profile's
    [min_years, max_years]. None bounds are treated as open-ended."""
    if job_min is None and job_max is None:
        return keep_unknown
    jmin = job_min if job_min is not None else 0
    jmax = job_max if job_max is not None else 99
    pmin = profile_min if profile_min is not None else 0
    pmax = profile_max if profile_max is not None else 99
    return jmin <= pmax and jmax >= pmin


def filter_for_profile(jobs, profile):
    return [
        j for j in jobs
        if yoe_matches(j.get("years_min"), j.get("years_max"),
                        profile["min_years"], profile["max_years"],
                        profile["keep_unknown"])
    ]


def output_dir(profile):
    if not profile["slug"]:
        return ROOT
    d = ROOT / "feeds" / profile["slug"]
    d.mkdir(parents=True, exist_ok=True)
    return d
