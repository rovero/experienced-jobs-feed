"""Renders one README.md per feed profile, in the SimplifyJobs
New-Grad-Positions style: emoji category headers, one markdown table per
category, a legend, a last-updated timestamp. GitHub renders these tables
responsively (they scroll horizontally on narrow/mobile viewports) with no
extra CSS.

The "all experience levels" profile is written to the repo root as before.
Each configured years-of-experience feed (companies.yaml -> feeds:) gets its
own README at feeds/<slug>/README.md — GitHub auto-displays a folder's
README.md when you browse into that folder, so each is a real landing page.
"""
from common import (
    CATEGORIES, OTHER_LABEL, H1B_ICON, ROOT,
    load_listings, load_feed_config, categorize, age_str,
    filter_for_profile, output_dir,
)


def build_table(jobs):
    lines = [
        "| Company | Role | Location | 🛂 H1B | Source | Posted |",
        "|---|---|---|---|---|---|",
    ]
    for j in jobs:
        company = (j.get("company") or "—").replace("|", "-")
        title = (j.get("title") or "—").replace("|", "-")
        location = (j.get("location") or "—").replace("|", "-")
        url = j.get("url")
        source = j.get("source", "—")
        posted = age_str(j.get("posted"))
        h1b = H1B_ICON.get(j.get("h1b_sponsor", "unknown"), "❔")
        role_cell = f"[{title}]({url})" if url else title
        lines.append(f"| {company} | {role_cell} | {location} | {h1b} | {source} | {posted} |")
    return "\n".join(lines)


def render_readme(jobs, profile, all_profiles, generated_at):
    grouped = {}
    for j in jobs:
        grouped.setdefault(categorize(j.get("title", "")), []).append(j)

    is_root = not profile["slug"]
    heading = "Experienced Engineer Job Feed" if is_root else f"Experienced Engineer Job Feed — {profile['label']}"
    ts_display = generated_at or "never — run the workflow"

    lines = []
    lines.append(f"# {heading}")
    lines.append("")
    lines.append(
        "Auto-updated list of open roles for experienced engineers, pulled "
        "directly from public Greenhouse, Lever, and Ashby job-board APIs, "
        "plus RemoteOK and the Hacker News \"Who is Hiring?\" thread. No "
        "email, LinkedIn, or Microsoft account access is used anywhere in "
        "this pipeline."
    )
    lines.append("")
    if not is_root:
        lines.append(
            f"Filtered to roles whose stated experience range overlaps "
            f"**{profile['label']}**"
            + (" (postings with no stated number are included)." if profile["keep_unknown"]
               else " (postings with no stated number are excluded from this feed).")
        )
        lines.append("")
        lines.append("[⬅️ Back to all experience levels](../../README.md)")
        lines.append("")
    lines.append(f"**Last updated:** {ts_display} · **Open roles in this feed:** {len(jobs)}")
    lines.append("")
    lines.append(
        "🛂 H1B column: 🟢 posting explicitly mentions sponsorship, or the "
        "company is on your known-sponsors list · 🔴 posting explicitly says "
        "no sponsorship · ❔ not stated — this is a best-effort heuristic on "
        "text that companies often don't specify, not a guarantee. See "
        "`companies.yaml` to tune it."
    )
    lines.append("")
    feed_json_path = "./feed.json" if is_root else "./feed.json"
    feed_xml_path = "./feed.xml" if is_root else "./feed.xml"
    lines.append(
        f"🤖 **Agents/scripts:** don't scrape this README — read "
        f"[`feed.json`]({feed_json_path}) or [`feed.xml`]({feed_xml_path}) "
        f"instead. See [Consuming this feed](#consuming-this-feed) below."
    )
    lines.append("")

    if is_root:
        lines.append("## Feeds by years of experience")
        lines.append("")
        lines.append(
            "Each link below is a separate, independently-updated feed "
            "filtered to that experience band — useful if you want to share "
            "just one with someone, or point an agent at a specific level."
        )
        lines.append("")
        for p in all_profiles:
            if not p["slug"]:
                continue
            lines.append(f"- **{p['label']}** — [README](./feeds/{p['slug']}/README.md) · "
                          f"[feed.json](./feeds/{p['slug']}/feed.json) · "
                          f"[feed.xml](./feeds/{p['slug']}/feed.xml)")
        lines.append("")
        lines.append("---")
        lines.append("")

    # Table of contents
    lines.append("## Browse roles by category")
    lines.append("")
    for label, _ in CATEGORIES:
        count = len(grouped.get(label, []))
        lines.append(f"- {label} ({count})")
    other_count = len(grouped.get(OTHER_LABEL, []))
    lines.append(f"- {OTHER_LABEL} ({other_count})")
    lines.append("")
    lines.append("---")
    lines.append("")

    anchor_base = heading.lower().replace(" ", "-").replace("—", "").replace("--", "-")
    for label, _ in CATEGORIES + [(OTHER_LABEL, [])]:
        cat_jobs = grouped.get(label, [])
        lines.append(f"## {label}")
        lines.append("")
        if cat_jobs:
            lines.append(build_table(cat_jobs))
        else:
            lines.append("*No open roles matched this category right now.*")
        lines.append("")
        lines.append(f"[⬆️ Back to top](#{anchor_base})")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## Consuming this feed")
    lines.append("")
    lines.append("This directory publishes two machine-readable files, regenerated on every run:")
    lines.append("")
    lines.append("- `feed.json` — flat JSON array, easiest for a script or agent to parse")
    lines.append("- `feed.xml` — standard RSS 2.0, works with any feed reader")
    lines.append("")
    rel = "" if is_root else f"feeds/{profile['slug']}/"
    lines.append(
        "If this repo is public, reachable without auth at:\n"
        "```\n"
        f"https://raw.githubusercontent.com/<your-username>/<your-repo>/main/{rel}feed.json\n"
        f"https://raw.githubusercontent.com/<your-username>/<your-repo>/main/{rel}feed.xml\n"
        "```"
    )
    lines.append("")
    lines.append("## Configuring what gets tracked")
    lines.append("")
    lines.append(
        "Edit [`companies.yaml`](" + ("./companies.yaml" if is_root else "../../companies.yaml") + ") — "
        "add Greenhouse/Lever/Ashby company slugs, tweak seniority/exclude "
        "keywords, adjust the H1B settings, or add/edit years-of-experience "
        "feeds under `feeds:`. The next scheduled run picks up your changes "
        "automatically."
    )
    lines.append("")

    return "\n".join(lines)


def main():
    data = load_listings()
    all_jobs = data.get("jobs", [])
    generated_at = data.get("generated_at")
    profiles = load_feed_config()

    for profile in profiles:
        jobs = filter_for_profile(all_jobs, profile)
        content = render_readme(jobs, profile, profiles, generated_at)
        out_dir = output_dir(profile)
        readme_path = out_dir / "README.md"
        readme_path.write_text(content)
        print(f"Wrote {readme_path} ({len(jobs)} roles)")


if __name__ == "__main__":
    main()
