#!/usr/bin/env python3
"""Compute gentrification velocity per CA using ACS 5-year deltas.

Pulls ACS 2018 5-year (centered ~2016.5) alongside the ACS 2023 5-year
data we already have (centered ~2021.5). About 5 years of change.

Three signals composited (each min-max normalized within survivors):
  - delta_median_rent_pct: rent rising = displacement pressure
  - delta_median_income_pct: incomers wealthier than departers
  - delta_pct_white: demographic shift toward (typically wealthier) white share

velocity_score = mean of the three normalized deltas, 0-100.
HIGH score = fast-changing neighborhood (Logan Square, Avondale).
LOW score  = stable neighborhood (Beverly, Mount Greenwood, lakefront premium).

Per user's framing — sidecar metric (visibility without weighting in the
composite). Lets you see gentrification trajectory without forcing the
model to penalize CAs you genuinely like.

Inputs:
  data/processed/community_areas.gpkg     (from 01)
  data/processed/acs_by_ca.gpkg           (from 04 — provides 2023 values)
  ~/.census_api_key                       (same key as 04)

Outputs:
  data/raw/acs_tracts_2018.json
  data/raw/tiger_tracts_il_2018/*.shp
  data/processed/gentrification_velocity_by_ca.gpkg
"""

import io
import json
import urllib.request
import warnings
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd

warnings.filterwarnings("ignore", category=UserWarning, module="geopandas")

ROOT = Path(__file__).resolve().parent.parent
KEY = (Path.home() / ".census_api_key").read_text().strip()
STATE_FIPS = "17"
COUNTY_FIPS = "031"
PROJECTED_CRS = "EPSG:3435"

PAST_YEAR = 2018
CURRENT_YEAR = 2023

# Minimal subset — only what we need for the three velocity signals
ACS_VARS = [
    "B19013_001E",      # median household income
    "B25064_001E",      # median gross rent
    "B02001_001E",      # total population (denominator for white share)
    "B02001_002E",      # white alone
    "B25001_001E",      # housing units (denominator for areal weighting)
    "B01001_001E",      # total population (count, used as weight)
]

COUNT_VARS = ["B25001_001E", "B01001_001E", "B02001_001E", "B02001_002E"]
MEDIAN_VARS = ["B19013_001E", "B25064_001E"]


def fetch_acs_2018() -> Path:
    out = ROOT / f"data/raw/acs_tracts_{PAST_YEAR}.json"
    if out.exists():
        print(f"[16] ACS {PAST_YEAR} cache hit → {out}")
        return out
    get_clause = ",".join(["NAME"] + ACS_VARS)
    url = (
        f"https://api.census.gov/data/{PAST_YEAR}/acs/acs5"
        f"?get={get_clause}&for=tract:*&in=state:{STATE_FIPS}+county:{COUNTY_FIPS}"
        f"&key={KEY}"
    )
    print(f"[16] Fetching ACS {PAST_YEAR} 5-year for Cook County tracts...")
    out.write_bytes(urllib.request.urlopen(url).read())
    return out


def fetch_tracts_2018() -> Path:
    out_dir = ROOT / f"data/raw/tiger_tracts_il_{PAST_YEAR}"
    if out_dir.exists() and any(out_dir.glob("*.shp")):
        print(f"[16] TIGER {PAST_YEAR} cache hit → {out_dir}")
        return out_dir
    url = (f"https://www2.census.gov/geo/tiger/TIGER{PAST_YEAR}/TRACT/"
           f"tl_{PAST_YEAR}_{STATE_FIPS}_tract.zip")
    print(f"[16] Fetching TIGER {PAST_YEAR} tract boundaries (Illinois)...")
    raw = urllib.request.urlopen(url).read()
    out_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        z.extractall(out_dir)
    return out_dir


