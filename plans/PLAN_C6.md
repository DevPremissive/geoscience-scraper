# PLAN C6 — Economics & Market: Comps, Buyers, Momentum, Valuation, Holding Costs, Deal Score

**Read `MASTER_PLAN.md` §2–§4 first.** C6 produces the final ranking. The system ranks
**deals, not rocks**: `deal_score = f(expected sale value, buyer probability & capacity,
criticality, geology, acquisition + holding cost, assessment-credit value)`. Prospectivity
(C2) supplies exactly one term. Every C6 output surfaces in the dossier (C4 section 8)
with its decomposition visible — the human must be able to disagree with any single term.

**Depends on:** C1 (ownership graph, heat, rules, criticality), C2 (calibrated scores,
negatives/nonbarren), C3.5 (market acquisition), C5 (historical results for valuation).
**Feeds:** C4 (dossier economics + buyer sections), C3.5 (issuer tracking list), C1.6
(watched-block definitions).
**Estimated effort:** 6.1 ≈ 1 week build + ongoing accumulation · 6.2 ≈ 1 week ·
6.3 ≈ 2 days · 6.4 ≈ 3 days (v1 heuristic) · 6.5 ≈ 2 days · 6.6 ≈ 2 days.
**New package:** `src/market/` — `comps.py`, `buyers.py`, `momentum.py`, `valuation.py`,
`holding.py`, `deal_score.py`.

---

## 6.1 Comps database (~1 week build; the pricing ground truth)

`market/comps.parquet` — every discoverable early-stage property transaction:

```
deal_id, announced_date, closed_date, buyer_issuer_id, seller (free text + resolved id
where possible), property_name, juris, commodity[], stage ∈ {grassroots, early_expl,
drill_ready, resource}, structure ∈ {purchase, option, option_jv, staking_agreement},
terms: {cash_schedule[], share_schedule[] (count + implied value at announcement),
        work_commitments[], nsr_pct, nsr_buyback},
area_ha, adjacency_context ∈ {adjacent_to_buyer, same_belt, standalone, unknown},
historical_work_present (bool — did the seller's package include prior data/credits),
source_doc (SEDAR+ filing path or news item), extraction_confidence, review_status
```

**Population pipeline (`comps.py`):**
1. Candidate detection over C3.5 raw acquisitions: material change reports + news items
   matching acquisition/option language for tracked issuers **plus** a belt-keyword
   sweep so deals by untracked juniors in watched belts are caught.
2. LLM extraction (local `:8082`, JSON-schema-constrained) of the terms block from the
   filing text; `extraction_confidence` recorded.
3. **Human QA:** every comp is human-reviewed before `review_status = confirmed`;
   only confirmed comps enter valuation (6.4). Deal terms are too consequential and too
   idiosyncratically worded for auto-trust.
4. **Backfill:** 3–5 years of history for the belts touched by Phase-1 targets first,
   then widen. Backfill is the long pole — schedule it as a background queue, not a
   blocker.
5. Normalization views: implied total consideration at announcement; consideration per
   hectare; cash-vs-paper ratio; time-to-first-payment — the quantities 6.4 regresses on.

**Acceptance:** ≥30 confirmed comps spanning ≥2 jurisdictions and ≥2 commodities;
independent re-extraction of 5 comps by a human matches the stored terms; distribution
sanity plots (consideration/ha by stage) reviewed.

## 6.2 Buyer graph (~1 week)

Per potential buyer — primarily owners of blocks adjacent to candidate ground —
`market/buyers.parquet` + document store:

```
issuer_id, names/aliases (feeds C1.4 entity resolution), exchange, tickers,
treasury_estimate {cash, as_of, source_filing}, financings_24mo[] {date, gross, type},
burn_estimate, drill_program_status ∈ {announced, active, completed, none_known},
acquisition_history[] (join from comps where buyer), consolidator_flag
(bool — has this issuer historically bought neighbours?), stated_plans_summary,
news_cadence, profile_as_of
```

**Pipeline (`buyers.py`):**
1. Seed list: owners from the C1.4 ownership graph within 5 km of any watched or
   screened target → resolve to SEDAR+ issuer profiles (this join also closes C1.4's
   numbered-company gaps — write the resolved ids back to `owners`).
2. Write the tracking list to `market/issuers.parquet` (C3.5 consumes it to scope
   pulls); tracked set grows with the watchlist, never the whole exchange.
3. Extraction from filings: treasury from most recent financials; financings from
   offering documents; program status and stated plans from news/MD&A — LLM-assisted,
   with `source_filing` on every figure.
4. Derived scores: `buyer_capacity` (can they pay a Phase-1-scale consideration without
   a raise — treasury vs typical comp terms), `buyer_propensity` (consolidator_flag +
   program activity + adjacency), `buyer_timing` (an announced drill program on the
   adjacent block is the single strongest urgency signal — a buyer drilling toward your
   cells has a deadline; a dormant one can wait you out).

