#!/usr/bin/env python3
"""Compute walkability proxy per Community Area.

Two components, each min-max normalized 0-100 across CAs:
  - amenity_density (weighted by bucket, see below)
  - intersection_density: street-network nodes with degree ≥ 3 per sq mi
                          (from OSM walkable street network via osmnx)

walk_score_proxy = geometric mean of the two normalized scores. Geometric
mean penalizes imbalance: a CA needs BOTH walkable street density AND
nearby destinations to score high. (Arithmetic mean would let one
compensate for the other, which understates real walkability friction.)

Amenity bucket weighting (user-profile specific):
  - social bucket (restaurant, cafe, fitness_centre) × 1.0
  - drinker bucket (bar, pub, nightclub, fast_food) × DRINKER_WEIGHT (default 0.3)
  - essential bucket (grocery, pharmacy, healthcare, vet) — broken out as
    its OWN density score (essential_services_density), so the user has a
    "can I live here without owning a car" axis distinct from walkability

Set DRINKER_WEIGHT = 1.0 for a drinker profile (or just delete the weighting).

Inputs:
  data/processed/community_areas.gpkg     (from 01)
  data/processed/osm_amenities.gpkg       (from 06, with bucket column)

Output:
  data/processed/walkability_by_ca.gpkg
    Columns: area_num_1, community, area_sqmi,
             social_count, drinker_count, essential_count,
             amenity_weighted, amenity_density,
             intersection_count, intersection_density,
             amenity_norm, intersection_norm, walk_score_proxy (0-100),
             essential_services_density, essential_services_norm

Cache:
  data/raw/osm_walkable_network.graphml   — the osmnx-pulled street network
  (~tens of MB; one-time download ~30-60s via Overpass)
"""

import warnings
from pathlib import Path

import geopandas as gpd
import numpy as np
import osmnx as ox

warnings.filterwarnings("ignore", category=UserWarning, module="geopandas")
warnings.filterwarnings("ignore", category=FutureWarning)

ROOT = Path(__file__).resolve().parent.parent
PROJECTED_CRS = "EPSG:3435"
SQ_FT_PER_SQ_MI = 27_878_400

# Profile-specific amenity weighting. User doesn't drink → bars/pubs/nightclubs
# count for less in the walkability calculation. Set to 1.0 to disable.
DRINKER_WEIGHT = 0.3


def fetch_street_network(boundary_gdf: gpd.GeoDataFrame):
    """Pull the walkable street network for Chicago via Overpass (cached)."""
    cache = ROOT / "data/raw/osm_walkable_network.graphml"
    if cache.exists():
        print(f"[07] Street network cache hit → {cache}")
        return ox.io.load_graphml(cache)

    # osmnx wants the polygon in WGS84 (lat/lon)
    boundary_wgs84 = boundary_gdf.to_crs("EPSG:4326")
    chicago_poly = boundary_wgs84.geometry.union_all()

    print(f"[07] Fetching Chicago walkable street network via Overpass (~1 min)...")
    G = ox.graph.graph_from_polygon(
        chicago_poly,
        network_type="walk",
        simplify=True,
        retain_all=False,
    )
    cache.parent.mkdir(parents=True, exist_ok=True)
    ox.io.save_graphml(G, cache)
    print(f"[07] Cached {cache} ({len(G.nodes):,} nodes, {len(G.edges):,} edges)")
    return G


