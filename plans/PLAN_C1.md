# PLAN C1 — Land & Activity: Open Ground, Rules, Heat, Ownership, Criticality, Lapse Watch

**Read `MASTER_PLAN.md` §2–§4 first.** C1 is the system's primary signal source: it
answers "what ground is open, where is interest concentrating, who owns everything
adjacent, and which open cells does a specific neighbour eventually need."

**Depends on:** C0.5 (fabric), C0.7 (tenure events), C3.1 (daily snapshots — now Phase 0),
C3.6 (alerting, for 1.6).
**Feeds:** C6 (every deal-score term except geology), C4 (map layers, dossier sections),
C2 (heat as a covariate, staking backtest labels).
**Estimated effort:** ~2 weeks for **Ontario** (Phase 1), ~1 week per additional
jurisdiction thereafter (mostly rules-table verification and subtraction-layer sourcing).

> **Jurisdiction: Ontario.** Briefly switched to BC on 2026-08-13 and reverted the same day
> (audit F). The switch rested on the finding that Ontario carries no owner or dates — true
> of the OGSEarth KMZ, false of Ontario. The **MLAS operational bulk shapefiles** (C0.1)
> carry `HOLDER`, `ISSUE_DATE`, `ANNIVERSAR` and `CLAIM_DUE_` on all **401,594** cell claims,
> 100% populated, 1,403 distinct holders — plus **431,557 cancelled claims with termination
> dates back to 2018-04**, which is an unbiased staked-and-dropped history.
>
> **This section therefore depends on C0.1 registering the MLAS bundle.** Until it does,
> every ON computation here runs on the wrong product and half the claims (202,407 of
> 401,594).
>
> **Standing caveat for other jurisdictions:** BC and YT publish *current registries* — BC has
> `TERMINATION_DATE` on 23 of 42,285 rows — so dropped tenure is invisible there and their
> activity history begins with C3.1's daily snapshots. Ontario is the exception, not the rule.

**New package:** `src/land/` — `open_ground.py`, `rules.py`, `heat.py`,
`ownership_graph.py`, `criticality.py`, `lapse_watch.py`.

---

## 1.1 Open-ground computation (~2 days for ON)

Open ground = jurisdiction landmass minus the union of every encumbrance layer, computed
on r9 cells within AOIs and as dissolved polygons for map display.

Per-jurisdiction subtraction stack (initial; extend via `sources.py`, never hardcode):

| Juris | Subtraction layers (all already harvested unless noted) |
|---|---|
| **ON (Phase 1)** | active cell claims (**MLAS `Operational_Cell_Claims`, 401,594** — not the 202,407 OGSEarth KMZ), alienations (**MLAS `Operational_Alienations`, 16,812**), mining land tenure (22,940), non-mining land tenure (193,757), plans & permits (799), provincial parks & conservation reserves (**new source: Ontario GeoHub/LIO — add registry entry**) |
| BC | MTA tenures (42,285), parks/protected areas (**new source: BC Data Catalogue**), mineral/placer reserve sites (no-registration reserves — **new source**) |
| YT | quartz + placer claims, leases, groupings, crown grants (all harvested); note physical-staking regime in rules |
| NU | claims (34,411), leases (893), prospecting permits (2,600); surface-rights/IOL parcels (**new source: CIRNAC/NPC**) |
| SK | mineral disposition layers from SK ER FeatureServer |
| NS | mineral rights GDB (harvested) |
| NB | claims (86) + MPS sections (27,555) |

Implementation notes:
- Compute as a **cell-state table**, not just geometry: `land_state(cell_id r9, juris,
  state ∈ {open, claimed, alienated, withdrawn, park, unknown}, blocking_layer,
  as_of_snapshot)`. "unknown" is a real state — it marks cells where a known encumbrance
  type exists in the jurisdiction but its layer is not yet harvested; dossiers must show it.
- Refresh incrementally: only cells intersecting geometries that appear in `tenure_events`
  since the last run, plus a weekly full rebuild as a consistency check.
- **Acceptance:** for Ontario, pick 15 cells spanning all states and verify each
  against the MLAS Map Viewer by hand; unknown-state cells < 5% of the AOI.

## 1.2 Jurisdiction rules table (~2 days ON+BC, then ~0.5 day each)

