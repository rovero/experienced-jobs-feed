"""Writes feed.json (flat, agent-friendly) and feed.xml (RSS 2.0) from
listings.json. These are the files an agent or feed reader should consume —
not the README, which is for humans."""
import json
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
LISTINGS_PATH = ROOT / "listings.json"
FEED_JSON_PATH = ROOT / "feed.json"
FEED_XML_PATH = ROOT / "feed.xml"


def main():
    data = json.loads(LISTINGS_PATH.read_text()) if LISTINGS_PATH.exists() else {
        "generated_at": None, "count": 0, "jobs": []
    }
    jobs = data.get("jobs", [])

    # --- feed.json: simple, stable shape for scripts/agents ---
    FEED_JSON_PATH.write_text(json.dumps({
        "version": "1.0",
        "generated_at": data.get("generated_at"),
        "count": len(jobs),
        "jobs": [
            {
                "company": j.get("company"),
                "title": j.get("title"),
                "location": j.get("location"),
                "url": j.get("url"),
                "source": j.get("source"),
                "posted": j.get("posted"),
            }
            for j in jobs
        ],
    }, indent=2))

    # --- feed.xml: RSS 2.0 ---
    now_rfc822 = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S %z")
    items = []
    for j in jobs:
        title = escape(f"{j.get('company', 'Unknown')} — {j.get('title', 'Untitled role')}")
        link = escape(j.get("url") or "")
        location = escape(j.get("location") or "")
        source = escape(j.get("source") or "")
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
      <description>{location} · via {source}</description>
      <pubDate>{pub_date}</pubDate>
    </item>""")

    rss = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Experienced Engineer Job Feed</title>
    <link>https://github.com/</link>
    <description>Open roles for experienced engineers, pulled from public job-board APIs.</description>
    <lastBuildDate>{now_rfc822}</lastBuildDate>
{chr(10).join(items)}
  </channel>
</rss>
"""
    FEED_XML_PATH.write_text(rss)
    print(f"Wrote {FEED_JSON_PATH} and {FEED_XML_PATH}")


if __name__ == "__main__":
    main()
