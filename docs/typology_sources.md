# Typology source policy

The qualitative typology layer characterizes surviving Community Areas
by archetype rather than scoring them numerically. To keep the
characterization honest, sources are tiered: only fall back to a
lower tier when higher tiers don't have indexed material on the CA
in question.

## Source tiers

### Tier 1 — Considered commentary

- **Marginal Revolution** (`marginalrevolution.com`) — Tyler Cowen
  writes regularly about cities and specific neighborhoods,
  including Chicago. Use the site search; cite the post URL and date.

### Tier 2 — Local journalism

In rough order of breadth:

- **Block Club Chicago** (`blockclubchicago.org`) —
  neighborhood-by-neighborhood reporting, organized by CA
- **South Side Weekly** (`southsideweekly.com`) — South Side
  coverage including community history and demographic change
- **WBEZ Chicago** (`wbez.org`) — public radio reporting; deeper
  pieces on housing, transit, gentrification
- **Chicago Reader** (`chicagoreader.com`) — alt-weekly, strong on
  arts/nightlife/scene reporting
- **Chicago Independent Media Directory** — registry of independent
  outlets; consult to find smaller neighborhood-specific publications
  (e.g. *La Raza*, *Hyde Park Herald*, *South Side Drive*).
  TODO: confirm canonical URL and pull list of member outlets.

### Tier 3 — Crowdsourced

- **Reddit r/chicago** — neighborhood guides, "where should I move"
  threads, AMA-style posts. Useful for current resident perspective
  but heavy selection bias (younger, online, transient). Treat as
  one data point, not consensus.

### Tier 4 — Last resort

Only if Tiers 1–3 produce nothing usable for a given CA:

- Niche.com, StreetEasy/Apartments.com neighborhood blurbs,
  generic "best neighborhoods in Chicago" listicles.

Skip these by default — they're real-estate marketing optimized for
SEO, not journalism. They homogenize neighborhoods toward a buyer's
sales pitch and miss the texture that makes the typology useful.

## Per-CA record schema

For each surviving Community Area, build one record:

```yaml
ca_name: Logan Square
ca_number: 22
archetype: gentrifying-edge        # controlled vocabulary, see below
key_phrases:                       # 3-5 recurring descriptors across sources
  - "former Puerto Rican enclave"
  - "music venue corridor on Milwaukee"
  - "rapid rent acceleration 2015-present"
  - "Logan Boulevard architecture"
notable_for: >
  Active music + bar scene anchored on Milwaukee Ave; visible
  generational/economic friction between long-time Latino residents
  and recent professional-class arrivals.
caveats: >
  Rent trajectory steep — what's affordable today may not be in 18
  months. Bar-scene density means street noise on weekend nights.
sources:
  - { tier: 2, outlet: "Block Club Chicago", url: "...", date: "2025-09-12" }
  - { tier: 2, outlet: "WBEZ", url: "...", date: "2024-11-03" }
  - { tier: 3, outlet: "r/chicago", url: "...", date: "2026-02-18" }
```

## Controlled vocabulary for `archetype`

Draft — refine as records get built. The point is to have a small
finite set so the categorical map symbology stays legible.

| Archetype | Loose description |
|---|---|
| `young-prof-nightlife` | Bar/restaurant-dense, weekend energy, 25-35 cohort skew, transient |
| `established-lakefront` | High-amenity, older demographic, expensive, low turnover, lake access |
| `established-mainstreet` | Walkable commercial corridor, mid-amenity, mixed-age, NOT lakefront |
| `gentrifying-edge` | Active demographic/economic transition, mixed-income, friction |
| `quiet-artsy` | Lower density, creative-class anchored, studios/galleries, residential |
| `industrial-cool` | Former/active industrial, warehouse-to-residential conversions, edge-of-grid |
| `latino-cultural` | Strong Latino identity, established commercial corridors, multigenerational |
| `asian-cultural` | Strong Asian-American identity (Chinatown, Koreatown stretches, etc.) |
| `lgbtq-anchor` | Visible queer cultural infrastructure, density of community institutions |
| `family-residential` | Family-skewed demographics, schools/parks dominant, low nightlife |
| `university-adjacent` | Student population shaping commerce, seasonal density |
| `transit-bedroom` | Quiet residential with strong commuter rail access, low local commercial |
| `diverse-bohemian` | Highly diverse, mixed-class, layered immigrant + arts texture |

Some CAs will fit two archetypes — that's fine; the record can use
a primary + secondary. The map can color by primary.

## Method notes

- **Always cite.** Tier 1-2 sources get a URL + date. Tier 3 gets a
  URL + date with a note flag. No archetype assignment without at
  least two cited sources.
- **No fabrication.** If sources don't have material on a CA, mark
  the record `archetype: insufficient-data` rather than guess.
- **Date check.** Chicago neighborhoods change fast — Logan Square
  in 2015 ≠ 2026. Prefer sources < 3 years old; flag older ones.
- **Read against each other.** A single Block Club piece on a
  neighborhood gives one angle. The point of multiple sources is
  triangulation, not volume.
