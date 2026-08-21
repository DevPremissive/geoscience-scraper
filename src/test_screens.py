#!/usr/bin/env python3
"""
test_screens.py — tests for C4.3 (screening DSL) and C4.5 (audit affordances).

Repo style: no pytest. Pure tests always run; lake tests skip loudly.

The ones that matter:

  * `test_rerun_is_identical` — C4.3's acceptance is "re-run identically on the
    same snapshot". A screen that drifts between runs cannot be a monitor.
  * `test_no_implicit_latest` — Master §4. The compiled SQL must name the
    snapshot it read, so the emitted SQL is a complete record of the question.
  * `test_no_barren_is_commodity_conditional` — Master §2 doctrine. A hole
    barren for gold does not test a lithium thesis.
  * `test_decision_log_refuses_anonymity` — the log is the ground truth C6.6
    calibrates on. An anonymous row cannot be weighed against its author's
    later record.

Run:  python -m test_screens
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config as C
from screens import compile as SC
from screens import dsl
from screens import runner as RN

_fails: list[str] = []
_skips: list[str] = []
TARGET = "ON-892b968aac7ffff"


def check(cond, msg):
    if cond:
        print(f"    ok   {msg}")
    else:
        _fails.append(msg)
        print(f"    FAIL {msg}")


def skip(name, why):
    _skips.append(f"{name}: {why}")
    print(f"    skip {name} — {why}")


def lake_ready() -> bool:
    try:
        C.require_lake()
    except Exception:
        return False
    return (C.PROCESSED_DIR / "fabric" / "r7_ON.parquet").exists()


# ---------------------------------------------------------------------------
# Pure — the parser
# ---------------------------------------------------------------------------

def test_parses_every_predicate_kind():
    text = '''screen "all kinds" commodity orogenic_au juris ON {
      where:
        fault__km < 3
        cmmi_gravity__gravity_hgm__mean >= percentile(80)
        dist_to(fault) < 5
        count(nonbarren_holes, 1) >= 1
        tenure == open
        no_barren(commodity=Au, min_depth_m=50)
        heat >= percentile(90, juris)
        within(ON-B001965 buffer 5)
      rank by: heat
      limit: 25
    }'''
    scr = dsl.parse(text)
    kinds = [p.kind for p in scr.where]
    for k in ("feature", "dist_to", "count", "tenure", "no_barren", "heat",
              "within"):
        check(k in kinds, f"parses a {k} predicate")
    check(scr.name == "all kinds", "reads the screen name")
    check(scr.commodity == "orogenic_au", "reads the commodity")
    check(scr.juris == ["ON"], "reads the jurisdiction")
    check(scr.limit == 25, "reads the limit")
    check(scr.rank_by == "heat", "reads the rank expression")


def test_percentile_and_scope():
    scr = dsl.parse('screen "p" {\n where:\n  x >= percentile(90, terrane)\n}')
    v = scr.where[0].value
    check(isinstance(v, dsl.Percentile), "percentile parses to a Percentile")
    check(v.p == 90 and v.scope == "terrane", "percentile keeps p and scope")
    try:
        dsl.parse('screen "p" {\n where:\n  x >= percentile(90, galaxy)\n}')
        check(False, "an unknown percentile scope is rejected")
    except dsl.ScreenSyntaxError as e:
        check("scope" in str(e), "an unknown percentile scope is rejected by name")


def test_syntax_errors_name_the_line():
    bad = 'screen "x" {\n  where:\n    this is not a predicate\n}'
    try:
        dsl.parse(bad)
        check(False, "a bad predicate raises")
    except dsl.ScreenSyntaxError as e:
        check(e.line_no == 3, f"the error names the offending line ({e.line_no})")
        check("Expected" in str(e) or "expected" in str(e),
              "the error says what was expected")


def test_empty_where_is_refused():
    try:
        dsl.parse('screen "everything" {\n  limit: 10\n}')
        check(False, "a screen with no predicates is refused")
    except dsl.ScreenSyntaxError as e:
        check("whole fabric" in str(e),
              "refusing an empty screen explains that it would return everything")


def test_comments_and_blank_lines():
    scr = dsl.parse('# a note\nscreen "c" {\n\n  where:\n    # why\n'
                    '    fault__km < 3\n}')
    check(len(scr.where) == 1, "comments and blank lines are ignored")


# ---------------------------------------------------------------------------
# Lake — compilation and execution
# ---------------------------------------------------------------------------

def test_no_implicit_latest():
    """Master §4: the SQL must name the snapshot it read."""
    scr = dsl.parse('screen "s" {\n where:\n  fault__km < 3\n}')
    out = SC.compile_screen(scr)
    check(out["snapshot"] in out["sql"] or out["feature_path"] in out["sql"],
          "the compiled SQL names its feature snapshot")
    check("latest" not in out["sql"].lower(),
          "the compiled SQL contains no notion of 'latest'")
    check(bool(out["fabric_version"]) and bool(out["snapshot"]),
          "the compile result pins fabric version and snapshot")


def test_pivots_only_what_is_asked():
    """299 features pivoted to answer a question about one is the wrong shape."""
    scr = dsl.parse('screen "s" {\n where:\n  fault__km < 3\n}')
    out = SC.compile_screen(scr)
    check(out["features_used"] == ["fault__km"],
          f"only the mentioned feature is pivoted ({out['features_used']})")
    check(out["sql"].count("CASE WHEN feature") == 1,
          "exactly one pivot column is emitted")


def test_no_barren_is_commodity_conditional():
    """Master §2: barren for gold does not test a lithium thesis."""
    scr = dsl.parse('screen "s" {\n where:\n  no_barren(commodity=Au)\n}')
    out = SC.compile_screen(scr)
    note = " ".join(out["notes"])
    check("commodity-conditional" in note,
          "the compile result states that no_barren is commodity-conditional")
    check("Au" in note, "the note names the commodity actually excluded")
    from screens import helpers as H
    au = H.barren_cells("Au")
    everything = H.barren_cells(None)
    import pandas as pd
    n_au = len(pd.read_parquet(au))
    n_all = len(pd.read_parquet(everything))
    check(n_au <= n_all,
          f"an Au-conditional exclusion is no larger than an unconditional one "
          f"({n_au} <= {n_all})")


def test_tenure_reports_its_coverage():
    scr = dsl.parse('screen "s" {\n where:\n  tenure == open\n}')
    out = SC.compile_screen(scr)
    note = " ".join(out["notes"])
    check("AOI" in note,
          "the tenure predicate says which AOIs it resolved against — cells "
          "outside a computed AOI are excluded, and that must not be silent")


def test_expiring_says_it_is_a_watch_list():
    scr = dsl.parse('screen "s" {\n where:\n  tenure == expiring(30)\n}')
    try:
        out = SC.compile_screen(scr)
    except SC.ScreenCompileError as e:
        skip("test_expiring_says_it_is_a_watch_list", str(e))
        return
    note = " ".join(out["notes"]).lower()
    check("watch" in note and "stakeable" in note,
          "expiring() states that the source is a watch list, never 'stakeable'")


def test_unknown_point_class_is_named():
    scr = dsl.parse('screen "s" {\n where:\n  count(unicorns, 1) >= 1\n}')
    try:
        SC.compile_screen(scr)
        check(False, "an unknown point class raises")
    except SC.ScreenCompileError as e:
        check("unicorns" in str(e) and "have" in str(e),
              "an unknown point class is refused and the valid ones are listed")


def test_acceptance_screens():
    """PLAN_C4 4.3's three named screens, compiled, run and re-run."""
    res = RN.acceptance(quiet=True)
    got = res["screens"]
    check(len(got) >= 3, f"the library holds the three named screens ({len(got)})")
    for name, r in got.items():
        check(r.get("ran"), f"{name} compiles and runs"
              + ("" if r.get("ran") else f" — {r.get('error')}"))
    check(res["acceptance_met"], "C4.3 acceptance met")


