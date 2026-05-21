#!/usr/bin/env python3
"""Apply hard filters + soft scoring to produce a ranked survivor list.

Hard filters cut the 77 Community Areas down to those that meet
non-negotiable thresholds (rent ceiling, safety floor, transit minimum,
housing-quality floor). Soft scores rank the survivors on the remaining
gradient criteria.

Inputs (all from earlier scripts):
  community_areas.gpkg           (01)
  crime_by_ca.csv                (02)
  violations_by_ca.csv           (02)
  cta_rail_stops.gpkg            (03)
  cta_bus_stops.gpkg             (03)
  acs_by_ca.gpkg                 (04) — for population and housing_units
  rent_by_ca.gpkg                (05)
  walkability_by_ca.gpkg         (07)

Outputs:
  data/processed/survivors.gpkg
  data/processed/drop_log.csv
  output/ranked_shortlist.csv

Hard-filter thresholds (edit the constants below to retune):

  RENT_CEILING = 3200
    NOTE: the rent source is currently ACS B25064 (2019-2023 5-yr median
    gross rent), not Zillow ZORI as originally planned (zip polygons
    couldn't be sourced — see 05_fetch_rents.py for the story). ACS rents
    run ~25-35% below current Zillow numbers in Chicago. A $3,200 ACS
    ceiling effectively filters nobody. Either lower the threshold (~$2,000
    ACS ≈ ~$2,800 ZORI current) or accept that rent is currently a soft
    score only.

  SAFETY_MULTIPLIER = 1.5
    Drop CAs whose 24mo violent crime rate (per 1,000 pop) exceeds 1.5×
    the median across all CAs. Conservative; bump to 2.0 if too many cut.

  TRANSIT_MIN_RAIL = 1
  TRANSIT_MIN_BUS_PER_SQMI = 10
    Pass if either condition met. Filters true transit deserts only.

  VIOLATIONS_DROP_TOP_PCT = 10
    Drop the worst-10% CAs by open building violations per 1,000 units.
"""

import warnings
from pathlib import Path

import geopandas as gpd
import pandas as pd

warnings.filterwarnings("ignore", category=UserWarning, module="geopandas")

ROOT = Path(__file__).resolve().parent.parent
PROJECTED_CRS = "EPSG:3435"
SQ_FT_PER_SQ_MI = 27_878_400

# -- thresholds ---------------------------------------------------------
RENT_CEILING = 3200
SAFETY_MULTIPLIER = 1.5
SAFETY_MULTIPLIER_COMMERCIAL = 2.5   # exception: commercial-heavy CAs (see COMMERCIAL_CAS)
TRANSIT_MIN_RAIL = 1
TRANSIT_MIN_BUS_PER_SQMI = 10
VIOLATIONS_DROP_TOP_PCT = 10
PCT_25_39_MIN = 28.0                 # demographic-fit hard filter (user-specific)

# CAs whose daytime population dwarfs residential pop, inflating per-capita
# crime rates against the residential denominator. We apply a higher safety
# multiplier to these so they're not unfairly cut by a measurement artifact.
# Hand-curated; expand only if you can defend the addition.
COMMERCIAL_CAS = {
    "LOOP",                   # downtown business district
    "NEAR NORTH SIDE",        # Magnificent Mile, River North
    "NEAR WEST SIDE",         # West Loop, Fulton Market, United Center area
    "NEAR SOUTH SIDE",        # McCormick Place, museum campus
}

# Structural exclusions — CAs whose metrics are real but not interpretable
# as residential signal. OHARE = the airport CA; ~15k residents are mostly
# cargo/airline workforce housing, not a neighborhood in the normal sense.
ALWAYS_EXCLUDE = {"OHARE"}

