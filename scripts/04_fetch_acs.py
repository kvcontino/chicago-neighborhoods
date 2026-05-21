#!/usr/bin/env python3
"""Fetch Census ACS 5-year demographics, areal-weighted to Chicago Community Areas.

Inputs:
  ~/.census_api_key                          — API key (mode 600)
  data/processed/community_areas.gpkg        — from script 01

Outputs:
  data/raw/acs_tracts.json                   — raw API response (cached)
  data/raw/tiger_tracts_il/*.shp             — TIGER tract boundaries (cached)
  data/processed/acs_by_ca.gpkg              — CA polygons + ACS metrics

What this script does:
  1. Pulls ~20 ACS variables at tract level for Cook County via Census API.
  2. Downloads TIGER tract boundary shapefile for Illinois (filtered to Cook).
  3. Areal-weighted spatial join: tracts × Community Areas → per-piece pieces,
     each piece carries the fraction of its tract's area that falls in a CA.
  4. Aggregates COUNT variables (population, housing units, never-married
     count, etc.) by SUM(value * area_weight).
  5. Aggregates MEDIAN variables (income, rent, age) by SUM(value * piece_area)
     / SUM(piece_area) — an area-weighted mean of medians. This is the
     standard approximation. It's wrong in the strict statistical sense
     (true medians need microdata), but acceptable for cross-CA ranking
     and within ~5% of the truth in practice.
  6. Computes derived percentages and writes to a GeoPackage joined to
     CA geometry, ready to symbolize in QGIS or filter in script 08.

Why areal weighting and not centroid assignment:
  Cook County has ~1,300 tracts; ~800 are in Chicago. Some tracts straddle
  CA boundaries (especially small CAs like the Loop). Centroid-assignment
  would mis-attribute population for those boundary tracts. Areal weighting
  splits the count proportionally. Same pattern reused in script 05 for rents.
"""

import io
import json
import sys
import urllib.request
import zipfile
import warnings
from pathlib import Path

import geopandas as gpd
import pandas as pd

warnings.filterwarnings("ignore", category=UserWarning, module="geopandas")

# -- config -------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
KEY = (Path.home() / ".census_api_key").read_text().strip()

ACS_YEAR = 2023
STATE_FIPS = "17"     # Illinois
COUNTY_FIPS = "031"   # Cook
PROJECTED_CRS = "EPSG:3435"  # Illinois State Plane East (feet); right for area calcs in Chicago

ACS_VARS = [
    # B01001 - sex by age
    "B01001_001E",                                       # total population
    "B01001_011E", "B01001_012E", "B01001_013E",         # M 25-29, 30-34, 35-39
    "B01001_035E", "B01001_036E", "B01001_037E",         # F 25-29, 30-34, 35-39
    # B12001 - sex by marital status (pop 15+)
    "B12001_001E",                                       # total 15+
    "B12001_003E", "B12001_012E",                        # never married (M, F)
    # B15003 - educational attainment (pop 25+)
    "B15003_001E",                                       # total 25+
    "B15003_022E", "B15003_023E", "B15003_024E", "B15003_025E",   # Bachelor's, Master's, Pro, PhD
    # Income & housing
    "B19013_001E",                                       # median household income
    "B25001_001E",                                       # housing units
    "B25064_001E",                                       # median gross rent
    # B08006 - means of transport to work
    "B08006_001E",                                       # total workers
    "B08006_017E",                                       # worked from home
    # Median age (single variable)
    "B01002_001E",
    # B25034 - year structure built (all tenures)
    # _001 total / _002 2014+ / _003 2010-13 / _004 2000-09
    "B25034_001E", "B25034_002E", "B25034_003E", "B25034_004E",
    # B25042 - TENURE BY BEDROOMS (renter side: _009 total / _010 studio / _011 1BR / _012 2BR / _013 3BR / _014 4BR / _015 5BR+)
    "B25042_009E",
    "B25042_010E", "B25042_011E",                        # studios + 1BR (small units)
    "B25042_012E", "B25042_013E", "B25042_014E", "B25042_015E",  # 2BR+ (WFH-friendly)
]

COUNT_VARS = [v for v in ACS_VARS if v not in
              ("B19013_001E", "B25064_001E", "B01002_001E")]
MEDIAN_VARS = ["B19013_001E", "B25064_001E", "B01002_001E"]


# -- fetchers -----------------------------------------------------------

def fetch_acs() -> Path:
    out = ROOT / "data/raw/acs_tracts.json"
    if out.exists():
        print(f"[04] ACS cache hit  → {out}")
        return out
    get_clause = ",".join(["NAME"] + ACS_VARS)
    url = (
        f"https://api.census.gov/data/{ACS_YEAR}/acs/acs5"
        f"?get={get_clause}"
        f"&for=tract:*"
        f"&in=state:{STATE_FIPS}+county:{COUNTY_FIPS}"
        f"&key={KEY}"
    )
    print(f"[04] Fetching ACS {ACS_YEAR} 5-year, Cook County tracts...")
    data = urllib.request.urlopen(url).read()
    out.write_bytes(data)
    return out


def fetch_tract_boundaries() -> Path:
    out_dir = ROOT / "data/raw/tiger_tracts_il"
    if out_dir.exists() and any(out_dir.glob("*.shp")):
        print(f"[04] TIGER cache hit → {out_dir}")
        return out_dir
    url = (f"https://www2.census.gov/geo/tiger/TIGER{ACS_YEAR}/TRACT/"
           f"tl_{ACS_YEAR}_{STATE_FIPS}_tract.zip")
    print(f"[04] Fetching TIGER tract boundaries (Illinois)...")
    raw = urllib.request.urlopen(url).read()
    out_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        z.extractall(out_dir)
    return out_dir


