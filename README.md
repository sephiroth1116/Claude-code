# E-Bike Finder

Pulls new e-bike listings from Craigslist, eBay, Upway, and The Pro's Closet (Craigslist
within a radius of your location; the rest ship nationwide), scores each one against a
brand wishlist (with a Class 3 / 28mph confirmation as a scoring bonus and a visible
badge, not a hard requirement -- see why below), and shows the results in a local web
dashboard so you can review them.

This started as a tool to find a specific stolen bike; it's since been repurposed into a
general e-bike shopping-wishlist tracker. The underlying mechanism — multi-site Craigslist
search, scoring, the dashboard — is unchanged, only what counts as a "match" is now a
brand list instead of one bike's exact description.

## What it actually does, source by source

- **Craigslist** — queries the plain search page for your local subdomain (no API key
  needed). Craigslist discontinued RSS feeds for search results, so this parses the
  no-JS "static" result list instead, which is still server-rendered HTML and doesn't
  need a browser. That result list only gives a title, no description, image, or posted
  date — so for any *new* listing that already scored some match on title alone, one
  extra request fetches its own page for the full description, photo, and posted date
  (bounded to 6 concurrent requests, skipped entirely for zero-score or already-seen
  listings). The listing is then re-scored against that fuller text, since that's usually
  where a seller actually writes down a spec like "Class 3" or exact mileage, not the title.

  Craigslist has no single nationwide search -- the US is split into ~700 separate
  regional sites (`minneapolis`, `chicago`, `madison`, ...), each with its own listings,
  and a site's own radius param only filters *within that one site*, never into a
  neighboring one. So the radius slider works by fetching Craigslist's published list of
  all US sites and their coordinates, picking every site within the chosen radius, and
  querying all of them concurrently -- at 30mi that's usually just your own site; at
  500mi it can be 80+ sites, which takes longer (tens of seconds) but actually returns
  results from that far out, unlike a plain distance param would. This is the most
  reliable source here.
- **eBay** — uses the official Browse API (needs a free developer app). eBay is a
  national marketplace, not local classifieds, so "radius" doesn't really apply — most
  results ship. When eBay's API reports real shipping options for a listing, that's used
  for the Ships/Local pickup badge directly; Craigslist listings only get a best-effort
  text guess (see Limitations).
- **Facebook Marketplace** — has no public API. The included scraper drives a real
  browser (Playwright) against your own logged-in session. It's off by default, disabled
  in requirements by default, and meant to be run manually, not on a schedule — automating
  your own account this way is against Facebook's Terms of Service, and the scraper will
  break whenever Facebook changes their page layout. Read `backend/app/scrapers/facebook.py`
  before turning it on; that's your decision to make about your own account.
- **Upway** — a used/refurbished e-bike marketplace built on Shopify Hydrogen (a
  "headless" storefront). Its search results are fetched client-side from Shopify's
  public Storefront GraphQL API using a token that's embedded in every page's own source
  for exactly that purpose; `backend/app/scrapers/upway.py` calls that same API directly
  over plain HTTPS instead of running a browser. No login, no scraping, no automation.
  This is a better source than it might sound: Upway tags each listing with its own
  `maxSpeed:20mph` / `maxSpeed:28mph` value and an exact `mileage` metafield, so Class 3
  confirmation and miles ridden come from Upway's own structured data here, not a text
  guess (see "How matching works"). Upway ships nationwide, so delivery is always on and
  distance doesn't apply.

  (An earlier attempt used Shopify's newer UCP/MCP "agentic commerce" protocol instead —
  authentication worked, but its `tools/call` endpoint returned `"Tool not found"` for
  every request tried, on both Upway and The Pro's Closet, which looked like a bug on
  Shopify's side rather than something fixable here. That approach was abandoned in favor
  of the Storefront API above, which is simpler and just works.)
