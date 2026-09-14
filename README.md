# Stolen Bike Finder

Pulls new bike listings from Craigslist, eBay, and (optionally, manually) Facebook
Marketplace within a radius of your location, scores each one against your bike's
description, and shows the results in a local web dashboard so you can review them.

This tool does not know for certain whether any listing is your bike. It surfaces
candidates for you to look at. **If you find a real match, don't contact or meet the
seller yourself** — take a screenshot of the listing and report it to your local police
department (most have a form for stolen property leads, and many require the listing as
evidence to act). Several cities also have bike-specific registries (e.g. Bike Index) —
worth checking if you registered your bike there or reporting the theft so others get
alerted if it shows up.

## What it actually does, source by source

- **Craigslist** — queries the plain search page for your local subdomain (no API key
  needed). Craigslist discontinued RSS feeds for search results, so this parses the
  no-JS "static" result list instead, which is still server-rendered HTML and doesn't
  need a browser. That result list doesn't include images, exact coordinates, or posted
  dates, so for any *new* listing that already scored some textual match, one extra
  request fetches its photo and posted date from the listing's own page (bounded to 6
  concurrent requests, and skipped entirely for zero-score or already-seen listings, to
  keep total request volume reasonable).

  Craigslist has no single nationwide search -- the US is split into ~700 separate
  regional sites (`minneapolis`, `chicago`, `madison`, ...), each with its own listings,
  and a site's own radius param only filters *within that one site*, never into a
  neighboring one. So the radius slider works by fetching Craigslist's published list of
  all US sites and their coordinates, picking every site within the chosen radius, and
  querying all of them concurrently -- at 30mi that's usually just your own site; at
  500mi it can be 80+ sites, which takes longer (tens of seconds) but actually returns
  results from that far out, unlike a plain distance param would. The posted date is what
  powers the "ignore anything posted before the bike was stolen"
  filter below. This is the most reliable source here.
- **eBay** — uses the official Browse API (needs a free developer app). eBay is a
  national marketplace, not local classifieds, so "radius" doesn't really apply — most
  results ship. Included because resold stolen parts/bikes do show up there, but treat
  location as informational, not a hard filter.
- **Facebook Marketplace** — has no public API. The included scraper drives a real
  browser (Playwright) against your own logged-in session. It's off by default, disabled
  in requirements by default, and meant to be run manually, not on a schedule — automating
  your own account this way is against Facebook's Terms of Service, and the scraper will
  break whenever Facebook changes their page layout. Read `backend/app/scrapers/facebook.py`
  before turning it on; that's your decision to make about your own account.

## Setup

1. **Python 3.11+**, then:
   ```
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r backend/requirements.txt
   ```

2. **Describe your bike(s).**
   ```
   cp config.example.yaml config.yaml
   ```
   Edit `config.yaml`: `bikes` is a list, one entry per stolen bike — make, model, color,
   frame size, serial number, distinguishing features, the date it was stolen, and roughly
   what it's worth. Every listing gets scored against every bike in the list and tagged
   with whichever one it matched best (shown as a badge on its card, filterable in the
   dashboard). Also set your search location (`lat`/`lon`) and radius under `search:`.
   `config.yaml` is gitignored so none of this gets committed.

   Don't have lat/lon handy? Search "[your city] latitude longitude" or right-click your
   location on Google Maps and copy the coordinates.

   The serial number field isn't used for search matching — sellers essentially never
   type a serial number into a listing — but it's worth filling in anyway for your police
   report and for registries like Bike Index.

3. **Craigslist**: set `sources.craigslist.site` to your local subdomain, e.g. `sfbay`,
   `seattle`, `losangeles`, `minneapolis` — check `https://geo.craigslist.org/iso/us` for
   the exact subdomain if unsure.

4. **eBay (optional)**: create a developer app at https://developer.ebay.com/my/keys,
   then:
   ```
   cp .env.example .env
   ```
   and fill in `EBAY_APP_ID` / `EBAY_CERT_ID`. Set `sources.ebay.enabled: true` in
   `config.yaml`.