Rules are **data, human-verified, never scraped-and-trusted**. File per jurisdiction:
`rules/<juris>.yaml`.

Schema (all fields required; `null` allowed only with a `notes` explanation):

```yaml
juris: ON
staking_method: online_map        # online_map | physical | hybrid
unit_name: cell claim
unit_area_ha: ~21                 # nominal; varies by latitude for cell-based systems
registration_cost: {amount: , currency: CAD, per: cell}
licence:
  required: true                  # prospector's licence prerequisite
  cost: {amount: , per: years}
  process:                        # how to obtain, renewal cycle
work_requirement:                 # schedule; many jurisdictions escalate by claim age
  - {years: "1-N", amount_per_unit: , unit: cell}
cash_in_lieu: {allowed: , rate: }
credit_banking: {allowed: , max_years_forward: , transferable_with_claim: }
transfer: {allowed: , fee: , process: }
expiry_mechanics:                 # anniversary dates, grace periods, forfeiture timing,
                                  # and exactly when lapsed ground reopens for staking
consultation_notes:               # duty-to-consult triggers relevant to early exploration
exempt_lands_notes:
verified_by: human
verified_date:
source_urls: []
```

Every numeric field is filled from the current regulation/fee schedule **by a human at
verification time** — the component plan deliberately ships the schema empty of numbers.
`rules.py` exposes `holding_schedule(juris, n_claims, years) -> cost table` and
`stakeable_now(juris) -> bool + missing prerequisites`, consumed by C6.5 and C4 dossiers.

**Acceptance:** ON, BC, SK, YT, NU files verified and dated; a unit test computes a
5-year holding schedule for a 10-claim ON block and a human confirms it against the
ministry fee page.

## 1.3 Heat detection (~2 days)

> **Runnable on Ontario today (audit F2).** Heat needs a trailing 8-quarter history, and the
> first-pass audit concluded none existed anywhere. That holds for BC/YT/NU, but **not for
> Ontario**: `Cancelled_Claim_Polygons` retains 431,557 records with `ISSUE_DATE` and
> `TERMINATIO` from 2018-04, giving ~33 quarters of staked-and-dropped events with no
> survivorship bias. Ontario heat is a real computation from day one.
>
> Two rules when using it:
> - **Split `STATUS` first.** Only the 303,138 `Cancelled` are drops; `Amalgamated` (102,966)
>   and `Merged` (2,379) are administrative and would read as false abandonment, inflating
>   `expiry_count` and corrupting `restake_latency_days`.
> - **Everywhere else, the original caveat stands.** BC's `ISSUE_DATE` describes only claims
>   still held (`TERMINATION_DATE` non-null on 23 of 42,285), so any non-Ontario
>   attribute-derived series omits dropped ground — carry `survivorship_biased = true` and
>   label it in the UI. The 8-quarter window degrades gracefully elsewhere: compute over
>   whatever whole quarters exist and record `quarters_available` on every row.

Input: `tenure_events` (C0.7). Output: `processed/heat.parquet` — one row per
(r7 cell, quarter).

Metrics per cell-quarter:
- `cells_staked`, `area_staked_ha`, `unique_new_owners` (first-time stakers in that cell's
  neighborhood), `net_area_change`, `expiry_count`, `restake_latency_days` (median days
  between an expiry and a subsequent staking overlapping it — fast restaking = contested
  ground), `conversion_count` (claim→lease, the strongest commitment signal).
- `heat_score` = weighted sum of per-metric robust z-scores against that cell's own
  trailing 8-quarter history *and* against the jurisdiction-quarter cross-section (both
  normalizations, kept separately — "hot vs itself" and "hot vs everywhere").
- Neighborhood smoothing: half-weight contribution from the 6 r7 neighbors (staking rushes
  spill across cell boundaries).

Doctrine reminder (Master §2): tenure history here is attention/momentum and a weak
positive prior. `heat.py` must also emit `ever_staked_count` per cell (how many distinct
historical staking episodes) — the weak-prior feature C2 may use, clearly named so the
backtest can exclude it.

