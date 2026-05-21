#!/usr/bin/env python3
"""Fetch Chicago amenities from OpenStreetMap via the Overpass API.

What we pull:
  - amenity=bar|pub|restaurant|cafe|nightclub|fast_food
  - leisure=fitness_centre

These are the third-place + nightlife + food categories that feed the
walkability density proxy in 07 and the social-scene typology in 09.

We deliberately do NOT pull the highway network for intersection-density
walkability. That requires either osmnx or pyrosm (additional deps) and
multi-MB downloads. Script 07 uses amenity density alone as the
walkability proxy for the initial pass; intersection density is a
TODO upgrade.

Bounding box for Chicago: -87.94 < lon < -87.52, 41.64 < lat < 42.03

Outputs:
  data/raw/osm_amenities.json    — raw Overpass response (cached)
  data/processed/osm_amenities.gpkg
"""

import json
import urllib.parse
import urllib.request
import warnings
from pathlib import Path

import geopandas as gpd
from shapely.geometry import Point

warnings.filterwarnings("ignore", category=UserWarning, module="geopandas")

ROOT = Path(__file__).resolve().parent.parent

OVERPASS = "https://overpass-api.de/api/interpreter"
BBOX = (41.64, -87.94, 42.03, -87.52)   # (south, west, north, east)

QUERY = f"""
[out:json][timeout:120];
(
  node["amenity"~"^(bar|pub|restaurant|cafe|nightclub|fast_food)$"]
       ({BBOX[0]},{BBOX[1]},{BBOX[2]},{BBOX[3]});
  node["leisure"="fitness_centre"]
       ({BBOX[0]},{BBOX[1]},{BBOX[2]},{BBOX[3]});
);
out body;
""".strip()


def fetch_overpass() -> Path:
    out = ROOT / "data/raw/osm_amenities.json"
    if out.exists():
        print(f"[06] Overpass cache hit → {out}")
        return out
    print(f"[06] Querying Overpass API (Chicago bbox, ~30-60s)...")
    data = urllib.parse.urlencode({"data": QUERY}).encode()
    req = urllib.request.Request(
        OVERPASS,
        data=data,
        method="POST",
        headers={"User-Agent": "chicago_neighborhoods/0.1 (personal mapping project)"},
    )
    resp = urllib.request.urlopen(req, timeout=180).read()
    out.write_bytes(resp)
    return out


def main():
    raw_path = fetch_overpass()
    raw = json.loads(raw_path.read_text())
    elements = raw.get("elements", [])
    print(f"[06] Parsed {len(elements):,} OSM elements")

    records = []
    for el in elements:
        if el.get("type") != "node":
            continue
        tags = el.get("tags") or {}
        category = tags.get("amenity") or tags.get("leisure") or "unknown"
        records.append({
            "osm_id": el["id"],
            "category": category,
            "name": tags.get("name"),
            "geometry": Point(el["lon"], el["lat"]),
        })

    gdf = gpd.GeoDataFrame(records, crs="EPSG:4326")
    out_path = ROOT / "data/processed/osm_amenities.gpkg"
    gdf.to_file(out_path, driver="GPKG", layer="osm_amenities")

    print(f"[06] Done → {out_path}")
    print(f"     Total amenities: {len(gdf):,}")
    cat_counts = gdf["category"].value_counts()
    for cat, n in cat_counts.items():
        print(f"       {cat:<18} {n:>5,}")


if __name__ == "__main__":
    main()