**Acceptance:** complete profiles for the 2–3 Phase-1 Ontario stories; treasury figures
match the filings they cite; capacity/propensity/timing render in a dossier with their
sources.

## 6.3 Hot-area momentum overlay (~2 days)

`momentum.py` composes, per r7 cell/belt: C1.3 heat × market overlays — financings
closed by issuers holding ground in the belt (12-mo sum), drill programs announced,
and a commodity-regime index per system (a simple price-trend state {rising, flat,
falling} from a public price series; coarse is fine — it's a covariate, not a forecast).

**Entry-window flag:** heat rising quarter-over-quarter while staking velocity remains
below its prior local peak — interest building before the rush crests. This flag
prioritizes the screening queue (C4.3) and lapse-watch attention (C1.6); it never
bypasses review.

**Acceptance:** momentum surface for Ontario renders in the viewer; the entry-window
flag, applied retroactively over backfilled events, flags at least one belt *before*
its news-verifiable staking rush (the same rush used in C1.3 acceptance).

## 6.4 Claim valuation (~3 days v1; fitted model deferred until data justifies)

**v1 — comps-anchored heuristic (`valuation.py`), fully decomposed:**
- `base` = median confirmed-comp consideration/ha for (juris, commodity, stage), with
  fallback widening (drop juris, then stage) when the cell count < 5 — the fallback
  path is recorded on the output.
- Multipliers, each with a documented range and its evidence attached:
  criticality tier (C1.5), `buyer_count` (>1 natural buyer lifts; =1 discounts),
  `buyer_timing` (active adjacent program lifts), heat percentile,
  historical-work presence (non-barren intercepts from C5.2 lift strongly; transferable
  credits per 6.5 add their face value), negative-veto flags (commodity-conditional
  discount, per doctrine).
- **Output: two numbers, never blended silently** — `geological_value` (what the rocks
  and data support) and `strategic_value` (land-position premium from criticality/
  consolidation), plus a combined range. Barren-drilled ground on a consolidation gap
  can carry near-zero geological value and real strategic value; the dossier must show
  both so the sales narrative is chosen deliberately.
- **v2 — fitted model:** gradient boosting on confirmed comps once n ≳ 150; until then
  the heuristic with visible knobs beats an overfit regression. Revisit at each phase
  gate.

**Acceptance:** valuation decompositions for 5 Phase-1 dossier targets reviewed by
human; every multiplier traces to a stored input; the fallback-widening path triggers
and reports correctly on a thin-comps case.

## 6.5 Holding-cost & assessment-credit calculator (~2 days)

`holding.py`, consuming `rules/<juris>.yaml` (C1.2):
- `holding_schedule(claims, years)` — year-by-year obligation per claim block:
  registration, work requirement (with escalation schedules), cash-in-lieu option,
  renewal fees; totals and per-year cash need.
- `credit_position(claims)` — banked assessment credits where the jurisdiction publishes
  balances (**data question per jurisdiction — resolve during C1.2 verification**:
  which registries expose credit balances on claim records vs require account access);
  where visible: transferable balance, years-of-obligation it covers, i.e. the runway a
  buyer inherits. Banked credits are the cleanest quantifiable form of
  "value of historical information on the claims" and are added at face value in 6.4.
- Seller-side view: my cost to carry a target for 1/2/3 years while marketing it — the
  denominator of every deal decision and the input to walk-away timing.

**Acceptance:** ON and BC schedules verified against ministry fee pages by human; a
dossier shows buyer-inherited runway for a target with historical work.

## 6.6 Deal score (~2 days)

`deal_score.py` — the ranking that orders the human review queue:

```
expected_net = valuation.combined_mid × p_sale − (acquisition_cost + carry_cost(T))
p_sale       = g(buyer_propensity, buyer_capacity, buyer_timing, buyer_count,
                 momentum.entry_window)
deal_score   = expected_net risk-adjusted by: valuation range width,
               land_state "unknown" fraction (C1.1), extraction/QA confidence flags
```

- v1: transparent multiplicative scorecard — every term visible in dossier section 8
  with a sensitivity line ("score drops below threshold if p_sale < X or carry
  exceeds Y years").
- **Calibration loop:** the C4.5 decision log (approve/reject + eventual outcomes:
  staked? sold? terms?) is the ground truth that later fits `g` and the risk
  adjustments. Design the log fields now; fit later.
- Explicit non-goals (per Master §8 deferred list): portfolio optimization across held
  claims, renewal automation, kill-discipline tooling — not built until claims are held.

**Acceptance:** Phase-1 dossier set ranked; the ordering survives a human sanity
review; changing any single visible term moves the score as documented.

## Handoff notes for detailed planning

Delegated: belt-keyword list construction, commodity price series source, exact
multiplier ranges (set from the first 30 confirmed comps, not from priors), scorecard
weights. Settled (do not reopen): human QA before comps confirmation, two-value
valuation (geological vs strategic, never silently blended), tracked-issuer scoping
(never whole-exchange), buyer_timing as the urgency term, decision-log-driven
calibration, deferred portfolio tooling.
