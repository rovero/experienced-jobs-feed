"""Writes feed.json (flat, agent-friendly) and feed.xml (RSS 2.0) for every
feed profile: the unfiltered "all levels" feed at the repo root, plus one
per configured years-of-experience band under feeds/<slug>/. These are the
files an agent or feed reader should consume — not the READMEs, which are
for humans."""
import json
from datetime import datetime, timezone
from xml.sax.saxutils import escape

from common import load_listings, load_feed_config, filter_for_profile, output_dir


def write_feed_json(jobs, generated_at, out_dir):
    path = out_dir / "feed.json"
    path.write_text(json.dumps({
        "version": "1.0",
        "generated_at": generated_at,
        "count": len(jobs),
        "jobs": [
            {
                "company": j.get("company"),
                "title": j.get("title"),
                "location": j.get("location"),
                "url": j.get("url"),
                "source": j.get("source"),
                "posted": j.get("posted"),
                "h1b_sponsor": j.get("h1b_sponsor", "unknown"),
                "years_min": j.get("years_min"),
                "years_max": j.get("years_max"),
            }
            for j in jobs
        ],
    }, indent=2))
    return path


def write_feed_xml(jobs, profile, out_dir):
    now_rfc822 = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S %z")
    items = []
    for j in jobs:
        title = escape(f"{j.get('company', 'Unknown')} — {j.get('title', 'Untitled role')}")
        link = escape(j.get("url") or "")
        location = escape(j.get("location") or "")
        source = escape(j.get("source") or "")
        h1b = escape(j.get("h1b_sponsor", "unknown"))
        ymin, ymax = j.get("years_min"), j.get("years_max")
        if ymin is None and ymax is None:
            yoe = "unspecified"
        elif ymax is None:
            yoe = f"{ymin}+ years"
        else:
            yoe = f"{ymin}-{ymax} years"
        pub_date = now_rfc822
        if j.get("posted"):
            try:
                dt = datetime.fromisoformat(j["posted"].replace("Z", "+00:00"))
                pub_date = dt.strftime("%a, %d %b %Y %H:%M:%S %z")
            except ValueError:
                pass
        guid = escape(j.get("url") or f"{j.get('company')}-{j.get('title')}")
        items.append(f"""    <item>
      <title>{title}</title>
      <link>{link}</link>
      <guid isPermaLink="false">{guid}</guid>
      <description>{location} · via {source} · h1b_sponsor: {h1b} · experience: {yoe}</description>
      <pubDate>{pub_date}</pubDate>
    </item>""")

    channel_title = "Experienced Engineer Job Feed"
    if profile["slug"]:
        channel_title += f" — {profile['label']}"

    rss = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>{escape(channel_title)}</title>
    <link>https://github.com/</link>
    <description>Open roles for experienced engineers, pulled from public job-board APIs.</description>
    <lastBuildDate>{now_rfc822}</lastBuildDate>
{chr(10).join(items)}
  </channel>
</rss>
"""
    path = out_dir / "feed.xml"
    path.write_text(rss)
    return path


def main():
    data = load_listings()
    all_jobs = data.get("jobs", [])
    generated_at = data.get("generated_at")
    profiles = load_feed_config()

    for profile in profiles:
        jobs = filter_for_profile(all_jobs, profile)
        out_dir = output_dir(profile)
        json_path = write_feed_json(jobs, generated_at, out_dir)
        xml_path = write_feed_xml(jobs, profile, out_dir)
        print(f"Wrote {json_path} and {xml_path} ({len(jobs)} roles)")


if __name__ == "__main__":
    main()