- **The Pro's Closet** — a used-bike marketplace on a standard (non-headless) Shopify
  theme, so it has no Storefront API token to call directly. Instead,
  `backend/app/scrapers/pros_closet.py` uses Shopify's public predictive-search endpoint
  (`/search/suggest.json`) — the same AJAX call the site's own search box makes — which
  takes a real text query and returns matching products directly. The one real limitation:
  Shopify caps that endpoint at 10 results per query regardless of what's requested, so
  this source only ever shows the 10 most relevant hits per wishlist brand. The Pro's
  Closet doesn't expose an exact-mileage or max-speed field the way Upway does, so those
  fall back to the same text-guessing used for Craigslist.

## Setup

1. **Python 3.11+**, then:
   ```
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r backend/requirements.txt
   ```

2. **Describe your wishlist.**
   ```
   cp config.example.yaml config.yaml
   ```
   Edit `config.yaml`'s `wishlist:` section:
   - `brands` — a list, OR-matched (any one qualifies), e.g.
     `["Specialized", "Riese & Muller", "Gazelle"]`
   - `require_class_3` — if `true`, you care about Class 3 / 28mph: listings that
     explicitly state it in their text get a scoring bonus and a "Class 3 confirmed"
     badge; everything else is tagged "Class 3 not stated" rather than hidden. This is
     deliberately a soft signal, not a hard filter, and never inferred from brand or
     model name (the same model, e.g. Specialized's Vado line, often ships switchable
     between speed classes) -- live testing found real sellers essentially never write
     legal speed classification into casual listings, so requiring it outright returned
     close to zero results. See Limitations for the details.
   - `max_price_usd` — optional hard ceiling; anything priced above it is dropped.
   - `keywords` — optional extra positive signals, e.g. `["ebike", "mid-drive"]`.

   Also set your search location (`lat`/`lon`) and radius under `search:`. `config.yaml`
   is gitignored so none of this gets committed.

   Don't have lat/lon handy? Search "[your city] latitude longitude" or right-click your
   location on Google Maps and copy the coordinates.

3. **Craigslist**: set `sources.craigslist.site` to your local subdomain, e.g. `sfbay`,
   `seattle`, `losangeles`, `minneapolis` — check `https://geo.craigslist.org/iso/us` for
   the exact subdomain if unsure.

4. **eBay (optional)**: create a developer app at https://developer.ebay.com/my/keys,
   then:
   ```
   cp .env.example .env
   ```
   and fill in `EBAY_APP_ID` / `EBAY_CERT_ID`. Set `sources.ebay.enabled: true` in
   `config.yaml`. eBay also requires every registered app to either handle "Marketplace
   Account Deletion" notifications or explicitly mark itself exempt (Alerts & Notifications
   tab in the developer portal) — this app doesn't store any eBay user's personal data, so
   the honest answer there is "I do not persist eBay data".

5. **Facebook (optional, read the warning above first)**:
   ```
   pip install -r backend/requirements-facebook.txt
   playwright install chromium
   python -m backend.app.scrapers.facebook
   ```
   A browser window opens — log into Facebook manually, then close the window. Your
   session is saved to `.fb_browser_profile/` (gitignored) and reused after that. Then
   set `sources.facebook.enabled: true` in `config.yaml`.

6. **Upway and The Pro's Closet**: no setup needed, no API key to request — both are
   public endpoints each site's own search already uses. Just set
   `sources.upway.enabled: true` and/or `sources.pros_closet.enabled: true` in
   `config.yaml`.

## Running

```
uvicorn backend.app.main:app
```

(Skip `--reload` -- its file-watcher subprocess has been unreliable in some environments;
plain `uvicorn` is what's actually been tested.)

Open http://localhost:8000. Click **Refresh listings** to pull new results (it also
auto-refreshes on the interval set by `refresh_interval_minutes` in `config.yaml`, default
30 minutes). Each listing shows a score and the specific reasons it matched (brand,
keywords, Class 3 confirmation, suspiciously-low price), plus a link to the original
listing. Mark listings as Interested / Not it / Purchased to keep track as you go.

The **search radius slider** at the top (with 50/250/500 mi quick-select buttons) overrides
`config.yaml`'s radius for the next refresh, and stays in effect for auto-refreshes after
that until you change it again or restart the server -- edit `config.yaml` if you want a
different permanent default.

