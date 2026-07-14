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
