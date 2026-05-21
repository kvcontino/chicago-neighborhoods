#!/usr/bin/env python3
"""Build qualitative typology records for surviving Community Areas.

Categorical archetypes instead of numeric scores — the social-scene / vibe
criterion the user explicitly didn't want to score against bad proxies.
See docs/typology_sources.md for the source-tier policy.

This script is a HUMAN-IN-THE-LOOP framework, not a fully automated step:
  - For each survivor CA, it creates a YAML record skeleton.
  - 5 sample records are pre-drafted (LAKE VIEW, LOGAN SQUARE, ROGERS PARK,
    LINCOLN PARK, EDGEWATER) using general knowledge — citations marked
    TODO and must be verified against tier-1/tier-2 sources before
    treating any archetype as final.
  - Other survivors get an empty record stub for human entry.

Inputs:
  data/processed/survivors.gpkg

Outputs:
  data/processed/typology.yaml      — one record per survivor CA
  data/processed/typology.gpkg      — survivors + typology fields joined
                                       (only built if typology.yaml exists
                                        AND has filled records)

To use:
  1. Run this script. It writes typology.yaml with 5 drafted + N empty stubs.
  2. Read docs/typology_sources.md for the source-tier policy.
  3. Fill in records by hand (or with another script you write) using
     Marginal Revolution / Block Club Chicago / WBEZ etc. citations.
  4. Re-run this script. It re-reads typology.yaml, validates structure,
     and writes typology.gpkg ready to symbolize categorically in QGIS.
"""

import sys
import textwrap
from pathlib import Path

import geopandas as gpd

try:
    import yaml
except ImportError:
    sys.exit("[09] PyYAML missing — install with: sudo dnf install -y python3-pyyaml")

ROOT = Path(__file__).resolve().parent.parent

VALID_ARCHETYPES = {
    "young-prof-nightlife",
    "established-lakefront",
    "gentrifying-edge",
    "quiet-artsy",
    "industrial-cool",
    "latino-cultural",
    "lgbtq-anchor",
    "family-residential",
    "university-adjacent",
    "transit-bedroom",
    "diverse-bohemian",
    "insufficient-data",
}

