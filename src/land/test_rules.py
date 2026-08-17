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


def test_real_ontario_refuses_until_verified():
    ok, missing = R.stakeable_now("ON")
    check("ON not stakeable while unverified", ok, False)
    check("sign-off named as missing",
          any("verified_by" in m for m in missing), True)
    try:
        R.holding_schedule("ON", 10, 5)
        check("holding_schedule refuses", "returned a value", "raised ValueError")
    except ValueError:
        check("holding_schedule refuses", "raised ValueError", "raised ValueError")


def main():
    for fn in [test_bands_escalate, test_open_ended_band,
               test_registration_scales_with_claims, test_licence_recurs,
               test_grand_total, test_missing_numbers_do_not_become_zero,
               test_real_ontario_refuses_until_verified]:
        print(f"\n{fn.__name__}")
        fn()
    print("\n" + ("-" * 60))
    if _fails:
        print(f"{len(_fails)} FAILURE(S)")
        for f in _fails:
            print("  " + f)
        sys.exit(1)
    print("all arithmetic tests pass")
    print("PENDING HUMAN STEP: fill rules/ON.yaml from the ministry fee page, "
          "sign it, then run\n  python -m land.rules --schedule ON --claims 10 --years 5\n"
          "and confirm the 5-year total against that page. That is the other half "
          "of C1.2's acceptance.")


if __name__ == "__main__":
    main()