# Soft-score weights (must sum to 1.0).
# 5-axis composite — adds age_match (% adults 25-39 in CA, normalized) as a
# user-profile axis. Weights are derived from a "profile-matched" framing:
#   - quality-of-living axes (walk, transit) get higher weight
#   - safety still meaningful as headroom above the hard filter
#   - cost de-emphasized (the $135k salary makes it less of a constraint)
#   - age_match treated as peer to safety in importance
# Original 4-axis ratios (walk 0.30 / transit 0.30 / safety 0.25 / cost 0.15)
# scaled to 0.80 total to make room for age_match at 0.20.
WEIGHTS = {
    "walk_score_proxy":    0.24,
    "transit_headroom":    0.24,
    "safety_headroom":     0.20,
    "cost_headroom":       0.12,
    "age_match":           0.20,
}


def load_all():
    """Load every per-CA layer and merge to one DataFrame keyed on area_num_1."""
    cas = gpd.read_file(ROOT / "data/processed/community_areas.gpkg").to_crs(PROJECTED_CRS)
    cas["area_num_1"] = cas["area_num_1"].astype(int)
    cas["area_sqmi"] = cas.area / SQ_FT_PER_SQ_MI

    crime = pd.read_csv(ROOT / "data/processed/crime_by_ca.csv")
    crime = crime[crime["community_area"].notna() & (crime["community_area"] != "")]
    crime["area_num_1"] = crime["community_area"].astype(int)
    crime = crime[["area_num_1", "violent_count"]]

    viol = pd.read_csv(ROOT / "data/processed/violations_by_ca.csv")
    viol["area_num_1"] = viol["community_area"].astype(int)
    viol = viol[["area_num_1", "open_violation_count"]]

    acs = gpd.read_file(ROOT / "data/processed/acs_by_ca.gpkg")
    acs["area_num_1"] = acs["area_num_1"].astype(int)
    acs = acs[["area_num_1", "population", "housing_units", "median_age",
               "median_hh_income", "pct_25_39", "pct_never_married_15plus",
               "pct_bachelors_plus", "pct_wfh"]]

    rent = gpd.read_file(ROOT / "data/processed/rent_by_ca.gpkg")
    rent["area_num_1"] = rent["area_num_1"].astype(int)
    rent = rent[["area_num_1", "median_rent_current"]]

    walk = gpd.read_file(ROOT / "data/processed/walkability_by_ca.gpkg")
    walk["area_num_1"] = walk["area_num_1"].astype(int)
    walk = walk[["area_num_1", "amenity_count", "amenity_density", "walk_score_proxy"]]

    # Transit: spatial-join points into CAs
    rail = gpd.read_file(ROOT / "data/processed/cta_rail_stops.gpkg").to_crs(PROJECTED_CRS)
    bus = gpd.read_file(ROOT / "data/processed/cta_bus_stops.gpkg").to_crs(PROJECTED_CRS)
    rail_per_ca = (
        gpd.sjoin(rail, cas[["area_num_1", "geometry"]], how="inner", predicate="within")
        .groupby("area_num_1").size().rename("rail_count").reset_index()
    )
    bus_per_ca = (
        gpd.sjoin(bus, cas[["area_num_1", "geometry"]], how="inner", predicate="within")
        .groupby("area_num_1").size().rename("bus_count").reset_index()
    )

    df = (cas[["area_num_1", "community", "area_sqmi", "geometry"]]
          .merge(crime, on="area_num_1", how="left")
          .merge(viol,  on="area_num_1", how="left")
          .merge(acs,   on="area_num_1", how="left")
          .merge(rent,  on="area_num_1", how="left")
          .merge(walk,  on="area_num_1", how="left")
          .merge(rail_per_ca, on="area_num_1", how="left")
          .merge(bus_per_ca,  on="area_num_1", how="left"))
    df["rail_count"] = df["rail_count"].fillna(0).astype(int)
    df["bus_count"] = df["bus_count"].fillna(0).astype(int)
    df["bus_per_sqmi"] = df["bus_count"] / df["area_sqmi"]

    # Derived rates
    df["violent_per_1k"] = 1000 * df["violent_count"].fillna(0) / df["population"]
    df["violations_per_1k_units"] = 1000 * df["open_violation_count"].fillna(0) / df["housing_units"]

    return df