def main():
    cas = gpd.read_file(ROOT / "data/processed/community_areas.gpkg").to_crs(PROJECTED_CRS)
    cas["area_num_1"] = cas["area_num_1"].astype(int)
    cas["area_sqmi"] = cas.area / SQ_FT_PER_SQ_MI

    # --- 1. Amenity density (by bucket, weighted) ---
    amen = gpd.read_file(ROOT / "data/processed/osm_amenities.gpkg").to_crs(PROJECTED_CRS)
    joined_amen = gpd.sjoin(
        amen[["osm_id", "category", "bucket", "geometry"]],
        cas[["area_num_1", "geometry"]],
        how="inner", predicate="within",
    )
    # Counts per CA per bucket
    bucket_counts = (
        joined_amen.groupby(["area_num_1", "bucket"]).size().unstack(fill_value=0).reset_index()
    )
    # Ensure all bucket columns exist (a CA could have 0 of one bucket)
    for b in ("social", "drinker", "essential", "other"):
        if b not in bucket_counts.columns:
            bucket_counts[b] = 0
    bucket_counts = bucket_counts.rename(columns={
        "social": "social_count", "drinker": "drinker_count",
        "essential": "essential_count", "other": "other_count",
    })
    # Weighted amenity = social × 1.0 + drinker × DRINKER_WEIGHT
    # (essential is handled as its own axis below; "other" excluded)
    bucket_counts["amenity_weighted"] = (
        bucket_counts["social_count"] * 1.0
        + bucket_counts["drinker_count"] * DRINKER_WEIGHT
    )

    # --- 2. Intersection density (from osmnx street network) ---
    G = fetch_street_network(cas)
    # Build a GeoDataFrame of intersection nodes (degree ≥ 3 in the simplified graph).
    # graph_to_gdfs returns a single GDF (not a tuple) when only one of
    # nodes/edges is requested.
    nodes = ox.convert.graph_to_gdfs(G, nodes=True, edges=False)
    nodes["degree"] = [G.degree(n) for n in nodes.index]
    intersections = nodes[nodes["degree"] >= 3].copy()
    intersections = intersections.to_crs(PROJECTED_CRS)
    intersections = intersections.reset_index().rename(columns={"osmid": "osm_id"})

    joined_int = gpd.sjoin(
        intersections[["osm_id", "geometry"]],
        cas[["area_num_1", "geometry"]],
        how="inner", predicate="within",
    )
    int_counts = joined_int.groupby("area_num_1").size().rename("intersection_count").reset_index()

    print(f"[07] Intersection nodes citywide: {len(intersections):,}")
    print(f"[07] Joined to CAs:              {len(joined_int):,}")

    # --- 3. Merge + compute densities + normalize + combine ---
    out = (
        cas[["area_num_1", "community", "area_sqmi", "geometry"]]
        .merge(bucket_counts, on="area_num_1", how="left")
        .merge(int_counts, on="area_num_1", how="left")
    )
    # Fill missing counts with 0
    for col in ("social_count", "drinker_count", "essential_count",
                "other_count", "intersection_count"):
        if col in out.columns:
            out[col] = out[col].fillna(0).astype(int)
    out["amenity_weighted"] = out["amenity_weighted"].fillna(0)

    out["amenity_density"]    = out["amenity_weighted"] / out["area_sqmi"]
    out["essential_services_density"] = out["essential_count"] / out["area_sqmi"]
    out["intersection_density"] = out["intersection_count"] / out["area_sqmi"]

    def _minmax(s):
        mn, mx = s.min(), s.max()
        return 100 * (s - mn) / (mx - mn) if mx > mn else 50.0

    out["amenity_norm"]            = _minmax(out["amenity_density"])
    out["intersection_norm"]       = _minmax(out["intersection_density"])
    out["essential_services_norm"] = _minmax(out["essential_services_density"])

    # Geometric mean — penalizes imbalance. Clamp at 0 to avoid sqrt of
    # tiny negatives from float rounding.
    out["walk_score_proxy"] = np.sqrt(
        np.clip(out["amenity_norm"] * out["intersection_norm"], 0, None)
    )

    out_path = ROOT / "data/processed/walkability_by_ca.gpkg"
    out.to_crs("EPSG:4326").to_file(out_path, driver="GPKG", layer="walkability_by_ca")

    print(f"[07] Done → {out_path}")
    print(f"     Amenity density (weighted, drinker×{DRINKER_WEIGHT}):  "
          f"{out['amenity_density'].min():>6.1f} - {out['amenity_density'].max():>6.1f} per sq mi")
    print(f"     Essential services density: "
          f"{out['essential_services_density'].min():>6.1f} - {out['essential_services_density'].max():>6.1f} per sq mi")
    print(f"     Intersection density: {out['intersection_density'].min():>6.0f} - {out['intersection_density'].max():>6.0f} per sq mi")
    print(f"     walk_score_proxy:     {out['walk_score_proxy'].min():>6.1f} - {out['walk_score_proxy'].max():>6.1f}")
    top_walk = out.nlargest(5, "walk_score_proxy")[
        ["community", "amenity_density", "intersection_density", "walk_score_proxy"]
    ]
    print(f"\n     Top 5 by walk_score_proxy:")
    for _, r in top_walk.iterrows():
        print(f"       {r['community']:<22} "
              f"amen {r['amenity_density']:>5.1f}/sqmi · "
              f"int {r['intersection_density']:>4.0f}/sqmi → "
              f"walk {r['walk_score_proxy']:>5.1f}")
    top_ess = out.nlargest(5, "essential_services_density")[
        ["community", "essential_count", "essential_services_density"]
    ]
    print(f"\n     Top 5 by essential services density:")
    for _, r in top_ess.iterrows():
        print(f"       {r['community']:<22} "
              f"{r['essential_count']:>3} essentials → "
              f"{r['essential_services_density']:>5.1f}/sqmi")


if __name__ == "__main__":
    main()
