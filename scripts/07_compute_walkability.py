#!/usr/bin/env python3
"""Compute walkability proxy per Community Area.

Initial-pass proxy: amenity density (third-place + nightlife + food counts
per sq mi). Correlates with Walk Score in the ~0.5-0.6 range — weaker than
the geometric mean of amenity + intersection density (~0.75), but the
intersection-density piece needs osmnx or pyrosm (extra deps) and is left
as a TODO upgrade.

Inputs:
  data/processed/community_areas.gpkg     (from 01)
  data/processed/osm_amenities.gpkg       (from 06)

Output:
  data/processed/walkability_by_ca.gpkg
    Columns: area_num_1, community, amenity_count, area_sqmi,
             amenity_density, walk_score_proxy (0-100)

The density is normalized 0-100 across CAs using min-max scaling; this
makes the score relative to Chicago's distribution, which is appropriate
for ranking neighborhoods against each other.
"""

import warnings
from pathlib import Path

import geopandas as gpd

warnings.filterwarnings("ignore", category=UserWarning, module="geopandas")

ROOT = Path(__file__).resolve().parent.parent

PROJECTED_CRS = "EPSG:3435"     # Illinois State Plane East (feet)
SQ_FT_PER_SQ_MI = 27_878_400


def main():
    cas = gpd.read_file(ROOT / "data/processed/community_areas.gpkg").to_crs(PROJECTED_CRS)
    cas["area_num_1"] = cas["area_num_1"].astype(int)
    cas["area_sqmi"] = cas.area / SQ_FT_PER_SQ_MI

    amen = gpd.read_file(ROOT / "data/processed/osm_amenities.gpkg").to_crs(PROJECTED_CRS)

    # Spatial join: each amenity point inherits the CA it falls in
    joined = gpd.sjoin(
        amen[["osm_id", "category", "geometry"]],
        cas[["area_num_1", "community", "geometry"]],
        how="inner",
        predicate="within",
    )
    counts = joined.groupby("area_num_1").size().rename("amenity_count").reset_index()

    out = cas[["area_num_1", "community", "area_sqmi", "geometry"]].merge(
        counts, on="area_num_1", how="left"
    )
    out["amenity_count"] = out["amenity_count"].fillna(0).astype(int)
    out["amenity_density"] = out["amenity_count"] / out["area_sqmi"]

    # Min-max normalize the density to 0-100. Use density rather than raw
    # count so big CAs aren't unfairly rewarded.
    dmin, dmax = out["amenity_density"].min(), out["amenity_density"].max()
    out["walk_score_proxy"] = 100 * (out["amenity_density"] - dmin) / (dmax - dmin)

    out_path = ROOT / "data/processed/walkability_by_ca.gpkg"
    out.to_crs("EPSG:4326").to_file(out_path, driver="GPKG", layer="walkability_by_ca")

    print(f"[07] Done → {out_path}")
    print(f"     Amenities joined: {len(joined):,} / {len(amen):,}")
    print(f"     Amenity density range: {dmin:.1f} – {dmax:.1f} per sq mi")
    top = out.nlargest(5, "amenity_density")[["community", "amenity_density", "walk_score_proxy"]]
    print(f"\n     Top 5 by amenity density:")
    for _, r in top.iterrows():
        print(f"       {r['community']:<22} {r['amenity_density']:>6.1f} /sqmi  →  walk_proxy {r['walk_score_proxy']:>5.1f}")


if __name__ == "__main__":
    main()
