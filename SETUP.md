# Setup

## 1. Create the repo
Push this folder to a new GitHub repo (public, so `feed.json`/`feed.xml` are
readable via a plain `raw.githubusercontent.com` URL with no auth):

```bash
cd experienced-jobs-feed
git init
git add .
git commit -m "Initial commit"
gh repo create your-username/experienced-jobs-feed --public --source=. --push
# (no `gh`? create the repo on github.com, then:
#  git remote add origin https://github.com/your-username/experienced-jobs-feed.git
#  git branch -M main && git push -u origin main)
```

## 2. Enable Actions
Actions run automatically once pushed — nothing else to configure. The
workflow uses the repo's built-in `GITHUB_TOKEN`, so no secrets are needed.

Go to the **Actions** tab and click "Run workflow" once to populate
`listings.json` / `README.md` / `feed.json` / `feed.xml` immediately, instead
of waiting for the first scheduled run.

## 3. Point your agent at the feed
Once it's run once, these URLs are live and public:

```
https://raw.githubusercontent.com/your-username/experienced-jobs-feed/main/feed.json
https://raw.githubusercontent.com/your-username/experienced-jobs-feed/main/feed.xml
```

Give your agent read access to that URL (a plain HTTP GET — no credentials,
no OAuth, no LinkedIn/Microsoft account in the loop at all). It's just a
static JSON/XML file that happens to refresh daily.

## 4. Tune what gets tracked
Edit `companies.yaml`:
- Add Greenhouse/Lever/Ashby slugs for companies you care about (instructions
  for finding a slug are in the file's comments).
- Adjust `seniority_keywords` / `exclude_keywords` to match the levels you
  want (defaults target senior/staff/principal/lead, and exclude
  intern/new-grad/junior roles).

Commit the change — the next scheduled run (or a manual "Run workflow") picks
it up.

## Notes / limitations
- RemoteOK and HN "Who is Hiring" need no configuration — they're scanned
  every run regardless of `companies.yaml`.
- Company slugs drift over time (companies migrate ATS providers). If a
  company stops showing results, re-check its careers page URL.
- This intentionally avoids scraping LinkedIn/Indeed/Microsoft-account-gated
  sources — those either require auth or forbid scraping in their ToS. The
  sources here are public, documented, no-auth JSON APIs.
