# Chicago neighborhoods — relocation scouting map

Multi-criteria neighborhood analysis for a relocation search to Chicago.
Hard filters on the dealbreakers (rent ceiling, safety floor, transit
access, housing-quality proxy); soft scoring on the gradient criteria
(walkability, transit headroom, safety headroom, cost headroom); a
**qualitative typology** instead of a numeric score for the squishy
criterion ("social scene / neighborhood vibe").

## Why this is shaped differently than `alaska_population`

| `alaska_population` | `chicago_neighborhoods` |
|---|---|
| One data source (Kontur) → one choropleth | ~8 sources merged on a common spatial key |
| Spatial unit: H3 hexagons (equal-area) | Spatial unit: 77 Community Areas (unequal — density matters) |
| Output: a beautiful map | Output: a defensible ranked shortlist + a map |
| Choropleth from raw counts is honest (cells are equal size) | Counts must be normalized to rates/densities (areas vary 10×) |
| No filtering, no scoring | Hard filters + soft scoring + categorical typology |

Mental model is the same — **GDAL is the bedrock, PyQGIS sits on top
for composition.** But the analytical layer (filters + scoring +
typology) sits between data acquisition and rendering, and it's the
part that actually drives the decision.

## The criteria and how each is operationalized

| Criterion | Filter? | Score? | Data source |
|---|---|---|---|
| Cost of living | Hard: median 1BR rent ≤ $3,200 | Soft: headroom below cap | Zillow ZORI (zip-level), areal-weighted to CAs |
| Safety | Hard: violent-crime rate ≤ 1.5× citywide median | Soft: headroom below cap | CDP crime dataset (24-month rolling) |
| Transit | Hard: ≥1 CTA rail stop OR ≥10 bus stops/sq mi | Soft: weighted stop density | CTA GTFS feed |
| Walkability | — | Soft: intersection + amenity density | OSM (free, transparent) |
| Housing quality | Hard: drop top decile of open code violations per 1k units | — | CDP building violations |
| Social scene / vibe | — | **Qualitative typology** (categorical, not scored) | See `docs/typology_sources.md` |

Hard filters are pre-conditions, not preferences — they cut the space
from ~77 CAs to whatever survives. Soft scores rank survivors on a
0–100 composite. The typology characterizes survivors qualitatively
and drives the final selection alongside the numeric ranking.

## Why the social-scene criterion has no number

Forcing a 0–100 score on "vibe" produces false precision: the proxies
(bar density, age cohort, % single) correlate with the thing but
aren't the thing. A `young-prof-nightlife` archetype + key descriptive
phrases is more honest and more useful than a misleading
`social_score = 73`. The typology is built from journalism and
considered commentary — see `docs/typology_sources.md` for the source
hierarchy.

## Pipeline

```
    raw data                         analytical layer            rendering
    ────────                         ────────────────            ─────────

    01_fetch_community_areas.sh ─┐
    02_fetch_chicago_data.sh ────┤
    03_fetch_transit.sh ─────────┤
    04_fetch_acs.py ─────────────┼──► 08_apply_filters.py ──┐
    05_fetch_rents.py ───────────┤    (hard + soft scoring) │
    06_fetch_osm.py ─────────────┤                          ├──► 10_build_project.py
                                 │                          │    (PyQGIS .qgz)
    07_compute_walkability.py ───┤                          │           │
                                 │                          │           ▼
                                 └──► 09_build_typology.py ─┘    11_export_print_map.py
                                      (qualitative records)      (PDF + PNG @ 300 DPI)
```

Numbered scripts in `scripts/` correspond to the pipeline above. Each
is independently re-runnable. `data/raw/` holds immutable downloads
(re-fetching produces the same data modulo upstream updates);
`data/processed/` holds derived GeoPackages and joined tables.

## Hard filters (defaults — adjust in `08_apply_filters.py`)

| Filter | Threshold | Rationale |
|---|---|---|
| Rent ceiling | $3,200/mo median 1BR | ~36% of take-home on $135k salary in IL (no city income tax). Generous — filter does light work, ranking does the rest. |
| Safety floor | violent crime ≤ 1.5× citywide median | Conservative; can soften to 2× if too many CAs cut |
| Transit minimum | ≥1 CTA rail stop OR ≥10 bus stops/sq mi | Filters true transit deserts; doesn't penalize bus-only neighborhoods |
| Housing quality | drop top decile of open building code violations / 1k units | Cat-friendliness can't be filtered by data — that's a unit-level question at visit time |

