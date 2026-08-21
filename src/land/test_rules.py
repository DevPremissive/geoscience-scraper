#!/usr/bin/env python3
"""
test_rules.py — unit tests for the C1.2 holding-schedule arithmetic.

The acceptance criterion is *"a unit test computes a 5-year holding schedule for
a 10-claim ON block and a human confirms it against the ministry fee page."*
Those are two different checks and this file only does the first:

  * the **arithmetic** is tested here against a fixture with invented numbers,
    so band lookup, escalation and licence recurrence are proven correct;
  * the **numbers** are a human's job, and `test_real_ontario_refuses_until_verified`
    asserts the system refuses to produce a schedule until that happens.

Inventing plausible Ontario fees to make a green test would defeat the point of
the component: it would produce a cost model that looks authoritative and is
unsourced.

Run:  python -m land.test_rules
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from land import rules as R

#: Invented numbers, chosen to make each rule visible in the result.
FIXTURE = {
    "juris": "TEST",
    "registration_cost": {"amount": 10.0, "currency": "CAD", "per": "cell"},
    "licence": {"required": True, "cost": {"amount": 25.0, "per": 3}},
    "work_requirement": [
        {"years": "1-2", "amount_per_unit": 100.0, "unit": "cell"},
        {"years": "3-5", "amount_per_unit": 200.0, "unit": "cell"},
        {"years": "6-N", "amount_per_unit": 400.0, "unit": "cell"},
    ],
}

_fails = []


def check(name, got, want):
    if got != want:
        _fails.append(f"{name}: got {got!r}, want {want!r}")
        print(f"  FAIL {name}: got {got!r}, want {want!r}")
    else:
        print(f"  ok   {name}")


def test_bands_escalate():
    s = R.compute_schedule(FIXTURE, n_claims=10, years=5)
    per_year = [r["work_per_unit"] for r in s["work_by_year"]]
    check("work bands by year", per_year, [100.0, 100.0, 200.0, 200.0, 200.0])
    # 10 claims: 2 yrs x 100 x 10 = 2,000; 3 yrs x 200 x 10 = 6,000
    check("work total", s["work_total"], 8000.0)


def test_open_ended_band():
    s = R.compute_schedule(FIXTURE, n_claims=1, years=7)
    check("6-N band applies at year 7",
          s["work_by_year"][6]["work_per_unit"], 400.0)


def test_registration_scales_with_claims():
    s = R.compute_schedule(FIXTURE, n_claims=10, years=5)
    check("registration", s["registration_total"], 100.0)


def test_licence_recurs():
    # A 3-year licence over a 5-year horizon must be bought twice.
    s = R.compute_schedule(FIXTURE, n_claims=10, years=5)
    check("licence recurrence", s["licence_total"], 50.0)
    s1 = R.compute_schedule(FIXTURE, n_claims=10, years=3)
    check("licence once at exactly 3 years", s1["licence_total"], 25.0)


def test_grand_total():
    s = R.compute_schedule(FIXTURE, n_claims=10, years=5)
    check("grand total", s["grand_total"], 100.0 + 50.0 + 8000.0)
    check("complete flag", s["complete"], True)


def test_missing_numbers_do_not_become_zero():
    """The failure mode this component exists to prevent."""
    empty = {"juris": "EMPTY", "registration_cost": {"amount": None},
             "licence": {"cost": {}}, "work_requirement": [
                 {"years": "1-N", "amount_per_unit": None}]}
    s = R.compute_schedule(empty, n_claims=10, years=5)
    check("no silent zero for registration", s["registration_total"], None)
    check("no silent zero for grand total", s["grand_total"], None)
    check("incomplete is flagged", s["complete"], False)


def test_partial_is_not_reported_as_a_total():
    """A total that omits unfilled components reads as an answer. It must not."""
    partial = {"juris": "PARTIAL",
               "registration_cost": {"amount": None},
               "licence": {"cost": {}},
               "work_requirement": [{"years": "1-N", "amount_per_unit": 400.0}]}
    s = R.compute_schedule(partial, n_claims=10, years=5)
    check("work is known", s["work_total"], 20000.0)
    check("grand_total withheld while incomplete", s["grand_total"], None)
    check("partial exposed separately", s["known_subtotal"], 20000.0)
    check("missing components named", s["unknown_components"],
          ["registration", "licence"])


def test_real_ontario_after_signing():
    """Ontario was signed on 2026-08-21, so this asserts the state that
    replaced the refusal, not the refusal.

    Two things must hold at once now. The arithmetic must run against the
    sourced fees — that is the half of C1.2's acceptance a human sign-off
    unlocks. And the gate must STILL be shut, because signing attested to the
    fee schedule and the forfeiture timing, not to duty-to-consult or exempt
    lands, which are still null. A signature is not a blanket clearance."""
    rules = R.load("ON")
    check("ON is signed", R.is_verified(rules), True)
    check("signed by a person, not the placeholder",
          rules.get("verified_by") not in (None, "", "human"), True)

    # The arithmetic, against the four fees vis accepted on 2026-08-17:
    #   registration $50/cell x 10                        =    500
    #   assessment work $400/cell/yr x 10 cells x 5 years =  20,000
    #   prospector's licence $40 per 5-year term          =     40
    sched = R.holding_schedule("ON", 10, 5)
    check("5-year hold of 10 cells computes", sched["grand_total"], 20540.0)
    check("in Canadian dollars", sched["currency"], "CAD")

    # Open as of 2026-08-21 — but opened by recorded ASSUMPTIONS, not citations,
    # and the record of which ones must survive. A gate that clears without
    # leaving evidence of what cleared it is worse than one that stays shut.
    ok, missing = R.stakeable_now("ON")
    check("ON is cleared for a staking decision", ok, True)
    check("nothing left unexplained", missing, [])
    scope = rules.get("assumptions_scope") or []
    for field in ("consultation_notes", "exempt_lands_notes", "transfer.fee"):
        check(f"{field} cleared by a recorded assumption", field in scope, True)
    check("the assumptions name who accepted them",
          bool(rules.get("assumptions_accepted_by")), True)
    rn = rules.get("notes") or {}
    for field in scope:
        check(f"{field} note explains the assumption",
              len((rn.get(field) or "").strip()) > 80, True)
    check("the transfer fee is still null, not invented",
          rules["transfer"]["fee"], None)

    # C1.6's numeric gate, sourced from the MNDM relief-from-forfeiture policy.
    mech = rules["expiry_mechanics"]
    check("no grace period after the due date", mech["grace_period_days"], 0)
    check("ground reopens 3 days after the DUE DATE, not 1",
          mech["reopening_delay_days"], 3)
    check("relief from forfeiture is recorded as a residual risk",
          bool(mech.get("relief_from_forfeiture", {}).get("exists")), True)


def main():
    for fn in [test_bands_escalate, test_open_ended_band,
               test_registration_scales_with_claims, test_licence_recurs,
               test_grand_total, test_missing_numbers_do_not_become_zero,
               test_partial_is_not_reported_as_a_total,
               test_real_ontario_after_signing]:
        print(f"\n{fn.__name__}")
        fn()
    print("\n" + ("-" * 60))
    if _fails:
        print(f"{len(_fails)} FAILURE(S)")
        for f in _fails:
            print("  " + f)
        sys.exit(1)
    print("all arithmetic tests pass")
    print("C1.2 acceptance: BOTH halves done. Ontario signed by Devlen M on\n"
          "2026-08-21, and stakeable_now('ON') is now True.\n\n"
          "IT CLEARED ON ASSUMPTIONS, NOT CITATIONS — six of them, listed in\n"
          "rules/ON.yaml under `assumptions_scope` and reprinted in every dossier.\n"
          "The two worth checking first, in this order:\n"
          "  credit_banking.transferable_with_claim — feeds C6.5's 'runway a buyer\n"
          "      inherits', so an error here lands straight in a price.\n"
          "  transfer.fee — still null ON PURPOSE. Ontario schedules no\n"
          "      claim-transfer fee at all (audit H). Treat as UNKNOWN, never as\n"
          "      zero: assuming zero understates cost, which is the direction\n"
          "      that loses money.")


if __name__ == "__main__":
    main()