def areal_aggregate_to_ca(acs_path, tracts_dir, cas_gdf):
    """Same pattern as 04: tract data + tract polygons + CA polygons → CA-level."""
    raw = json.loads(acs_path.read_text())
    acs = pd.DataFrame(raw[1:], columns=raw[0])
    acs["GEOID"] = acs["state"] + acs["county"] + acs["tract"]
    for v in ACS_VARS:
        acs[v] = pd.to_numeric(acs[v], errors="coerce")
        acs.loc[acs[v] < 0, v] = pd.NA

    shp = next(tracts_dir.glob("*.shp"))
    tracts = gpd.read_file(shp)
    tracts = tracts[tracts["COUNTYFP"] == COUNTY_FIPS].copy()
    tracts = tracts[["GEOID", "geometry"]].to_crs(PROJECTED_CRS)
    tracts["tract_area"] = tracts.area

    pieces = gpd.overlay(
        tracts, cas_gdf[["area_num_1", "geometry"]],
        how="intersection", keep_geom_type=True,
    )
    pieces["piece_area"] = pieces.area
    pieces["weight"] = pieces["piece_area"] / pieces["tract_area"]
    pieces = pieces.merge(acs[["GEOID"] + ACS_VARS], on="GEOID", how="left")

    # Allocate counts; area-weight medians
    for v in COUNT_VARS:
        pieces[v] = pieces[v] * pieces["weight"]
    counts = pieces.groupby("area_num_1")[COUNT_VARS].sum(min_count=1).reset_index()

    for v in MEDIAN_VARS:
        pieces[f"_w_{v}"] = pieces[v] * pieces["piece_area"]
    grp = pieces.groupby("area_num_1")
    weighted_area = grp["piece_area"].sum()
    medians = pd.DataFrame({"area_num_1": weighted_area.index})
    for v in MEDIAN_VARS:
        medians[v] = (grp[f"_w_{v}"].sum() / weighted_area).values

    return counts.merge(medians, on="area_num_1")