The **min price buttons** (100/250/500/750/$1000+, click again to clear) filter out
anything priced below that threshold -- purely client-side, applies instantly. **Sort by**
controls ordering: Score (ties broken by cheapest first, the default), Date posted, Cost,
or Distance.

Each card also shows **delivery** (Ships / Local pickup only, when known) and **mileage**
(when a seller states it), and the **Brand** filter narrows to one wishlist brand at a time.

Distance shown per listing is an approximation for Craigslist results: since the search
results list doesn't expose a listing's exact coordinates, each one is tagged with the
distance from you to the *Craigslist site* it came from (e.g. everything from the
"minneapolis" site shows the same distance) rather than its literal address -- close enough
to gauge how far away something is, not precise to the block.

## How matching works

Simple, transparent keyword scoring (`backend/app/matching.py`) — no ML, nothing hidden:
a listing scores against every wishlist brand and keeps whichever one matched best.
Mentioning the brand is worth the most; extra `wishlist.keywords` add a smaller bonus;
an explicit Class 3 / 28mph confirmation adds another bonus and sets the
"Class 3 confirmed" badge (see `require_class_3` above for why this is a bonus, not a
filter). Listings outside your radius or over `max_price_usd` are filtered out entirely.

`search.exclude_keywords` is a hard filter too, checked before anything else: any listing
mentioning one of those terms is dropped outright regardless of score. Useful when a
wishlist brand also makes product lines that could never be a match -- e.g. wishlisting
"Specialized" will otherwise surface every Hardrock, Rockhopper, and Fatboy in the metro,
since those all mention the same brand.

The search query sent to each source is deliberately just the brand name — most search
boxes, Craigslist included, AND-match every word in a query, so combining brand + model +
keywords together almost never matches anything real. Casting a wide net on brand and
doing the actual narrowing with the scoring above works far better in practice.

## Limitations, honestly

- **Class 3 confirmation is a soft signal, not a filter, because most sources never
  state it as text.** This was verified live, not assumed: checking 15 real Craigslist
  listings' full descriptions (not just titles), including an actual Specialized electric
  bike, **zero** mentioned "Class 3" or "28mph" anywhere. An earlier version of this tool
  treated `require_class_3` as a hard filter and that returned zero results in practice —
  it's now a scoring bonus plus a "Class 3 confirmed" / "Class 3 not stated" badge instead,
  so you still see every brand match and can eyeball the listing yourself to judge the
  actual motor class. Upway is the one exception: its own `maxSpeed` product data is
  authoritative, so Upway listings get a real yes/no instead of a text guess. Inferring it
  from brand or model name was considered and rejected for every source — e.g.
  Specialized's Vado line ships switchable between speed classes, so guessing risks being
  outright wrong.
- This only sees what's publicly listed, across Craigslist, eBay, Upway, The Pro's
  Closet, and (manually, riskily) Facebook.
- The Pro's Closet's predictive-search endpoint is capped at 10 results per query by
  Shopify itself, not by this code -- if it carries more than 10 matching listings for a
  wishlist brand at once, only the top 10 show up here.
- Scoring is keyword matching against text, not image recognition. A generic listing
  with no detail will score low even if it's a real match, unless the seller happened to
  mention something the scorer looks for.
- Delivery/shipping status is real structured data from eBay's API, and always "ships"
  for Upway/The Pro's Closet (both are online marketplaces, not local pickup), but a
  best-effort text guess for Craigslist (looks for phrases like "can ship" / "local
  pickup only"); most Craigslist listings mention neither, in which case it's shown as
  unknown rather than guessed.
- Craigslist thumbnails, full descriptions, and post dates only get fetched for listings
  that scored above 0 on title alone — a zero-score listing shows none of those, and if
  you drop min score down to "Any" you'll see cards without them, that's expected.
- Changing the radius slider only affects Craigslist's own search results (which is where
  radius filtering actually happens server-side); eBay's results aren't filtered by
  distance regardless of the slider.