# Pre-drafted records for known CAs. These are starting points — citations
# are TODO and the archetype assignments need verification against tier-1/2
# sources per docs/typology_sources.md.
DRAFTS = {
    "LAKE VIEW": {
        "archetype": "young-prof-nightlife",
        "secondary_archetype": "lgbtq-anchor",   # Boystown/Northalsted falls in Lake View
        "key_phrases": [
            "Wrigleyville sports/bar core",
            "Boystown / Northalsted LGBTQ commercial district",
            "high renter share, transient young-professional turnover",
            "Belmont/Halsted/Clark commercial corridors",
            "CTA Red+Brown line spine",
        ],
        "notable_for": (
            "Highest amenity density on the North Side outside the Loop; "
            "two distinct sub-areas — Wrigleyville's bar scene around the "
            "ballpark and Northalsted's LGBTQ commercial district. Heavy "
            "weekend foot traffic; weeknight calmer."
        ),
        "caveats": (
            "Gameday surge crowds and noise around Wrigley; turnover-driven "
            "rental market means weak neighbor continuity. Bar-density "
            "concentrates rather than spreads, so block-level vibe varies sharply."
        ),
        "citations": [
            {"tier": 2, "outlet": "Block Club Chicago", "url": "TODO — verify recent Lake View / Wrigleyville coverage", "date": "TODO"},
            {"tier": 2, "outlet": "Chicago Reader", "url": "TODO — verify Northalsted commercial-district pieces", "date": "TODO"},
        ],
        "draft_status": "needs-citation-verification",
    },
    "LOGAN SQUARE": {
        "archetype": "gentrifying-edge",
        "secondary_archetype": "latino-cultural",
        "key_phrases": [
            "former Puerto Rican enclave, mid-gentrification",
            "music venue corridor on Milwaukee Avenue",
            "rapid rent acceleration 2015-present",
            "Logan & Kedzie Boulevards architecture",
            "Blue Line spine to Loop",
        ],
        "notable_for": (
            "Active music + bar + restaurant scene anchored on Milwaukee "
            "between California and Western. Strong sense of place and "
            "architectural texture (boulevards, greystones). Long-time "
            "Latino cultural presence still visible in commercial corridors."
        ),
        "caveats": (
            "Generational and economic friction between long-time Latino "
            "residents and recent professional-class arrivals is real and "
            "ongoing. Rent trajectory is steep — what's affordable today "
            "may not be in 18 months. Bar-density means weekend street noise."
        ),
        "citations": [
            {"tier": 2, "outlet": "Block Club Chicago", "url": "TODO — verify gentrification + displacement coverage", "date": "TODO"},
            {"tier": 2, "outlet": "WBEZ", "url": "TODO — verify Logan Square housing pieces", "date": "TODO"},
        ],
        "draft_status": "needs-citation-verification",
    },
    "ROGERS PARK": {
        "archetype": "diverse-bohemian",
        "secondary_archetype": "university-adjacent",
        "key_phrases": [
            "Loyola University anchor on the north end",
            "highly racially + economically diverse",
            "lakefront access without Lincoln Park price tag",
            "Red Line spine; long commute to Loop",
            "Devon Avenue South Asian corridor (Edgewater border)",
        ],
        "notable_for": (
            "One of Chicago's most diverse Community Areas across race, "
            "class, and immigration status. Lakefront access, Loyola "
            "campus, and a long-running independent arts/literary scene "
            "(Heartland Cafe alumni, small theaters). Rents materially "
            "below comparable North Side neighborhoods."
        ),
        "caveats": (
            "Far north — Red Line commute to Loop is 45+ minutes. Block-"
            "level variation is significant; the lakefront blocks are "
            "very different from blocks west of Clark. Some longstanding "
            "concerns about petty property crime; check block-specific "
            "data before committing."
        ),
        "citations": [
            {"tier": 2, "outlet": "Block Club Chicago", "url": "TODO — verify Rogers Park neighborhood coverage", "date": "TODO"},
            {"tier": 2, "outlet": "Chicago Reader", "url": "TODO — verify arts/literary scene pieces", "date": "TODO"},
        ],
        "draft_status": "needs-citation-verification",
    },
    "LINCOLN PARK": {
        "archetype": "established-lakefront",
        "secondary_archetype": "family-residential",
        "key_phrases": [
            "DePaul University anchor; Lincoln Park Zoo + park",
            "high renter + high home-owner mix; expensive for both",
            "Halsted/Lincoln/Armitage commercial spine",
            "stable demographics, low turnover by Chicago standards",
            "Brown + Red Line CTA coverage",
        ],
        "notable_for": (
            "One of the most established, amenity-rich, and consistently "
            "expensive North Side neighborhoods. Significant park access "
            "(Lincoln Park itself, lakefront, conservatory), strong "
            "commercial corridors, and the highest concentration of "
            "professional-class adults in Chicago by ACS measure."
        ),
        "caveats": (
            "Premium pricing across rental and ownership; cost_headroom "
            "score will be low. DePaul presence means a student footprint "
            "in some pockets. The neighborhood's reputation skews older "
            "and more family-oriented than the actual demographic data — "
            "verify against current resident impressions if a particular "
            "block matters."
        ),
        "citations": [
            {"tier": 1, "outlet": "Marginal Revolution", "url": "TODO — Tyler Cowen has written about Lincoln Park", "date": "TODO"},
            {"tier": 2, "outlet": "Chicago Reader", "url": "TODO", "date": "TODO"},
        ],
        "draft_status": "needs-citation-verification",
    },
    "EDGEWATER": {
        "archetype": "established-lakefront",
        "secondary_archetype": "diverse-bohemian",
        "key_phrases": [
            "Andersonville commercial district (Clark/Foster)",
            "lakefront high-rises along Sheridan",
            "established LGBTQ-friendly demographic",
            "Bryn Mawr historic district",
            "Red Line + lakefront path access",
        ],
        "notable_for": (
            "Quieter and more residential than Lake View but still North "
            "Side lakefront. Andersonville's Clark Street commercial "
            "corridor is one of the strongest 'main street' destinations "
            "in Chicago for independent retail and restaurants. "
            "Lakefront access without Lincoln Park rent."
        ),
        "caveats": (
            "Andersonville energy is concentrated on Clark; blocks east "
            "of Broadway can feel sleepier than expected. North end (near "
            "Loyola) blends into Rogers Park; south end (near Uptown) has "
            "more variable street feel."
        ),
        "citations": [
            {"tier": 2, "outlet": "Block Club Chicago", "url": "TODO — Andersonville coverage", "date": "TODO"},
            {"tier": 2, "outlet": "Chicago Reader", "url": "TODO", "date": "TODO"},
        ],
        "draft_status": "needs-citation-verification",
    },
}


