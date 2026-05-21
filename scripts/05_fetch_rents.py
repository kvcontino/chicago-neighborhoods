#!/usr/bin/env python3
"""Fetch Zillow Observed Rent Index (ZORI), areal-weighted to Community Areas.

ZORI is a smoothed, seasonally-adjusted typical-rent index published monthly
at zip-code level. Tracks the typical for-rent unit, not raw asking-rent —
directionally accurate, absolute values typically within ±15% of what you'd
see on apartments.com.

Zip-polygon source:
  We need zip-code polygons to areal-weight ZORI from zips to CAs. The
  CDP zip datasets (gdcf-axmw, vsuf-uefy, etc.) return empty stubs as of
  2026-05. The Census TIGER national ZCTA file is 528MB. We use a curated
  middle path:
    OpenDataDE/State-zip-code-GeoJSON (Illinois file, ~30MB, 1,384 zips)
  These are 2010 ZCTA boundaries — zip lines shift slowly, so this is
  fine for areal interpolation in 2026.

Inputs:
  data/processed/community_areas.gpkg     (from script 01)

Outputs:
  data/raw/zori_zip.csv                   (cached)
  data/raw/il_zip_boundaries.json         (cached, ~30MB)
  data/processed/rent_by_ca.gpkg          (canonical rent layer for script 08)

Areal-weighting math: same pattern as 04_fetch_acs.py. Intersect zip
polygons × CA polygons; for each piece, weight = piece_area / zip_area.
Rent is a per-unit rate (not a count), so we weight rent by piece_area
within each CA: weighted_rent_ca = SUM(rent_zip * piece_area) / SUM(piece_area).
"""

import urllib.request
import warnings
from pathlib import Path

import geopandas as gpd
import pandas as pd

warnings.filterwarnings("ignore", category=UserWarning, module="geopandas")

ROOT = Path(__file__).resolve().parent.parent

ZORI_URL = "https://files.zillowstatic.com/research/public_csvs/zori/Zip_zori_uc_sfrcondomfr_sm_sa_month.csv"
ZIP_GEOJSON_URL = "https://raw.githubusercontent.com/OpenDataDE/State-zip-code-GeoJSON/master/il_illinois_zip_codes_geo.min.json"
PROJECTED_CRS = "EPSG:3435"


def fetch(url: str, out: Path, label: str) -> Path:
    if out.exists():
        print(f"[05] {label} cache hit → {out}")
        return out
    print(f"[05] Fetching {label}...")
    out.write_bytes(urllib.request.urlopen(url).read())
    return out


def main():
    zori_path = fetch(ZORI_URL, ROOT / "data/raw/zori_zip.csv", "Zillow ZORI")
    zips_path = fetch(ZIP_GEOJSON_URL, ROOT / "data/raw/il_zip_boundaries.json", "IL zip polygons")

    # 1. Load ZORI; identify current + year-ago monthly columns
    zori = pd.read_csv(zori_path, dtype={"RegionName": str})
    month_cols = sorted([c for c in zori.columns if c.startswith("2") and "-" in c])
    if len(month_cols) < 13:
        raise SystemExit(f"[05] ZORI has only {len(month_cols)} monthly cols; expected 13+")
    current_col, year_ago_col = month_cols[-1], month_cols[-13]
    print(f"[05] ZORI window: YoY {year_ago_col} → current {current_col}")

    zori_il = zori[zori["State"] == "IL"].copy()
    zori_il["median_rent_current"] = zori_il[current_col]
    zori_il["rent_yoy_pct"] = (
        100 * (zori_il[current_col] - zori_il[year_ago_col]) / zori_il[year_ago_col]
    )
    zori_slim = zori_il[["RegionName", "median_rent_current", "rent_yoy_pct"]]

    # 2. Load zip polygons, filter to Chicago range (606xx) + project
    zips = gpd.read_file(zips_path)
    zip_col = "ZCTA5CE10"   # OpenDataDE 2010-ZCTA naming
    zips = zips[zips[zip_col].str.startswith("606")].copy()
    zips = zips.rename(columns={zip_col: "zip"})[["zip", "geometry"]].to_crs(PROJECTED_CRS)
    zips["zip_area"] = zips.area
    print(f"[05] Loaded {len(zips)} Chicago-range zips")

    zips = zips.merge(zori_slim, left_on="zip", right_on="RegionName", how="left")
    n_with_rent = zips["median_rent_current"].notna().sum()
    print(f"[05] Zips with ZORI: {n_with_rent}/{len(zips)}")

    # 3. CA polygons + intersection
    cas = gpd.read_file(ROOT / "data/processed/community_areas.gpkg").to_crs(PROJECTED_CRS)
    cas["area_num_1"] = cas["area_num_1"].astype(int)

    print(f"[05] Intersecting {len(zips)} zips × {len(cas)} CAs...")
    pieces = gpd.overlay(
        zips[["zip", "zip_area", "median_rent_current", "rent_yoy_pct", "geometry"]],
        cas[["area_num_1", "community", "geometry"]],
        how="intersection",
        keep_geom_type=True,
    )
    pieces["piece_area"] = pieces.area

    # 4. Area-weighted mean for rent within each CA (only over zips with data)
    with_rent = pieces.dropna(subset=["median_rent_current"]).copy()
    with_rent["_w_rent"] = with_rent["median_rent_current"] * with_rent["piece_area"]
    with_rent["_w_yoy"] = with_rent["rent_yoy_pct"] * with_rent["piece_area"]
    grp = with_rent.groupby("area_num_1")
    by_ca = pd.DataFrame({
        "area_num_1": grp.size().index,
        "median_rent_current": (grp["_w_rent"].sum() / grp["piece_area"].sum()).values,
        "rent_yoy_pct": (grp["_w_yoy"].sum() / grp["piece_area"].sum()).values,
    })
    # Coverage diagnostic: what fraction of each CA's area is covered by a rent-bearing zip?
    total_area_by_ca = pieces.groupby("area_num_1")["piece_area"].sum()
    rent_area_by_ca = with_rent.groupby("area_num_1")["piece_area"].sum()
    coverage = (100 * rent_area_by_ca / total_area_by_ca).reindex(by_ca["area_num_1"]).values
    by_ca["rent_coverage_pct"] = coverage
    by_ca["rent_source"] = f"Zillow ZORI {current_col}"

    out_gdf = cas[["area_num_1", "community", "geometry"]].merge(
        by_ca, on="area_num_1", how="left"
    ).to_crs("EPSG:4326")

    out_path = ROOT / "data/processed/rent_by_ca.gpkg"
    out_gdf.to_file(out_path, driver="GPKG", layer="rent_by_ca")

    print(f"[05] Done → {out_path}")
    n_covered = out_gdf["median_rent_current"].notna().sum()
    print(f"     CAs with rent data: {n_covered} / 77")
    if n_covered > 0:
        print(f"     Median rent range: ${out_gdf['median_rent_current'].min():,.0f} – ${out_gdf['median_rent_current'].max():,.0f}")
        print(f"     YoY range:         {out_gdf['rent_yoy_pct'].min():+.1f}% – {out_gdf['rent_yoy_pct'].max():+.1f}%")
        low_coverage = out_gdf[(out_gdf["rent_coverage_pct"] < 50) & out_gdf["median_rent_current"].notna()]
        if len(low_coverage):
            print(f"     {len(low_coverage)} CAs have <50% area covered by rent-bearing zips (treat with care):")
            for _, r in low_coverage.iterrows():
                print(f"       {r['community']:<22} coverage={r['rent_coverage_pct']:.0f}%")


if __name__ == "__main__":
    main()