def apply_filters(df: pd.DataFrame):
    drop_log = []   # (area_num_1, community, filter, value, threshold)

    df["passed"] = True

    # 0. Structural exclusions (OHARE etc.) — applied first, no scoring
    excl_fail = df["community"].isin(ALWAYS_EXCLUDE)
    for _, r in df[excl_fail].iterrows():
        drop_log.append((r["area_num_1"], r["community"], "always_exclude",
                         "structural", f"in {sorted(ALWAYS_EXCLUDE)}"))
    df.loc[excl_fail, "passed"] = False

    # 1. Rent ceiling
    rent_fail = (df["median_rent_current"] > RENT_CEILING) & df["median_rent_current"].notna() & df["passed"]
    for _, r in df[rent_fail].iterrows():
        drop_log.append((r["area_num_1"], r["community"], "rent_ceiling",
                         r["median_rent_current"], RENT_CEILING))
    df.loc[rent_fail, "passed"] = False

    # 2. Safety floor — commercial CAs get a higher multiplier to compensate
    # for inflated per-resident rates driven by non-resident daytime population.
    citywide_median_rate = df["violent_per_1k"].median()
    df["_safety_cap"] = df["community"].apply(
        lambda c: SAFETY_MULTIPLIER_COMMERCIAL * citywide_median_rate
        if c in COMMERCIAL_CAS else SAFETY_MULTIPLIER * citywide_median_rate
    )
    safety_fail = (df["violent_per_1k"] > df["_safety_cap"]) & df["passed"]
    for _, r in df[safety_fail].iterrows():
        drop_log.append((r["area_num_1"], r["community"], "safety_floor",
                         r["violent_per_1k"], r["_safety_cap"]))
    df.loc[safety_fail, "passed"] = False

    # 3. Transit minimum
    transit_fail = (
        (df["rail_count"] < TRANSIT_MIN_RAIL)
        & (df["bus_per_sqmi"] < TRANSIT_MIN_BUS_PER_SQMI)
        & df["passed"]
    )
    for _, r in df[transit_fail].iterrows():
        drop_log.append((r["area_num_1"], r["community"], "transit_minimum",
                         f"rail={r['rail_count']}, bus/sqmi={r['bus_per_sqmi']:.1f}",
                         f"rail≥{TRANSIT_MIN_RAIL} OR bus/sqmi≥{TRANSIT_MIN_BUS_PER_SQMI}"))
    df.loc[transit_fail, "passed"] = False

    # 4. Housing-quality (drop top decile by violations per 1k units)
    viol_cutoff = df["violations_per_1k_units"].quantile(1 - VIOLATIONS_DROP_TOP_PCT / 100)
    viol_fail = (df["violations_per_1k_units"] > viol_cutoff) & df["passed"]
    for _, r in df[viol_fail].iterrows():
        drop_log.append((r["area_num_1"], r["community"], "housing_violations_top_decile",
                         r["violations_per_1k_units"], viol_cutoff))
    df.loc[viol_fail, "passed"] = False

    # 5. Demographic fit — drop CAs where the user's cohort (25-39) is
    # below the threshold. Family-residential outer-ring CAs cluster <28%;
    # cohort-hub urban CAs cluster >35%. 28% is the user-set threshold.
    age_fail = (df["pct_25_39"] < PCT_25_39_MIN) & df["passed"] & df["pct_25_39"].notna()
    for _, r in df[age_fail].iterrows():
        drop_log.append((r["area_num_1"], r["community"], "pct_25_39_floor",
                         r["pct_25_39"], PCT_25_39_MIN))
    df.loc[age_fail, "passed"] = False

    drop_df = pd.DataFrame(drop_log, columns=["area_num_1", "community", "filter", "value", "threshold"])
    return df, drop_df