**Acceptance (revised).** The news-verifiable-rush test is withdrawn for Phase 1 — it cannot
be met without history. Instead: (a) the metric implementation is unit-tested against
synthetic event streams with known answers; (b) top-20 hot cells from whatever real window
exists are human-reviewed and free of diff artifacts; (c) `quarters_available` and
`survivorship_biased` render correctly. **The original acceptance becomes a deferred check**
— re-run it against BC once ≥4 quarters of daily snapshots have accrued, and treat a
reproduced rush as the real validation of C1.3.

**Acceptance (restored for Ontario, audit F2).** The paragraph above still governs
jurisdictions without a cancellation register. For **Ontario the original test is back on**:
the heat series must reproduce at least one staking rush independently verifiable from
industry news. The 2018–2026 register makes this testable immediately — the 2025 spike
(73,400 terminations against 51,390 in 2024) is the obvious starting point. Top-20 hot cells
human-reviewed and free of artifacts, with `Amalgamated`/`Merged` correctly excluded from
drop counts.

## 1.4 Ownership & adjacency graph (~3 days)

Output: `processed/ownership.duckdb` with three tables:

> **Source (audit F1).** Build this on **Ontario**, from the MLAS `HOLDER` field — 100%
> populated across 401,594 claims, 1,403 distinct holders. Two things make it easier than the
> plan assumes:
> - `HOLDER` embeds ownership share as a `(NN)` prefix (`"(100) KENORLAND EXPLORATION LTD"`).
>   Parse into `owner_name` + `percent`; multi-holder claims appear as several prefixed
>   entries. That yields `PERCENT_OWNERSHIP` semantics for free.
> - The holder set is small and clean — 1,403 distinct strings across the province — so the
>   entity-resolution review queue below should be far under the 200-row target. Numbered
>   companies remain the residual problem for C6.2.
>
> `Cancelled_Claim_Polygons.HOLDER` (326,212 populated) additionally gives **historical**
> ownership, so `blocks.first_seen`/`last_change` and owner turnover are computable back to
> 2018 rather than starting today.
>
> For **BC**, the equivalent fields are `OWNER_NAME`, `CLIENT_NUMBER_ID`,
> `PERCENT_OWNERSHIP`, `NUMBER_OF_OWNERS` and `OWNERSHIP_TRANSFER_EVENT_COUNT` — prefer the
> registry-assigned `CLIENT_NUMBER_ID` over name matching there.

- `owners(owner_id, name_raw, name_normalized, entity_type_guess, sedar_issuer_id
  (nullable — joined by C6.2), aliases[])`. Entity resolution v1: uppercase, strip
  punctuation/legal suffixes (LTD, INC, CORP, LIMITED, LTÉE), collapse whitespace, exact
  match; emit a manual-review queue of near-matches (Jaro-Winkler > 0.92) rather than
  auto-merging. Numbered companies remain unresolved until C6.2 joins SEDAR+ profiles.
- `blocks(block_id, owner_id, juris, claim_ids[], geometry, area_ha, first_seen,
  last_change)` — connected components of same-owner claims touching or within 100 m.
- `adjacency(block_id_a, block_id_b, relation ∈ {touching, within_1km, within_5km},
  shared_boundary_m)`; plus `block_open_frontier(block_id, open_cell_id r9,
  frontier_bearing)` — every open cell on a block's perimeter with its bearing from the
  block centroid (consumed by 1.5).

Standard queries to ship as views: "open cells adjacent to blocks whose owner had a
`staked` or `conversion` event in the last 2 quarters"; "blocks whose owner holds ground
in ≥2 jurisdictions" (serious operators).

**Acceptance:** for three known Ontario juniors, the graph reproduces their property
outline recognizably vs. their corporate presentations; entity-resolution review queue
< 200 rows for ON.

## 1.5 Criticality scoring (~3 days)

For a (target neighbour block, open cell) pair, score how much the neighbour will
eventually need that cell. Output: `criticality(block_id, cell_id r9, score 0–1,
reason_codes[], trend_source, computed_at)`.

Three sub-scores, max-combined with reason codes (interpretability over elegance):

1. **On-trend (0–1):** project a trend corridor from the block onto open ground. Trend
   sources, in priority order, recorded in `trend_source`: (a) manual azimuth entered by
   the human in the dossier UI (C4) — always wins; (b) strike of mapped structures/faults
   from bedrock layers intersecting the block; (c) alignment axis of occurrence/drillhole
   points inside the block (PCA of point coordinates, only if n ≥ 5 and the first
   component explains > 70% of variance); (d) none → sub-score 0, not a guess. Corridor =
   block hull swept along ±azimuth, width = block's cross-trend extent; open cells inside
   score by distance decay from the block boundary.
