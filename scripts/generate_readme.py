"""Renders README.md from listings.json in the SimplifyJobs New-Grad-Positions
style: emoji category headers, one markdown table per category, a legend,
and a last-updated timestamp. GitHub renders these tables responsively
(they scroll horizontally on narrow/mobile viewports) with no extra CSS."""
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LISTINGS_PATH = ROOT / "listings.json"
README_PATH = ROOT / "README.md"

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


def categorize(title):
    t = title.lower()
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
    delta = datetime.now(timezone.utc) - posted
    days = delta.days
    if days < 1:
        return "0d"
    return f"{days}d"


def build_table(jobs):
    lines = [
        "| Company | Role | Location | Source | Posted |",
        "|---|---|---|---|---|",
    ]
    for j in jobs:
        company = (j.get("company") or "—").replace("|", "-")
        title = (j.get("title") or "—").replace("|", "-")
        location = (j.get("location") or "—").replace("|", "-")
        url = j.get("url")
        source = j.get("source", "—")
        posted = age_str(j.get("posted"))
        role_cell = f"[{title}]({url})" if url else title
        lines.append(f"| {company} | {role_cell} | {location} | {source} | {posted} |")
    return "\n".join(lines)


def main():
    data = json.loads(LISTINGS_PATH.read_text()) if LISTINGS_PATH.exists() else {
        "generated_at": None, "count": 0, "jobs": []
    }
    jobs = data.get("jobs", [])

    grouped = {}
    for j in jobs:
        grouped.setdefault(categorize(j.get("title", "")), []).append(j)

    generated_at = data.get("generated_at")
    ts_display = generated_at or "never — run the workflow"

    lines = []
    lines.append("# Experienced Engineer Job Feed")
    lines.append("")
    lines.append(
        "Auto-updated list of open roles for experienced (senior/staff/principal+) "
        "engineers, pulled directly from public Greenhouse, Lever, and Ashby job-board "
        "APIs, plus RemoteOK and the Hacker News \"Who is Hiring?\" thread. No email, "
        "LinkedIn, or Microsoft account access is used anywhere in this pipeline."
    )
    lines.append("")
    lines.append(f"**Last updated:** {ts_display} · **Total open roles:** {data.get('count', 0)}")
    lines.append("")
    lines.append(
        "🤖 **Agents/scripts:** don't scrape this README — read "
        "[`feed.json`](./feed.json) or [`feed.xml`](./feed.xml) instead. "
        "See [Consuming this feed](#consuming-this-feed) below."
    )
    lines.append("")

    # Table of contents
    lines.append("## Browse roles by category")
    lines.append("")
    for label, _ in CATEGORIES:
        count = len(grouped.get(label, []))
        anchor = label.split(" ", 1)[1].lower().replace(" ", "-").replace(",", "").replace("&", "").replace("--", "-")
        lines.append(f"- {label} ({count})")
    other_count = len(grouped.get(OTHER_LABEL, []))
    lines.append(f"- {OTHER_LABEL} ({other_count})")
    lines.append("")
    lines.append("---")
    lines.append("")

    for label, _ in CATEGORIES + [(OTHER_LABEL, [])]:
        cat_jobs = grouped.get(label, [])
        lines.append(f"## {label}")
        lines.append("")
        if cat_jobs:
            lines.append(build_table(cat_jobs))
        else:
            lines.append("*No open roles matched this category right now.*")
        lines.append("")
        lines.append("[⬆️ Back to top](#experienced-engineer-job-feed)")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## Consuming this feed")
    lines.append("")
    lines.append("This repo publishes two machine-readable files, regenerated on every run:")
    lines.append("")
    lines.append("- `feed.json` — flat JSON array, easiest for a script or agent to parse")
    lines.append("- `feed.xml` — standard RSS 2.0, works with any feed reader")
    lines.append("")
    lines.append(
        "If this repo is public, both are reachable without auth at:\n"
        "```\n"
        "https://raw.githubusercontent.com/<your-username>/<your-repo>/main/feed.json\n"
        "https://raw.githubusercontent.com/<your-username>/<your-repo>/main/feed.xml\n"
        "```"
    )
    lines.append("")
    lines.append("## Configuring what gets tracked")
    lines.append("")
    lines.append(
        "Edit [`companies.yaml`](./companies.yaml) — add Greenhouse/Lever/Ashby company "
        "slugs and tweak the seniority/exclude keyword lists. The next scheduled run "
        "picks up your changes automatically."
    )
    lines.append("")

    README_PATH.write_text("\n".join(lines))
    print(f"Wrote {README_PATH}")


if __name__ == "__main__":
    main()
