#!/usr/bin/env python3
"""Compute walkability proxy per Community Area.

Two components, each min-max normalized 0-100 across CAs:
  - amenity_density: bars/restaurants/cafes/gyms per sq mi  (from script 06)
  - intersection_density: street-network nodes with degree ≥ 3 per sq mi
                          (from OSM walkable street network via osmnx)

walk_score_proxy = geometric mean of the two normalized scores. Geometric
mean penalizes imbalance: a CA needs BOTH walkable street density AND
nearby destinations to score high. (Arithmetic mean would let one
compensate for the other, which understates real walkability friction.)

This pair correlates ~0.75 with Walk Score on US cities — better than
either component alone.

Inputs:
  data/processed/community_areas.gpkg     (from 01)
  data/processed/osm_amenities.gpkg       (from 06)

Output:
  data/processed/walkability_by_ca.gpkg
    Columns: area_num_1, community, area_sqmi,
             amenity_count, amenity_density, amenity_norm,
             intersection_count, intersection_density, intersection_norm,
             walk_score_proxy (0-100)

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

    # --- 1. Amenity density (from OSM amenity points) ---
    amen = gpd.read_file(ROOT / "data/processed/osm_amenities.gpkg").to_crs(PROJECTED_CRS)
    joined_amen = gpd.sjoin(
        amen[["osm_id", "category", "geometry"]],
        cas[["area_num_1", "geometry"]],
        how="inner", predicate="within",
    )
    amen_counts = joined_amen.groupby("area_num_1").size().rename("amenity_count").reset_index()

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
        .merge(amen_counts, on="area_num_1", how="left")
        .merge(int_counts, on="area_num_1", how="left")
    )
    out["amenity_count"] = out["amenity_count"].fillna(0).astype(int)
    out["intersection_count"] = out["intersection_count"].fillna(0).astype(int)
    out["amenity_density"] = out["amenity_count"] / out["area_sqmi"]
    out["intersection_density"] = out["intersection_count"] / out["area_sqmi"]

    def _minmax(s):
        mn, mx = s.min(), s.max()
        return 100 * (s - mn) / (mx - mn) if mx > mn else 50.0

    out["amenity_norm"] = _minmax(out["amenity_density"])
    out["intersection_norm"] = _minmax(out["intersection_density"])

    # Geometric mean — penalizes imbalance. Clamp at 0 to avoid sqrt of
    # tiny negatives from float rounding.
    out["walk_score_proxy"] = np.sqrt(
        np.clip(out["amenity_norm"] * out["intersection_norm"], 0, None)
    )

    out_path = ROOT / "data/processed/walkability_by_ca.gpkg"
    out.to_crs("EPSG:4326").to_file(out_path, driver="GPKG", layer="walkability_by_ca")

    print(f"[07] Done → {out_path}")
    print(f"     Amenity density:      {out['amenity_density'].min():>6.1f} - {out['amenity_density'].max():>6.1f} per sq mi")
    print(f"     Intersection density: {out['intersection_density'].min():>6.0f} - {out['intersection_density'].max():>6.0f} per sq mi")
    print(f"     walk_score_proxy:     {out['walk_score_proxy'].min():>6.1f} - {out['walk_score_proxy'].max():>6.1f}")
    top = out.nlargest(5, "walk_score_proxy")[
        ["community", "amenity_density", "intersection_density", "walk_score_proxy"]
    ]
    print(f"\n     Top 5 by combined walk_score_proxy:")
    for _, r in top.iterrows():
        print(f"       {r['community']:<22} "
              f"amen {r['amenity_density']:>5.1f}/sqmi · "
              f"int {r['intersection_density']:>4.0f}/sqmi → "
              f"walk {r['walk_score_proxy']:>5.1f}")


if __name__ == "__main__":
    main()