2. **Gap-closing (0/1):** cell adjoins two or more blocks of the *same* owner
   (consolidation hole), or lies between the owner's block and that owner's other block
   within 5 km.
3. **Chokepoint (0–1):** fraction of the block's open frontier this cell (with its
   contiguous open neighbors) represents in the direction of the trend — a cell covering
   the only on-trend expansion direction scores ~1.

**Acceptance:** run against two real Ontario stories; a human agrees the top-5
critical cells per story are the ones a landman would want; every score explains itself via
reason codes with no opaque blending.

## 1.6 Lapse watch (~1.5 days) — *requires C3.6, now scheduled in Phase 0*

> **Premise restored (audit F1).** The original text asserts "ON claims carry due dates".
> The first-pass audit called that wrong; it was wrong about the KMZ, not about Ontario.
> MLAS `Operational_Cell_Claims` carries **`CLAIM_DUE_` and `ANNIVERSAR` on all 401,594
> claims**, so Ontario lapse watch is directly buildable — it is in fact the best-served
> jurisdiction, since it also has `HOLDER` for the alert payload. Separately, this section
> depends on C3.6 alerting, which the original roadmap mentioned only as "alerting maturity"
> in Phase 4 while placing C1.6 in Phase 1 — C3.6 is now Phase 0.

- Sources: expiry/anniversary attributes where the tenure layer publishes them (ON
  `CLAIM_DUE_` + `ANNIVERSAR`, BC `GOOD_TO_DATE`, YT `EXPIRY_DATE` int64 epoch-ms, NU
  `ANNIV_DT`) plus observed `expired` events for jurisdictions without published dates.
- Watch rules (config, not code): alert when a claim with expiry ≤ N days (default 30)
  intersects (a) a top-decile heat cell, (b) any cell with criticality ≥ 0.5 for a watched
  block, or (c) a saved C4 screen's result set. Alert payload: claim id, expiry date,
  owner, overlap reason, link to auto-generated draft dossier.
- Post-expiry confirmation: lapse ≠ instantly open in every jurisdiction (grace periods,
  pending-forfeiture states — per `rules/<juris>.yaml` `expiry_mechanics`); the alert
  pipeline must re-check `land_state` after the rules-defined reopening delay before
  flagging ground as stakeable.
- Delivery via C3.6 alerting (ntfy/email).

**Acceptance:** two weeks of live operation on Ontario produces alerts whose
claimed-expiry dates match the provincial registry, with zero "stakeable" flags on ground
still in grace period.

## Known blind spots (documented, not faked)

QC (GESTIM boundaries non-public — MRNF bulk-licensing decision is a Master-plan Phase-1
action), MB (MapGallery only), NL (GeoFiles map only), AB (AER, no public claims API).
`land_state` for these jurisdictions returns `unknown` everywhere rather than pretending.

**~~Ontario ownership and expiry~~ — not a blind spot (resolved 2026-08-13, audit F1).**
Briefly recorded as one when only the OGSEarth KMZ had been examined. The MLAS operational
bundle publishes `HOLDER`, `ISSUE_DATE`, `ANNIVERSAR` and `CLAIM_DUE_` openly and daily, so
Ontario is the *best*-served jurisdiction for this component, not the worst. The dead ends —
LIO ArcGIS `MLAS` 403, the claim-abstract SPA, the access-restricted `data.ontario.ca`
record — are catalogued in audit F4 so the search is not repeated.

**The real blind spot to watch is temporal coverage elsewhere.** Ontario's cancellation
register starts 2018-04 (map-staking conversion); pre-2018 legacy claims sit in the separate
administrative bundle. BC, YT and NU have no equivalent register at all, so their
`tenure_events` genuinely begin with C3.1's first snapshot.

## Handoff notes for detailed planning

Delegated: exact heat-score weights (start uniform, tune against the news-verifiable rush
in acceptance), corridor decay function, entity-resolution threshold. Settled (do not
reopen): cell-state table over pure geometry, rules as human-verified YAML with numbers
filled at verification time, criticality as three interpretable sub-scores with reason
codes, tenure history never used as a negative.