def test_rerun_is_identical():
    scr_text = (LIB := RN.LIBRARY / "expiring_on_hot_ground.screen").read_text() \
        if (RN.LIBRARY / "expiring_on_hot_ground.screen").exists() else None
    if scr_text is None:
        skip("test_rerun_is_identical", "screen not in the library")
        return
    a = RN.run(scr_text, quiet=True)
    r = RN.rerun(a["screen"], a["run_id"], quiet=True)
    check(r["identical"], "a stored run replays to the same cell set")
    check(r["rows_before"] == r["rows_after"],
          f"row counts match ({r['rows_before']} vs {r['rows_after']})")


def test_run_is_self_describing():
    rr = RN.runs("expiring on hot ground")
    if not rr:
        skip("test_run_is_self_describing", "no runs on disk")
        return
    d = RN.RUNS_DIR / RN._safe("expiring on hot ground") / rr[-1]
    for f in ("screen.txt", "query.sql", "run.json", "results.parquet"):
        check((d / f).exists(), f"the run directory keeps {f}")
    meta = json.loads((d / "run.json").read_text())
    for k in ("fabric_version", "snapshot", "feature_path", "sql_sha256"):
        check(bool(meta.get(k)), f"the run records {k}")


# ---------------------------------------------------------------------------
# Lake — C4.5
# ---------------------------------------------------------------------------