# -- main ---------------------------------------------------------------

def main():
    acs_path = fetch_acs()
    tracts_dir = fetch_tract_boundaries()

    # 1. ACS JSON → DataFrame
    raw = json.loads(acs_path.read_text())
    acs = pd.DataFrame(raw[1:], columns=raw[0])
    acs["GEOID"] = acs["state"] + acs["county"] + acs["tract"]
    for v in ACS_VARS:
        acs[v] = pd.to_numeric(acs[v], errors="coerce")
        # Census uses large negative sentinels (e.g. -666666666) for "not available".
        # Treat anything < 0 as missing.
        acs.loc[acs[v] < 0, v] = pd.NA

    # 2. Tract polygons → filter to Cook
    shp = next(tracts_dir.glob("*.shp"))
    tracts = gpd.read_file(shp)
    tracts = tracts[tracts["COUNTYFP"] == COUNTY_FIPS].copy()
    tracts = tracts[["GEOID", "geometry"]].to_crs(PROJECTED_CRS)
    tracts["tract_area"] = tracts.area

    # 3. CA polygons (canonical geometry from script 01)
    cas = gpd.read_file(ROOT / "data/processed/community_areas.gpkg").to_crs(PROJECTED_CRS)
    cas["area_num_1"] = cas["area_num_1"].astype(int)

    # 4. Intersection: per-piece records with (GEOID, area_num_1, piece_area)
    print(f"[04] Intersecting {len(tracts)} tracts × {len(cas)} CAs...")
    pieces = gpd.overlay(
        tracts,
        cas[["area_num_1", "community", "geometry"]],
        how="intersection",
        keep_geom_type=True,
    )
    pieces["piece_area"] = pieces.area
    pieces["weight"] = pieces["piece_area"] / pieces["tract_area"]

    # Merge ACS values in
    pieces = pieces.merge(acs[["GEOID"] + ACS_VARS], on="GEOID", how="left")

    # 5a. Counts: allocate by area weight, sum within CA
    for v in COUNT_VARS:
        pieces[v] = pieces[v] * pieces["weight"]
    counts = pieces.groupby("area_num_1")[COUNT_VARS].sum(min_count=1).reset_index()

    # 5b. Medians: area-weighted mean (best approximation without microdata)
    for v in MEDIAN_VARS:
        pieces[f"_w_{v}"] = pieces[v] * pieces["piece_area"]
    grp = pieces.groupby("area_num_1")
    weighted_area = grp["piece_area"].sum()
    medians = pd.DataFrame({"area_num_1": weighted_area.index})
    for v in MEDIAN_VARS:
        medians[v] = (grp[f"_w_{v}"].sum() / weighted_area).values

    agg = counts.merge(medians, on="area_num_1")

    # 6. Derived percentages
    agg["pct_25_39"] = 100 * (
        agg["B01001_011E"] + agg["B01001_012E"] + agg["B01001_013E"]
        + agg["B01001_035E"] + agg["B01001_036E"] + agg["B01001_037E"]
    ) / agg["B01001_001E"]
    agg["pct_never_married_15plus"] = 100 * (
        agg["B12001_003E"] + agg["B12001_012E"]
    ) / agg["B12001_001E"]
    agg["pct_bachelors_plus"] = 100 * (
        agg["B15003_022E"] + agg["B15003_023E"] + agg["B15003_024E"] + agg["B15003_025E"]
    ) / agg["B15003_001E"]
    agg["pct_wfh"] = 100 * agg["B08006_017E"] / agg["B08006_001E"]

    # B25034: % housing units built since 2000 (HVAC/finish quality proxy)
    agg["pct_built_2000_plus"] = 100 * (
        agg["B25034_002E"] + agg["B25034_003E"] + agg["B25034_004E"]
    ) / agg["B25034_001E"]

    # B25042 renter-side: % of rental units with 2BR+ (WFH-friendly,
    # room for office + two cats per user's stated needs).
    agg["pct_rentals_2br_plus"] = 100 * (
        agg["B25042_012E"] + agg["B25042_013E"]
        + agg["B25042_014E"] + agg["B25042_015E"]
    ) / agg["B25042_009E"]

    # Rename for legibility
    agg = agg.rename(columns={
        "B01001_001E": "population",
        "B25001_001E": "housing_units",
        "B19013_001E": "median_hh_income",
        "B25064_001E": "median_gross_rent_acs",
        "B01002_001E": "median_age",
    })

    out_cols = [
        "area_num_1", "population", "housing_units", "median_age",
        "median_hh_income", "median_gross_rent_acs",
        "pct_25_39", "pct_never_married_15plus", "pct_bachelors_plus", "pct_wfh",
        "pct_built_2000_plus", "pct_rentals_2br_plus",
    ]
    final = agg[out_cols].copy()

    # Join back to CA geometry for QGIS-ready output
    out_gdf = cas[["area_num_1", "community", "geometry"]].merge(
        final, on="area_num_1", how="left"
    ).to_crs("EPSG:4326")

    out_path = ROOT / "data/processed/acs_by_ca.gpkg"
    out_gdf.to_file(out_path, driver="GPKG", layer="acs_by_ca")
    print(f"[04] Done → {out_path}")
    print(f"     CAs with population data: {final['population'].notna().sum()} / 77")
    print(f"     Median HH income range: ${final['median_hh_income'].min():,.0f} – ${final['median_hh_income'].max():,.0f}")
    print(f"     % age 25-39 range:      {final['pct_25_39'].min():.1f}% – {final['pct_25_39'].max():.1f}%")


if __name__ == "__main__":
    main()
