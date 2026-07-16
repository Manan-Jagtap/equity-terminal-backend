# Data sources — owner setup (free official APIs)

The engine already runs on the RBI DBIE seed + the live NSE / OECD / PIB feeds.
This doc is the one-time registration to make **MoSPI and OGD** (GDP, CPI, WPI,
IIP, and any other government dataset) refresh automatically instead of via the
monthly DBIE re-upload. All free. Set the env vars on the **Railway backend and
scheduler** services (never in the repo, never in chat).

## 1. MoSPI API — GDP, CPI, IIP, labour

1. Go to **https://api.mospi.gov.in/** → register for an API key (the portal
   has a Swagger UI that documents each endpoint and its exact query string).
2. For each series you want, copy the complete GET URL from the Swagger page
   (it will include the query params for base year / periodicity). Put the API
   key wherever the endpoint expects it, or leave the literal `{key}` in the URL
   and the fetcher will substitute `MOSPI_KEY`.
3. Set on Railway:
   - `MOSPI_KEY` = your key
   - `MOSPI_CPI_URL` = the CPI (2024=100, combined) endpoint URL
   - `MOSPI_IIP_URL` = the IIP endpoint URL
   - `MOSPI_GDP_URL` = the GDP (current prices) endpoint URL

Each URL you set turns that series live; unset ones are simply skipped. The
weekly macro job and the deploy-boot both call it. Response shapes handled:
`{data:[{Year,Month,Index}]}`, `{records:[…]}`, and flat `[{date,value}]`.

## 2. OGD / API Setu — WPI, agriculture, energy, transport, etc.

1. Go to **https://data.gov.in/** (or **https://apisetu.gov.in/**) → sign up →
   generate an **OGD API key**.
2. Find the resource you want (e.g. WPI monthly), copy its resource API URL —
   the pattern is `https://api.data.gov.in/resource/<id>?api-key={key}&format=json&limit=1000`.
3. Set on Railway:
   - `OGD_KEY` = your key (also used as the MoSPI fallback)
   - `OGD_WPI_URL` = the WPI resource URL

**Important, learned the hard way:** many data.gov.in resources are *stale
snapshots* (some frozen years ago). Before wiring one, open it and check the
latest date. Only point a URL at a resource that is actually kept current.
A stale feed is worse than the honest "awaiting" state.

## 3. High-frequency activity indicators (GST, PMI, power, e-way, auto, UPI)

These have no single clean API. Two paths, both already built:

- **Live URL** (if you find one): set `ACTIVITY_<X>_URL` (+ optional
  `ACTIVITY_<X>_KEY`) — `X` ∈ GST, EWAY, PMI_MFG, PMI_SVC, POWER, AUTO, UPI.
  The fetcher expects JSON `[{date, value}]`.
- **Manual monthly** (most reliable): `POST /api/admin/macro/activity-point`
  `{slug, date, value}` — fills the card with one verified number a month.
  GST already auto-fills when a PIB "GST collection" release is in the feed.

## What's already live (no setup needed)

RBI DBIE (rates, money, FX, banking, BoP — the seed) · live 10Y G-sec discount
rate · NSE FII/DII, pledge, insider, bulk/block deals · concall transcripts ·
RBI + SEBI + PIB regulatory radar · OECD India Composite Leading Indicator.

## Priority order (by evidence value)

1. `MOSPI_CPI_URL` + `MOSPI_IIP_URL` — the two most-watched activity reads.
2. `MOSPI_GDP_URL` — quarterly growth.
3. `OGD_WPI_URL` — producer inflation.
4. Activity `ACTIVITY_*_URL` where a live source exists; else the monthly POST.

## 2b. MoSPI eSankhyiki (CPI / IIP / WPI) — the REST API behind the portal

The eSankhyiki portal (esankhyiki.mospi.gov.in → macroindicators → "API" tab) is a
JS app; its data comes from a REST backend. Reverse-engineered from the app bundle
(16 Jul 2026):

- **Data host / base:** `https://datainnovation.mospi.gov.in/api/`
  (also referenced: `https://api.mospi.gov.in/api/esankhyiki/`). Swagger UI:
  `https://esankhyiki.mospi.gov.in/EC/swagger-ui/index.html` (auth-gated).
- **CPI endpoints:** `/api/cpi/getCpiFilterByLevelAndBaseYear` (INDEX levels — use
  this for MOSPI_CPI_URL), `/api/cpi/getInflation` (the RATE ~5% — the parser's
  index band (80,400) will REJECT this; don't point CPI at it).
- IIP / WPI: the API tab exposes equivalent endpoints per product.

**To turn it on (owner, ~2 min — the fetcher + parser already exist):**
1. Open the CPI **API tab** in your browser; copy the exact GET URL that returns
   the all-India CPI **index** series (with its query params, and `{key}` if a key
   is shown — eSankhyiki appears keyless).
2. Railway (backend + scheduler): `MOSPI_CPI_URL` = that URL. Same for
   `MOSPI_IIP_URL` (IIP index) and optionally repoint WPI.
3. Trigger `POST /api/admin/macro/refresh` (or wait for the Monday job).
4. If a card stays blank, the response shape is new — paste me a sample and I'll
   add it to `_parse_gov_rows` (it already handles {data:[{Year,Month,Index}]},
   {records:[…]}, and flat [{date,value}]).

NB: the datainnovation host was unreachable from the build sandbox, so the exact
request params must be copied from the API tab (as with the OGD/WPI flow).