def test_provenance_drawer():
    from dossier import audit as A
    try:
        pr = A.provenance(TARGET)
    except FileNotFoundError as e:
        skip("test_provenance_drawer", str(e))
        return
    check(pr["n_sources"] > 5,
          f"the drawer collects every source ({pr['n_sources']})")
    check(all(s["source_id"] for s in pr["sources"]),
          "every source is named")
    check(all(s["snapshot_date"] for s in pr["sources"]),
          "every source carries a snapshot date")
    check(bool(pr["pins"]["fabric_version"]) and bool(pr["pins"]["feature_snapshot"]),
          "the dossier pins its fabric version and feature snapshot")


def test_reproducibility_contract():
    from dossier import audit as A
    try:
        r = A.verify_reproducible(TARGET, quiet=True)
    except FileNotFoundError as e:
        skip("test_reproducibility_contract", str(e))
        return
    check(r.get("reproducible"),
          f"the dossier rebuilds identically from pinned inputs"
          + ("" if r.get("reproducible") else f" — drift: {r.get('drift')[:3]}"))
    check("generated_at" in r.get("excluded_from_comparison", []),
          "the generation timestamp is excluded, and the exclusion is named")


def test_decision_log_refuses_anonymity():
    """The log is C6.6's ground truth. An anonymous row cannot be weighed."""
    from dossier import audit as A
    for by in ("", "human", "system"):
        try:
            A.log_decision(TARGET, "approve", by)
            check(False, f"the log refuses {by!r} as a decider")
        except ValueError as e:
            check("person" in str(e).lower(),
                  f"the log refuses {by!r} and says why")
        except FileNotFoundError:
            skip("test_decision_log_refuses_anonymity", "no dossier on disk")
            return
    try:
        A.log_decision(TARGET, "maybe", "Devlen M")
        check(False, "the log refuses an invalid decision verb")
    except ValueError:
        check(True, "the log refuses an invalid decision verb")
    except FileNotFoundError:
        pass


def test_decision_log_is_empty_and_that_is_correct():
    from dossier import audit as A
    df = A.decisions()
    check(len(df) == 0,
          f"no decisions have been fabricated ({len(df)} rows). This log is "
          f"ground truth for calibration; a synthetic row would poison it.")


PURE = [test_parses_every_predicate_kind, test_percentile_and_scope,
        test_syntax_errors_name_the_line, test_empty_where_is_refused,
        test_comments_and_blank_lines]

LAKE = [test_no_implicit_latest, test_pivots_only_what_is_asked,
        test_no_barren_is_commodity_conditional, test_tenure_reports_its_coverage,
        test_expiring_says_it_is_a_watch_list, test_unknown_point_class_is_named,
        test_acceptance_screens, test_rerun_is_identical,
        test_run_is_self_describing, test_provenance_drawer,
        test_reproducibility_contract, test_decision_log_refuses_anonymity,
        test_decision_log_is_empty_and_that_is_correct]


def main():
    print("C4.3 / C4.5 tests\n")
    print("  pure")
    for fn in PURE:
        print(f"  {fn.__name__}")
        fn()
    if lake_ready():
        print("\n  lake")
        for fn in LAKE:
            print(f"  {fn.__name__}")
            try:
                fn()
            except Exception as e:                              # noqa: BLE001
                _fails.append(f"{fn.__name__} raised {type(e).__name__}: {e}")
                print(f"    FAIL {fn.__name__} raised {type(e).__name__}: {e}")
    else:
        print(f"\n  SKIPPING {len(LAKE)} lake tests — {C.LAKE_ROOT} not mounted")

    print("\n" + "-" * 64)
    if _skips:
        print(f"{len(_skips)} skipped:")
        for s in _skips:
            print("  " + s)
    if _fails:
        print(f"{len(_fails)} FAILURE(S)")
        for f in _fails:
            print("  " + f)
        sys.exit(1)
    print("all C4.3/C4.5 tests pass")


if __name__ == "__main__":
    main()
