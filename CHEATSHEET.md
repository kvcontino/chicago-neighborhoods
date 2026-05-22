# Chicago Relocation Cheat Sheet

Quick reference distilled from the full analysis. Keep on phone for in-person scouting.

---

## The shortlist (the 14 that survived all filters)

Two coherent paths through this list:

**Path A — neighborhood-character** (Center Square heritage)

| CA | Sub-area to walk | Why |
|---|---|---|
| West Town (#5) | Ukrainian Village, Bucktown | Closest Center Square spirit. Damen + Chicago + Division corridors. |
| North Center (#8) | Roscoe Village | Quietest, lowest velocity, top-3 quality. Closest Center Square *pace*. |
| Edgewater (#13) | Andersonville (Clark St) | Out of model top 10 but Clark St ≈ Albany's Lark St. |
| Lincoln Park (#12) | Old Town (Wells St) | Out of model top 10 but historic walkable brownstones. |

**Path B — modern high-rise / Vermont contrast**

| CA | Sub-area | Why |
|---|---|---|
| Near South Side (#4) | South Loop | Newest construction. Best value for high-rise. Panoramic views. |
| Near West Side (#6) | West Loop / Fulton Market | Work-activity days + evening dining + not-too-touristy. |
| Near North Side (#2) | **Streeterville** | Best lake views in Chicago. Quieter than River North. |
| Near North Side (#2) | River North | More nightlife, more tourists. Try only if Streeterville feels too quiet. |
| Loop (#1) | (whole CA is the sub-area) | Highest walk + transit by construction, but former office district turning residential. |

---

## Streeterville quick-take (you said you like it)

- **Geography:** lakefront wedge east of Michigan Ave, between Chicago River and Oak St. Northwestern Memorial Hospital is the anchor.
- **What works for you:** lake views, modern high-rise stock (lots of post-2000 buildings), hospital-driven daytime activity, walkable to Mag Mile evening life, quieter than River North.
- **What to watch for:** Navy Pier tourist density (Saturdays brutal in summer), Mag Mile chain-heavy commerce (not the indie texture of Path A), Lake Shore Park is the only real "neighborhood" green space.
- **Transit:** Red Line at Grand (5-10 min walk west) — not as on-top-of-it as living in River North. Lots of bus lines on Michigan/Columbus.
- **Grocery:** Whole Foods (Streeterville location), Mariano's near Mag Mile.
- **Cat-friendly:** Most modern high-rises accept cats with deposit + $25-50/mo. Several vets in the area.
- **Rent floor for a decent 2BR with lake view:** ~$3,500-4,500/mo from a quick mental anchor. Below that you're either inland-facing or older building.

---

## Apartment search filters (apartments.com / zumper / streeteasy)

For the WFH + cats + modern preference:

| Filter | Value | Why |
|---|---|---|
| Min beds | 2 | WFH office + bedroom; ACS data shows your CAs have 30-50% 2BR+ stock |
| Construction year | 2000+ (where filterable) | HVAC, soundproofing, finishes |
| Building type | High-rise (Path B) or 2-4 unit / walk-up (Path A) | Match the path |
| Pet policy | Cats allowed | Universal-ish in modern buildings, variable in walk-ups |
| Floor | 5+ for views; 10+ for skyline (Path B) | Below 5 is just an apartment, above 10 starts being a *view* |
| In-unit laundry | Yes | Standard in modern; old walk-ups often share basement laundry |

Skip filtering on rent ceiling — you can afford anywhere in your survivor list.

---

## Visit checklist (per CA or sub-area)

Spend ~3 hours per visit, ideally:

1. **Wednesday afternoon (work-day baseline)** — what does this place feel like on a normal day?
2. **Friday evening 7-10pm** — what does it feel like at peak social hours?
3. **Saturday morning** — what do residents (not tourists) actually do on weekends?

Things to do during each visit:

- Walk the main commercial corridor end-to-end.
- Buy coffee. Notice who else is there. Talk to the barista.
- Step into 1-2 buildings if there are open-houses or lobby visibility.
- Walk 2-3 residential blocks. Note: tree cover, building age, foot traffic, parked-car density.
- Eat a meal. Notice volume, music, age skew of patrons.
- Find the grocery store closest to where you'd live. Note distance + selection.
- Find the L stop. Walk to it. Time it.

Take a note on your phone: **would you bring a date here? a parent? yourself on a bad day?**

---

## Red flags to watch for

| Red flag | Where it shows up | What it means |
|---|---|---|
| Half-empty storefronts on the main corridor | Anywhere | Commercial decline — could be turnaround or could be terminal |
| For-rent signs every 50 feet | Any sub-area | High turnover; transient population |
| Strong smell of weed at 11am | Anywhere | Probably fine but confirms cultural signal you're entering |
| Multiple "luxury condo" billboards | Logan Square, Avondale, Pilsen | Active gentrification pressure — you'd be participating |
| Loud HVAC noise in unit during showing | Any high-rise | Building has aging mechanical systems |
| Visible water staining on ceiling | Any building | Past leak — check landlord history |
| Echoing sound in hallway | Older walk-ups | Bad soundproofing — you'll hear everything |
| Cigarette smell in hallway | Any | Building tolerates smoking; you'll smell it |

---

## Decision heuristics

When you can't decide between two finalists:

1. **Imagine yourself there on the worst day of your year.** Which one would you prefer to be in?
2. **Imagine yourself there on a Tuesday in February at 4pm in the dark.** Which feels less depressing?
3. **Picture explaining the choice to a friend.** Which sentence comes out without strain?
4. **Where is your hypothetical first Chicago friend most likely to live?** Pick that one.
5. **First lease is one year.** Bias toward something you can fully exit if it's wrong, over something you'd want forever.

---

## Files to come back to in the repo

| File | Use |
|---|---|
| `output/ranked_shortlist.csv` | Sort by any column in a spreadsheet to test "what if I weighted X more" |
| `output/chicago_decision_map.pdf` | Print and put on wall during search |
| `output/chicago_small_multiples.pdf` | Tradeoff visibility — which CAs are good at which axes |
| `data/processed/typology.yaml` | Per-CA archetype + caveats + citations |
| `scripts/08_apply_filters.py` | The single tunable file — adjust weights or thresholds and re-run pipeline |

To re-run the analysis after any change to script 08:

```bash
cd ~/2_projects/mapping/chicago_neighborhoods
python3 scripts/08_apply_filters.py && python3 scripts/09_build_typology.py && \
python3 scripts/10_build_project.py && python3 scripts/11_export_print_map.py && \
python3 scripts/12_build_editorial_map.py && python3 scripts/13_build_small_multiples.py && \
python3 scripts/14_build_decision_map.py
```

~45 seconds end-to-end.