def empty_record():
    return {
        "archetype": "insufficient-data",
        "secondary_archetype": None,
        "key_phrases": [],
        "notable_for": "",
        "caveats": "",
        "citations": [],
        "draft_status": "empty",
    }


def main():
    survivors_path = ROOT / "data/processed/survivors.gpkg"
    if not survivors_path.exists():
        sys.exit("[09] survivors.gpkg missing — run 08_apply_filters.py first")

    survivors = gpd.read_file(survivors_path)
    print(f"[09] {len(survivors)} survivor CAs to characterize")

    out_yaml = ROOT / "data/processed/typology.yaml"

    if out_yaml.exists():
        # Subsequent run: validate + build typology.gpkg
        print(f"[09] typology.yaml exists; validating and joining to geometry")
        existing = yaml.safe_load(out_yaml.read_text())
        bad = [k for k, v in existing.items()
               if v.get("archetype") not in VALID_ARCHETYPES]
        if bad:
            print(f"[09] WARNING: invalid archetypes in {len(bad)} records:")
            for k in bad[:10]:
                print(f"       {k}: {existing[k].get('archetype')!r}")
        # Flatten and join
        rows = []
        for ca_name, rec in existing.items():
            rows.append({
                "community": ca_name,
                "archetype": rec.get("archetype"),
                "secondary_archetype": rec.get("secondary_archetype"),
                "notable_for": rec.get("notable_for"),
                "caveats": rec.get("caveats"),
                "draft_status": rec.get("draft_status"),
            })
        import pandas as pd
        flat = pd.DataFrame(rows)
        joined = survivors.merge(flat, on="community", how="left")
        out_gpkg = ROOT / "data/processed/typology.gpkg"
        joined.to_file(out_gpkg, driver="GPKG", layer="typology")
        print(f"[09] Wrote {out_gpkg}")
        return

    # First run: build YAML scaffold
    records = {}
    for _, r in survivors.sort_values("composite_score", ascending=False).iterrows():
        name = r["community"]
        if name in DRAFTS:
            records[name] = DRAFTS[name]
        else:
            records[name] = empty_record()

    # YAML dump with reasonable formatting
    out_yaml.write_text(yaml.safe_dump(records, sort_keys=False, allow_unicode=True, width=88))

    drafted = sum(1 for v in records.values() if v.get("draft_status") != "empty")
    empty = len(records) - drafted
    print(f"[09] Wrote typology.yaml")
    print(f"     Pre-drafted records: {drafted}")
    print(f"     Empty stubs (need fill): {empty}")
    print()
    print("Next steps:")
    print("  1. Review the 5 drafted records in data/processed/typology.yaml")
    print("  2. Verify citations (currently TODO) against docs/typology_sources.md")
    print("  3. Fill empty stubs for other survivor CAs")
    print("  4. Re-run this script to build typology.gpkg for QGIS symbology")


if __name__ == "__main__":
    main()