## One-shot run (once all scripts are implemented)

```bash
cd ~/2_projects/mapping/chicago_neighborhoods

./scripts/01_fetch_community_areas.sh
./scripts/02_fetch_chicago_data.sh
./scripts/03_fetch_transit.sh
python3 ./scripts/04_fetch_acs.py
python3 ./scripts/05_fetch_rents.py
python3 ./scripts/06_fetch_osm.py
python3 ./scripts/07_compute_walkability.py
python3 ./scripts/08_apply_filters.py
python3 ./scripts/09_build_typology.py
python3 ./scripts/10_build_project.py
python3 ./scripts/11_export_print_map.py

qgis output/chicago_neighborhoods.qgz
xdg-open output/chicago_neighborhoods_shortlist.pdf
```

## Key concepts cheatsheet

**Community Areas vs. neighborhoods.** Chicago has 77 official
Community Areas (CAs) — administrative units defined in the 1920s
that haven't changed. "Neighborhoods" (Wicker Park, Boystown,
Andersonville) are informal subdivisions of CAs. CAs are the only
unit with consistent boundaries across CDP, ACS, and Zillow data;
they're our spatial key.

**CKAN API pattern.** Chicago Data Portal uses CKAN, same backend as
HDX (and most public data portals). The endpoint
`/api/3/action/package_show?id=<slug>` returns JSON describing the
dataset including direct resource URLs — way more reliable than
scraping the HTML page. Same pattern works on data.gov, data.gov.uk,
ckan.publishing.service.gov.uk, etc.

**Areal-weighted interpolation.** Zillow's ZORI rent index is
published at zip-code level but we need it at Community Area level.
Zips and CAs don't nest. The standard fix is to intersect the two
polygon sets, compute each piece's area, and weight the source
attribute (rent) by overlap area when aggregating to the target unit
(CA). Assumes rent is uniform within a zip — imperfect but better
than picking a "primary" zip per CA.

**Why CAs unequal sizes change the math.** In Alaska we colored
hexagons by raw counts because hexagons are equal-area. CAs range
from ~1 sq mi (Loop) to ~13 sq mi (West Town and others). Coloring
by raw crime count would make big CAs look unsafe and tiny CAs look
safe. Always normalize to a rate (per capita, per area, per
household).

**EPSG codes for Chicago work.**
- `EPSG:4326` — WGS84 lat/lon, what all source data ships in
- `EPSG:3857` — Web Mercator, for basemap rendering
- `EPSG:3435` — Illinois State Plane East (NAD83, feet) — the right
  projection for accurate area/distance in Chicago

## Files

```
chicago_neighborhoods/
├── README.md
├── docs/
│   └── typology_sources.md       # source-priority policy for vibe characterization
├── scripts/
│   ├── 01_fetch_community_areas.sh
│   ├── 02_fetch_chicago_data.sh  # crime, business licenses, violations (CDP)
│   ├── 03_fetch_transit.sh       # CTA GTFS (L stops + bus stops)
│   ├── 04_fetch_acs.py           # Census ACS 5-year demographics
│   ├── 05_fetch_rents.sh         # Zillow ZORI, areal-weighted to CAs
│   ├── 06_fetch_osm.sh           # OSM city extract
│   ├── 07_compute_walkability.py # intersection + amenity density from OSM
│   ├── 08_apply_filters.py       # hard filters → survivors; soft scores
│   ├── 09_build_typology.py      # qualitative archetype records
│   ├── 10_build_project.py       # assemble .qgz
│   └── 11_export_print_map.py    # PDF + PNG shortlist map
├── styles/                       # .qml symbology (typology categorical, score graduated)
├── data/
│   ├── raw/                      # immutable downloads
│   └── processed/                # derived, regenerable
└── output/
    ├── chicago_neighborhoods.qgz
    ├── chicago_neighborhoods_shortlist.pdf
    └── chicago_neighborhoods_shortlist.png
```