def main():
    acs_2018_path = fetch_acs_2018()
    tracts_2018_dir = fetch_tracts_2018()

    cas = gpd.read_file(ROOT / "data/processed/community_areas.gpkg").to_crs(PROJECTED_CRS)
    cas["area_num_1"] = cas["area_num_1"].astype(int)

    print(f"[16] Aggregating ACS {PAST_YEAR} to CA level...")
    past = areal_aggregate_to_ca(acs_2018_path, tracts_2018_dir, cas)

    # Pull the 2023 values from our existing acs_by_ca.gpkg
    current_acs = gpd.read_file(ROOT / "data/processed/acs_by_ca.gpkg", layer="acs_by_ca")
    current = current_acs[["area_num_1", "population", "median_hh_income",
                           "median_gross_rent_acs"]].copy()
    current["area_num_1"] = current["area_num_1"].astype(int)

    # We also need pct_white in 2023 from the raw ACS — re-derive from 2023 file
    raw_2023 = json.loads((ROOT / "data/raw/acs_tracts.json").read_text())
    # The 2023 file may not have B02001 because we didn't include it in script 04.
    # Pragmatic fallback: just compute pct_white_change from a fresh pull if needed.
    # Quick check:
    headers = raw_2023[0]
    has_b02001 = "B02001_001E" in headers and "B02001_002E" in headers
    if has_b02001:
        df = pd.DataFrame(raw_2023[1:], columns=headers)
        df["GEOID"] = df["state"] + df["county"] + df["tract"]
        for v in ("B02001_001E", "B02001_002E"):
            df[v] = pd.to_numeric(df[v], errors="coerce")
            df.loc[df[v] < 0, v] = pd.NA
        # Areal-aggregate B02001 to CA (need TIGER 2023, which 04 cached)
        shp_2023 = next((ROOT / "data/raw/tiger_tracts_il").glob("*.shp"))
        tr23 = gpd.read_file(shp_2023)
        tr23 = tr23[tr23["COUNTYFP"] == COUNTY_FIPS][["GEOID", "geometry"]].to_crs(PROJECTED_CRS)
        tr23["tract_area"] = tr23.area
        p = gpd.overlay(tr23, cas[["area_num_1", "geometry"]],
                        how="intersection", keep_geom_type=True)
        p["piece_area"] = p.area
        p["weight"] = p["piece_area"] / p["tract_area"]
        p = p.merge(df[["GEOID", "B02001_001E", "B02001_002E"]], on="GEOID", how="left")
        for v in ("B02001_001E", "B02001_002E"):
            p[v] = p[v] * p["weight"]
        white_2023 = p.groupby("area_num_1")[["B02001_001E", "B02001_002E"]].sum().reset_index()
        white_2023["pct_white_2023"] = 100 * white_2023["B02001_002E"] / white_2023["B02001_001E"]
    else:
        # B02001 wasn't pulled in 2023; we can still do income+rent deltas.
        print(f"[16] Note: 2023 ACS doesn't have B02001 (race) — skipping pct_white delta")
        white_2023 = pd.DataFrame({"area_num_1": cas["area_num_1"].values})
        white_2023["pct_white_2023"] = pd.NA

    # Compute past pct_white
    past["pct_white_2018"] = 100 * past["B02001_002E"] / past["B02001_001E"]

    # Merge everything
    delta = past[["area_num_1", "B19013_001E", "B25064_001E", "pct_white_2018"]].rename(
        columns={"B19013_001E": "median_hh_income_2018",
                  "B25064_001E": "median_gross_rent_2018"}
    ).merge(
        current.rename(columns={"median_hh_income": "median_hh_income_2023",
                                  "median_gross_rent_acs": "median_gross_rent_2023"}),
        on="area_num_1", how="left",
    ).merge(white_2023[["area_num_1", "pct_white_2023"]], on="area_num_1", how="left")

    delta["delta_income_pct"] = 100 * (
        delta["median_hh_income_2023"] - delta["median_hh_income_2018"]
    ) / delta["median_hh_income_2018"]
    delta["delta_rent_pct"] = 100 * (
        delta["median_gross_rent_2023"] - delta["median_gross_rent_2018"]
    ) / delta["median_gross_rent_2018"]
    delta["delta_pct_white"] = delta["pct_white_2023"] - delta["pct_white_2018"]

    # Composite velocity (min-max normalize within survivors? no — across all 77
    # because user wants comparison context across all of Chicago)
    def _mm(s):
        s = pd.to_numeric(s, errors="coerce")
        mn, mx = s.min(), s.max()
        return 100 * (s - mn) / (mx - mn) if mx > mn else 50.0

    delta["v_income"] = _mm(delta["delta_income_pct"])
    delta["v_rent"]   = _mm(delta["delta_rent_pct"])
    delta["v_white"]  = _mm(delta["delta_pct_white"])
    delta["velocity_score"] = (delta["v_income"] + delta["v_rent"] + delta["v_white"]) / 3

    out = cas[["area_num_1", "community", "geometry"]].merge(delta, on="area_num_1", how="left")
    out_path = ROOT / "data/processed/gentrification_velocity_by_ca.gpkg"
    out.to_crs("EPSG:4326").to_file(out_path, driver="GPKG", layer="gentrification_velocity")

    print(f"[16] Done → {out_path}")
    print(f"     Delta income range: {delta['delta_income_pct'].min():+.1f}% – {delta['delta_income_pct'].max():+.1f}%")
    print(f"     Delta rent range:   {delta['delta_rent_pct'].min():+.1f}% – {delta['delta_rent_pct'].max():+.1f}%")
    print(f"     Delta %white range: {delta['delta_pct_white'].min():+.1f}pp – {delta['delta_pct_white'].max():+.1f}pp")

    # Show velocity for the survivor list
    survivors = gpd.read_file(ROOT / "data/processed/survivors.gpkg")[["area_num_1", "community"]]
    surv_delta = survivors.merge(delta, on="area_num_1", how="left").sort_values("velocity_score", ascending=False)
    print(f"\n     Survivors ranked by gentrification velocity (high = changing fast):")
    print(f"     {'CA':<22} {'velocity':>8}  {'income':>7}  {'rent':>7}  {'%white':>7}")
    for _, r in surv_delta.iterrows():
        income_str = f"{r['delta_income_pct']:+.1f}%" if pd.notna(r['delta_income_pct']) else "  n/a"
        rent_str   = f"{r['delta_rent_pct']:+.1f}%"   if pd.notna(r['delta_rent_pct'])   else "  n/a"
        white_str  = f"{r['delta_pct_white']:+.1f}pp" if pd.notna(r['delta_pct_white'])  else "  n/a"
        vel_str    = f"{r['velocity_score']:.1f}"     if pd.notna(r['velocity_score'])   else "n/a"
        print(f"     {r['community']:<22} {vel_str:>8}  {income_str:>7}  {rent_str:>7}  {white_str:>7}")


if __name__ == "__main__":
    main()