5. **Facebook (optional, read the warning above first)**:
   ```
   pip install -r backend/requirements-facebook.txt
   playwright install chromium
   python -m backend.app.scrapers.facebook
   ```
   A browser window opens — log into Facebook manually, then close the window. Your
   session is saved to `.fb_browser_profile/` (gitignored) and reused after that. Then
   set `sources.facebook.enabled: true` in `config.yaml`. It'll be included in the
   dashboard's refresh, but expect it to be the flakiest of the three.

## Running

```
uvicorn backend.app.main:app --reload
```

Open http://localhost:8000. Click **Refresh listings** to pull new results (it also
auto-refreshes on the interval set by `refresh_interval_minutes` in `config.yaml`, default
30 minutes). Each listing shows a score and the specific reasons it matched (make, model,
color, features, suspiciously-low price), plus a link to the original listing. Mark
listings as Reviewed / Not it / Reported to keep track as you go through them.

The **search radius slider** at the top (with 50/250/500 mi quick-select buttons) overrides
`config.yaml`'s radius for the next refresh, and stays in effect for auto-refreshes after
that until you change it again or restart the server -- edit `config.yaml` if you want a
different permanent default.

The **min price slider** (100/250/500/750/$1000+ presets) filters out anything priced below
that threshold -- purely client-side, so it applies instantly without a refresh, and
listings with no price at all are hidden once it's above $0 (since there's nothing to
compare). **Sort by** controls ordering: Score (ties broken by cheapest first, the
default), Date posted, Cost, or Distance.

Distance shown per listing is an approximation for Craigslist results: since the search
results list doesn't expose a listing's exact coordinates (see below), each one is tagged
with the distance from you to the *Craigslist site* it came from (e.g. everything from the
"minneapolis" site shows the same distance) rather than its literal address -- close enough
to gauge how far away something is, not precise to the block.

## How matching works

Simple, transparent keyword scoring (`backend/app/matching.py`) — no ML, nothing hidden:
make, model, color, frame size, and each distinguishing feature you listed all add
points if they appear in a listing's title/description (with basic fuzzy matching for
typos). Listings priced well below that bike's stated value get a small bonus, since
stolen goods are often dumped cheap. Every listing is scored against every bike in
`config.yaml`, and it's tagged with whichever one scored highest. Listings posted before
that bike's `stolen_date`, or outside your radius (for sources that expose location),
are filtered out entirely. Tune the weights at the top of `matching.py` if it's over- or
under-matching for you.

The search query sent to each source is deliberately just the bike's make (falling back
to model) — most search boxes, Craigslist included, AND-match every word in a query, so
a query built from make + model + extra keywords together almost never matches anything
real. Casting a wide net on make and doing the actual narrowing with the scoring above
works far better in practice.

## Limitations, honestly

- This only sees what's publicly listed. A lot of stolen bikes are sold in person, via
  private groups, or stripped for parts and never show up on these sites at all.
- Facebook Marketplace and OfferUp (not included) are where a lot of local bike resale
  actually happens, and both are hard to reach reliably without violating their terms —
  this covers Facebook in a limited, manual way and skips OfferUp entirely.
- Scoring is keyword matching against text, not image recognition. A generic "black road
  bike, size M" listing with no other detail will score low even if it's genuinely yours,
  unless the seller happened to mention a feature you also listed. It also means an
  unrelated item that happens to share a make/color/price range (a pair of cycling shoes,
  say) can outscore a real bike listing — that's expected, not a bug; review before acting.
- Craigslist thumbnails and post dates only get fetched for listings that scored above 0
  (see the Craigslist section above) — a zero-score listing shows no image, and if you
  drop min score down to "Any" you'll see cards with neither, that's expected.
- Changing the radius slider only affects Craigslist's own search results (which is where
  radius filtering actually happens server-side); eBay's results aren't filtered by
  distance regardless of the slider, per the eBay note above.
