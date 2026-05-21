#!/usr/bin/env bash
# Fetch three CDP datasets, aggregated to per-Community-Area counts.
#
# Datasets:
#   - Crimes 2001-present       (slug ijzp-q8t2) → violent crimes by CA, last 24mo
#   - Business Licenses         (slug r5kz-chrr) → active drink/food/venue licenses by CA + type
#   - Building Violations       (slug 22u3-xenr) → open violations by CA
#
# Why aggregate via SoQL $group instead of pulling raw records:
#   - Crime alone is ~200k records/year. 24mo = ~400k. We don't need them;
#     we need counts per CA (77 rows). Letting the server aggregate cuts
#     transfer from MBs to KBs and avoids pagination entirely.
#   - The aggregations are exactly the inputs script 08 needs for filtering
#     and scoring. No further reduction required.
#
# Outputs (CSVs, joined to community_areas.gpkg in step 08):
#   data/processed/crime_by_ca.csv
#       columns: community_area, violent_count
#   data/processed/business_licenses_by_ca.csv
#       columns: community_area, license_description, license_count
#   data/processed/violations_by_ca.csv
#       columns: community_area, open_violation_count
#
# Raw responses cached at data/raw/<dataset>.json — re-run is idempotent.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"

# 24-month window. SoQL date literals want ISO without timezone; use UTC.
SINCE="$(date -u -d '24 months ago' '+%Y-%m-%dT%H:%M:%S')"

echo "[02] Fetching CDP datasets aggregated to Community Area"
echo "     Window: since $SINCE (UTC)"
echo

# Generic helper: hit a CDP SoQL endpoint with $select/$where/$group,
# save raw JSON, convert to CSV. Uses curl --data-urlencode so the SoQL
# special chars ($, spaces, quotes) get encoded properly.
fetch_cdp_aggregate() {
    local label="$1"     # human label for log lines
    local slug="$2"      # CDP resource slug (4-char + 4-char)
    local select="$3"    # $select clause (without "$select=")
    local where="$4"     # $where clause
    local group="$5"     # $group clause
    local out_raw="$6"   # data/raw/...json
    local out_csv="$7"   # data/processed/...csv

    echo "[02] $label  ← $slug"

    curl --fail --silent --show-error --get \
         --data-urlencode "\$select=${select}" \
         --data-urlencode "\$where=${where}" \
         --data-urlencode "\$group=${group}" \
         --data-urlencode '$limit=50000' \
         --output "$out_raw" \
         "https://data.cityofchicago.org/resource/${slug}.json"

    # Embedded Python: pivot the JSON array of objects into a flat CSV.
    # Uses the first row's keys as headers, so it adapts to whatever
    # columns SoQL returned (count(*) aliases, etc.).
    python3 - "$out_raw" "$out_csv" <<'PYEOF'
import json, csv, sys
raw = json.load(open(sys.argv[1]))
if not raw:
    sys.exit(f"[02] EMPTY response — check slug, $where filter, or column names")
keys = list(raw[0].keys())
with open(sys.argv[2], "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=keys)
    w.writeheader()
    w.writerows(raw)
print(f"     {len(raw)} rows → {sys.argv[2]}")
PYEOF
    echo
}

# --- 1. Crime: violent crimes per CA, last 24 months ----------------------
# Violent crime classes per Chicago Police FBI Index Part 1:
#   HOMICIDE, CRIM SEXUAL ASSAULT, ROBBERY, AGGRAVATED BATTERY, ASSAULT
# (ASSAULT in this dataset is aggravated assault; simple assault is BATTERY.)
fetch_cdp_aggregate \
    "Crime (violent, 24mo)" \
    "ijzp-q8t2" \
    "community_area, count(*) AS violent_count" \
    "date > '${SINCE}' AND primary_type IN ('HOMICIDE','CRIM SEXUAL ASSAULT','ROBBERY','AGGRAVATED BATTERY','ASSAULT')" \
    "community_area" \
    "$ROOT/data/raw/crime_violent_24mo.json" \
    "$ROOT/data/processed/crime_by_ca.csv"

# --- 2. Business licenses: active food/drink/venue licenses, by type, per CA ---
# Group by both CA and license type so the typology step can use category-specific
# densities (just bars, just restaurants, etc.) rather than a single conflated total.
# license_status 'AAI' = Account Active Issued (CDP code for currently-issued).
fetch_cdp_aggregate \
    "Business licenses (active food/drink/venue)" \
    "r5kz-chrr" \
    "community_area, license_description, count(*) AS license_count" \
    "license_status='AAI' AND license_description IN ('Tavern','Consumption on Premises - Incidental Activity','Retail Food Establishment','Late Hour','Public Place of Amusement')" \
    "community_area, license_description" \
    "$ROOT/data/raw/business_licenses_active.json" \
    "$ROOT/data/processed/business_licenses_by_ca.csv"

# --- 3. Building violations: currently open, per CA -----------------------
# Special case: this dataset has no `community_area` column. CDP attached
# a spatial-join column `:@computed_region_vrxf_vc4k` whose values are the
# *row index* (_feature_id) within the CA layer at slug vrxf-vc4k — NOT
# the official CA number 1-77. We need two queries:
#   a) violations grouped by :@computed_region_vrxf_vc4k → counts per feature_id
#   b) vrxf-vc4k CA layer → mapping from _feature_id to area_num_1
# Then join in Python to produce the canonical (community_area, count) CSV.
echo "[02] Building violations (open)  ← 22u3-xenr (with vrxf-vc4k feature_id remap)"
VIOL_RAW="$ROOT/data/raw/building_violations_open.json"
CA_MAP_RAW="$ROOT/data/raw/ca_feature_id_map.json"
VIOL_OUT="$ROOT/data/processed/violations_by_ca.csv"

curl --fail --silent --show-error --get \
     --data-urlencode '$select=:@computed_region_vrxf_vc4k AS feature_id, count(*) AS open_violation_count' \
     --data-urlencode "\$where=violation_status='OPEN'" \
     --data-urlencode '$group=:@computed_region_vrxf_vc4k' \
     --data-urlencode '$limit=50000' \
     --output "$VIOL_RAW" \
     'https://data.cityofchicago.org/resource/22u3-xenr.json'

curl --fail --silent --show-error --get \
     --data-urlencode '$select=_feature_id, area_num_1, community' \
     --data-urlencode '$limit=200' \
     --output "$CA_MAP_RAW" \
     'https://data.cityofchicago.org/resource/vrxf-vc4k.json'

python3 - "$VIOL_RAW" "$CA_MAP_RAW" "$VIOL_OUT" <<'PYEOF'
import json, csv, sys
viol = json.load(open(sys.argv[1]))
ca_map_rows = json.load(open(sys.argv[2]))
# _feature_id is a string in the CA mapping; feature_id from violations is also string
fid_to_ca = {r["_feature_id"]: r["area_num_1"] for r in ca_map_rows}

# Aggregate: a couple feature_ids may be missing (records outside Chicago); skip them
rows = []
for v in viol:
    fid = v.get("feature_id")
    if fid is None or fid not in fid_to_ca:
        continue
    rows.append({"community_area": fid_to_ca[fid], "open_violation_count": v["open_violation_count"]})

with open(sys.argv[3], "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["community_area", "open_violation_count"])
    w.writeheader()
    w.writerows(rows)
print(f"     {len(rows)} rows → {sys.argv[3]}")
PYEOF
echo

echo "[02] Done."
echo "     Outputs ready for step 08 (apply_filters.py) to join against community_areas.gpkg."
