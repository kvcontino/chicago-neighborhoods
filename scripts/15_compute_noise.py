#!/usr/bin/env python3
"""Compute a noise-exposure proxy per Community Area.

For each CA: what fraction of its land area falls within 200m of either
an Interstate highway or an L track? Higher = louder ambient environment.
Inverted as quiet_score (100 = no noise exposure, 0 = entire CA buffered).

Noise sources from OSM (via Overpass):
  - highway=motorway / motorway_link (Kennedy, Eisenhower, Dan Ryan,
    Edens, Stevenson, Skyway, and their ramps)
  - railway=subway (the Chicago L, regardless of elevated/underground;
    elevated stretches dominate the audible footprint)

200m buffer is a defensible default — at that distance freeway noise
drops to roughly background-suburban levels and elevated-rail rumble
becomes intermittent rather than constant.

Inputs:
  data/processed/community_areas.gpkg     (from 01)

Outputs:
  data/raw/osm_noise_sources.json         (cached Overpass response)
  data/processed/noise_by_ca.gpkg
    Columns: area_num_1, community, area_sqmi,
             noise_exposure_pct (0-100, fraction of CA within 200m buffer),
             quiet_score (100 - noise_exposure_pct, min-max-normalized 0-100)

Sidecar metric — joined into survivors.gpkg in script 08 but NOT folded
into the composite (per user's preference for visibility-without-weighting).
"""

import json
import urllib.parse
import urllib.request
import warnings
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString
from shapely.ops import unary_union

warnings.filterwarnings("ignore", category=UserWarning, module="geopandas")

ROOT = Path(__file__).resolve().parent.parent
PROJECTED_CRS = "EPSG:3435"        # Illinois State Plane East (feet)
BUFFER_FT = 200 * 3.28084          # 200m in feet ≈ 656 ft
SQ_FT_PER_SQ_MI = 27_878_400

BBOX = (41.64, -87.94, 42.03, -87.52)

OVERPASS = "https://overpass-api.de/api/interpreter"
QUERY = f"""
[out:json][timeout:180];
(
  way["highway"~"^(motorway|motorway_link)$"]
       ({BBOX[0]},{BBOX[1]},{BBOX[2]},{BBOX[3]});
  way["railway"="subway"]
       ({BBOX[0]},{BBOX[1]},{BBOX[2]},{BBOX[3]});
);
(._;>;);
out body;
""".strip()


def fetch_noise_sources() -> Path:
    out = ROOT / "data/raw/osm_noise_sources.json"
    if out.exists():
        print(f"[15] Cache hit → {out}")
        return out
    print(f"[15] Querying Overpass for highways + L tracks...")
    data = urllib.parse.urlencode({"data": QUERY}).encode()
    req = urllib.request.Request(
        OVERPASS, data=data, method="POST",
        headers={"User-Agent": "chicago_neighborhoods/0.1"},
    )
    resp = urllib.request.urlopen(req, timeout=240).read()
    out.write_bytes(resp)
    return out


def parse_lines(json_path: Path):
    """Reconstruct LineStrings from Overpass nodes + ways."""
    raw = json.loads(json_path.read_text())
    elements = raw.get("elements", [])
    nodes = {el["id"]: (el["lon"], el["lat"]) for el in elements if el.get("type") == "node"}

    highway_lines, rail_lines = [], []
    for el in elements:
        if el.get("type") != "way":
            continue
        tags = el.get("tags") or {}
        coords = [nodes[n] for n in el.get("nodes", []) if n in nodes]
        if len(coords) < 2:
            continue
        line = LineString(coords)
        if tags.get("highway") in ("motorway", "motorway_link"):
            highway_lines.append(line)
        elif tags.get("railway") == "subway":
            rail_lines.append(line)
    return highway_lines, rail_lines


def main():
    src = fetch_noise_sources()
    highway_lines, rail_lines = parse_lines(src)
    print(f"[15] Highway segments: {len(highway_lines):,}, L segments: {len(rail_lines):,}")

    # Build GeoDataFrame in WGS84, project, buffer
    all_lines = gpd.GeoDataFrame(
        {"kind": ["highway"] * len(highway_lines) + ["rail"] * len(rail_lines)},
        geometry=highway_lines + rail_lines, crs="EPSG:4326",
    ).to_crs(PROJECTED_CRS)

    print(f"[15] Buffering noise sources by 200m...")
    noise_polygon = unary_union(all_lines.geometry.buffer(BUFFER_FT))
    noise_gdf = gpd.GeoDataFrame(geometry=[noise_polygon], crs=PROJECTED_CRS)

    cas = gpd.read_file(ROOT / "data/processed/community_areas.gpkg").to_crs(PROJECTED_CRS)
    cas["area_num_1"] = cas["area_num_1"].astype(int)
    cas["area_sqft"] = cas.area
    cas["area_sqmi"] = cas["area_sqft"] / SQ_FT_PER_SQ_MI

    print(f"[15] Computing exposure per CA...")
    cas["noise_intersection"] = cas.geometry.intersection(noise_polygon)
    cas["noise_area_sqft"] = cas["noise_intersection"].area
    cas["noise_exposure_pct"] = 100 * cas["noise_area_sqft"] / cas["area_sqft"]

    # Inverted + normalized
    def _mm(s):
        mn, mx = s.min(), s.max()
        return 100 * (s - mn) / (mx - mn) if mx > mn else 50.0
    cas["quiet_score"] = _mm(-cas["noise_exposure_pct"])

    out_gdf = cas[["area_num_1", "community", "area_sqmi",
                   "noise_exposure_pct", "quiet_score", "geometry"]]
    out_path = ROOT / "data/processed/noise_by_ca.gpkg"
    out_gdf.to_crs("EPSG:4326").to_file(out_path, driver="GPKG", layer="noise_by_ca")

    print(f"[15] Done → {out_path}")
    print(f"     Noise exposure range: {cas['noise_exposure_pct'].min():.1f}% - "
          f"{cas['noise_exposure_pct'].max():.1f}%")

    top_quiet = cas.nlargest(5, "quiet_score")[
        ["community", "noise_exposure_pct", "quiet_score"]
    ]
    print(f"\n     Top 5 quietest CAs:")
    for _, r in top_quiet.iterrows():
        print(f"       {r['community']:<22} {r['noise_exposure_pct']:>5.1f}% exposure  "
              f"quiet={r['quiet_score']:.1f}")
    top_loud = cas.nsmallest(5, "quiet_score")[
        ["community", "noise_exposure_pct", "quiet_score"]
    ]
    print(f"\n     Top 5 loudest CAs:")
    for _, r in top_loud.iterrows():
        print(f"       {r['community']:<22} {r['noise_exposure_pct']:>5.1f}% exposure  "
              f"quiet={r['quiet_score']:.1f}")


if __name__ == "__main__":
    main()
