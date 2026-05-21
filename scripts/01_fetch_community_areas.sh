#!/usr/bin/env bash
# Fetch Chicago's 77 official Community Areas (boundary polygons).
#
# Source: Chicago Data Portal, "Boundaries - Community Areas (current)"
#         https://data.cityofchicago.org/Facilities-Geographic-Boundaries/Boundaries-Community-Areas-current-/cauq-8yn6
# Slug:   igwz-8jzy (geospatial export endpoint)
#
# Output:
#   data/raw/community_areas.geojson   — immutable download
#   data/processed/community_areas.gpkg — same data in GeoPackage form,
#                                          ready to join other layers against
#
# The GeoPackage is the canonical spatial key for the project: every
# other dataset (crime, transit, rents, ACS) gets joined to CA by
# `community` (name) or `area_num_1` (number 1-77).

set -euo pipefail

# Resolve project root relative to this script so it works from anywhere
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"

RAW="$ROOT/data/raw/community_areas.geojson"
OUT="$ROOT/data/processed/community_areas.gpkg"

URL="https://data.cityofchicago.org/api/geospatial/igwz-8jzy?method=export&format=GeoJSON"

echo "[01] Fetching Community Areas from CDP..."
curl --fail --silent --show-error --location \
     --output "$RAW" \
     "$URL"

echo "[01] Converting GeoJSON → GeoPackage with ogr2ogr..."
# -overwrite: clobber any prior gpkg at this path
# -nln: name the layer inside the gpkg explicitly (default would be filename)
# -t_srs EPSG:4326: ensure WGS84 lat/lon for downstream joins
ogr2ogr -overwrite \
        -f GPKG \
        -nln community_areas \
        -t_srs EPSG:4326 \
        "$OUT" "$RAW"

echo "[01] Done."
echo "       raw:       $RAW"
echo "       processed: $OUT"
ogrinfo -so "$OUT" community_areas | grep -E "Feature Count|Extent|Geometry"
