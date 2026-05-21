#!/usr/bin/env bash
# Fetch CTA GTFS feed → derive two point GeoPackages (L rail stops + bus stops).
#
# GTFS is the open standard for transit data: a zip of CSVs, same shape
# across every transit agency. Useful files for our purpose:
#   stops.txt       — stop_id, stop_name, stop_lat, stop_lon, location_type
#   routes.txt      — route_id, route_type (1=subway/L, 3=bus per GTFS spec)
#   trips.txt       — route_id ↔ trip_id mapping
#   stop_times.txt  — trip_id ↔ stop_id mapping (huge: ~1M+ rows)
#
# To label each stop with its mode, we join stops → stop_times → trips →
# routes and take the route_type that serves it. Multi-mode stops (a few
# downtown points serving both L and bus) are tagged as rail since that's
# what the transit hard filter cares about.
#
# Using sqlite3 (Python stdlib, no deps) as an in-memory analytics engine
# rather than pandas — same join logic, no dependency, and SQL is the
# right tool for "join four tables and group" anyway.
#
# Output:
#   data/raw/cta_gtfs.zip
#   data/raw/cta_gtfs/                 — extracted CSVs (kept for inspection)
#   data/processed/cta_rail_stops.gpkg — L station/platform points
#   data/processed/cta_bus_stops.gpkg  — bus stop points

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"

GTFS_URL="https://www.transitchicago.com/downloads/sch_data/google_transit.zip"
ZIP_OUT="$ROOT/data/raw/cta_gtfs.zip"
EXTRACT_DIR="$ROOT/data/raw/cta_gtfs"

echo "[03] Downloading CTA GTFS feed..."
curl --fail --silent --show-error --location \
     --output "$ZIP_OUT" \
     "$GTFS_URL"

echo "[03] Extracting..."
rm -rf "$EXTRACT_DIR"
mkdir -p "$EXTRACT_DIR"
unzip -q -o "$ZIP_OUT" -d "$EXTRACT_DIR"
ls "$EXTRACT_DIR" | sed 's/^/       /'

RAIL_CSV="$ROOT/data/processed/cta_rail_stops.csv"
BUS_CSV="$ROOT/data/processed/cta_bus_stops.csv"
RAIL_GPKG="$ROOT/data/processed/cta_rail_stops.gpkg"
BUS_GPKG="$ROOT/data/processed/cta_bus_stops.gpkg"

echo "[03] Joining stops × stop_times × trips × routes via sqlite3..."
python3 - "$EXTRACT_DIR" "$RAIL_CSV" "$BUS_CSV" <<'PYEOF'
import csv, sqlite3, sys, os

extract, rail_out, bus_out = sys.argv[1], sys.argv[2], sys.argv[3]

conn = sqlite3.connect(":memory:")
conn.row_factory = sqlite3.Row
cur = conn.cursor()

def load(name):
    # Build the table schema from the CSV header — every column TEXT.
    # SQLite is dynamically typed; we cast where needed in queries.
    path = os.path.join(extract, f"{name}.txt")
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        cols = [c.strip() for c in reader.fieldnames]
        col_defs = ", ".join(f"{c} TEXT" for c in cols)
        cur.execute(f"CREATE TABLE {name} ({col_defs})")
        placeholders = ",".join("?" for _ in cols)
        col_list = ",".join(cols)
        rows = (tuple(r[c] for c in cols) for r in reader)
        cur.executemany(f"INSERT INTO {name} ({col_list}) VALUES ({placeholders})", rows)

for tbl in ("stops", "routes", "trips", "stop_times"):
    load(tbl)

cur.execute("CREATE INDEX idx_st_stop ON stop_times(stop_id)")
cur.execute("CREATE INDEX idx_st_trip ON stop_times(trip_id)")
cur.execute("CREATE INDEX idx_t_trip  ON trips(trip_id)")
cur.execute("CREATE INDEX idx_t_route ON trips(route_id)")

# For each stop, get the distinct route_types serving it. Rail (1) wins over
# bus (3) if both, because the transit hard filter rewards rail access more.
cur.execute("""
    WITH stop_modes AS (
        SELECT DISTINCT s.stop_id, s.stop_name, s.stop_lat, s.stop_lon,
               CAST(r.route_type AS INTEGER) AS route_type
        FROM stops s
        JOIN stop_times st ON st.stop_id = s.stop_id
        JOIN trips t       ON t.trip_id  = st.trip_id
        JOIN routes r      ON r.route_id = t.route_id
        WHERE s.stop_lat != '' AND s.stop_lon != ''
    )
    SELECT stop_id, stop_name, stop_lat, stop_lon,
           MIN(route_type) AS primary_mode    -- MIN(1,3) = 1 = rail wins
    FROM stop_modes
    GROUP BY stop_id, stop_name, stop_lat, stop_lon
""")

rail, bus = [], []
for row in cur.fetchall():
    rec = {"stop_id": row["stop_id"], "stop_name": row["stop_name"],
           "lat": row["stop_lat"], "lon": row["stop_lon"]}
    if row["primary_mode"] == 1:
        rail.append(rec)
    elif row["primary_mode"] == 3:
        bus.append(rec)

for path, rows in [(rail_out, rail), (bus_out, bus)]:
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["stop_id", "stop_name", "lat", "lon"])
        w.writeheader()
        w.writerows(rows)

print(f"     rail: {len(rail):>5} stops → {rail_out}")
print(f"     bus:  {len(bus):>5} stops → {bus_out}")
PYEOF

# CSV → GeoPackage point layers (ogr2ogr reads lat/lon as columns via -oo)
echo "[03] Building GeoPackages from CSVs..."
ogr2ogr -overwrite -f GPKG -nln cta_rail_stops \
        -oo X_POSSIBLE_NAMES=lon -oo Y_POSSIBLE_NAMES=lat -oo AUTODETECT_TYPE=YES \
        -a_srs EPSG:4326 \
        "$RAIL_GPKG" "$RAIL_CSV"

ogr2ogr -overwrite -f GPKG -nln cta_bus_stops \
        -oo X_POSSIBLE_NAMES=lon -oo Y_POSSIBLE_NAMES=lat -oo AUTODETECT_TYPE=YES \
        -a_srs EPSG:4326 \
        "$BUS_GPKG" "$BUS_CSV"

echo "[03] Done."
ogrinfo -so "$RAIL_GPKG" cta_rail_stops | grep -E "Feature Count|Geometry"
ogrinfo -so "$BUS_GPKG"  cta_bus_stops  | grep -E "Feature Count|Geometry"