def compute_soft_scores(survivors: pd.DataFrame) -> pd.DataFrame:
    """Min-max normalize each axis to 0-100, then weighted composite."""
    s = survivors.copy()

    # walkability already 0-100 from script 07, but renormalize within survivors
    s["walk_score_proxy"] = minmax(s["walk_score_proxy"])

    # transit_headroom: weight rail more than bus (rail = high-frequency, weather-protected)
    s["transit_raw"] = (s["rail_count"] * 5) + s["bus_per_sqmi"]
    s["transit_headroom"] = minmax(s["transit_raw"])

    # safety_headroom: inverted — lower crime → higher score
    s["safety_headroom"] = minmax(-s["violent_per_1k"])

    # cost_headroom: inverted — lower rent → higher score
    s["cost_headroom"] = minmax(-s["median_rent_current"])

    # age_match: higher % adults 25-39 → higher score (within survivors)
    s["age_match"] = minmax(s["pct_25_39"])

    # composite
    s["composite_score"] = sum(s[k] * w for k, w in WEIGHTS.items())
    return s


def minmax(series: pd.Series) -> pd.Series:
    mn, mx = series.min(), series.max()
    if mx == mn:
        return pd.Series([50.0] * len(series), index=series.index)
    return 100 * (series - mn) / (mx - mn)


def main():
    print("[08] Loading and merging per-CA layers...")
    df = load_all()
    print(f"     Total CAs loaded: {len(df)}")

    print("[08] Applying hard filters...")
    df, drop_df = apply_filters(df)
    survivors = df[df["passed"]].copy()
    print(f"     Survivors: {len(survivors)} / {len(df)}")
    print(f"     Dropped:   {len(drop_df)} filter hits across {drop_df['area_num_1'].nunique()} CAs")

    print("\n[08] Drop reasons:")
    for f, n in drop_df["filter"].value_counts().items():
        print(f"       {f:<30} {n} CAs")

    print("\n[08] Computing soft scores...")
    survivors = compute_soft_scores(survivors)

    # Outputs
    out_gpkg = ROOT / "data/processed/survivors.gpkg"
    survivors_for_gpkg = gpd.GeoDataFrame(survivors, crs=PROJECTED_CRS).to_crs("EPSG:4326")
    survivors_for_gpkg.to_file(out_gpkg, driver="GPKG", layer="survivors")

    drop_log_path = ROOT / "data/processed/drop_log.csv"
    drop_df.to_csv(drop_log_path, index=False)

    rank_cols = ["area_num_1", "community", "composite_score",
                 "walk_score_proxy", "transit_headroom", "safety_headroom",
                 "cost_headroom", "age_match",
                 "median_rent_current", "violent_per_1k", "rail_count", "bus_per_sqmi",
                 "population", "median_hh_income", "pct_25_39", "pct_never_married_15plus",
                 "pct_bachelors_plus", "pct_wfh"]
    shortlist = survivors[rank_cols].sort_values("composite_score", ascending=False)
    shortlist_path = ROOT / "output/ranked_shortlist.csv"
    shortlist.to_csv(shortlist_path, index=False)

    print(f"\n[08] Done.")
    print(f"     → {out_gpkg}")
    print(f"     → {drop_log_path}")
    print(f"     → {shortlist_path}")

    print(f"\n[08] Ranked survivors by composite score (5-axis):")
    print(f"     {'rank':>4} {'CA':<22} {'score':>6} {'walk':>5} {'tran':>5} {'safe':>5} {'cost':>5} {'age':>5} {'rent':>7}")
    for i, (_, r) in enumerate(shortlist.iterrows(), 1):
        print(f"     {i:>4} {r['community']:<22} {r['composite_score']:>6.1f} "
              f"{r['walk_score_proxy']:>5.1f} {r['transit_headroom']:>5.1f} "
              f"{r['safety_headroom']:>5.1f} {r['cost_headroom']:>5.1f} "
              f"{r['age_match']:>5.1f} "
              f"${r['median_rent_current']:>6.0f}")


if __name__ == "__main__":
    main()
