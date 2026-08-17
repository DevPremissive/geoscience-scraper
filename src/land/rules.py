#!/usr/bin/env python3
"""
rules.py — jurisdiction staking rules, as human-verified data (C1.2).

Rules live in `rules/<juris>.yaml` and are **data, human-verified, never
scraped-and-trusted**. MASTER §8 is explicit: staking prerequisites, consultation
obligations and exempt lands must be human-verified before first staking in a
jurisdiction, and no scraper substitutes for it. So this module ships the schema
and the arithmetic, and deliberately ships **no numbers**.

The consequence is a fail-safe design rather than a convenience one:

  * `stakeable_now()` returns **False** for any jurisdiction whose file is not
    signed off, and names what is missing. An unverified jurisdiction is not
    "probably fine" — it is unknown, and unknown must not read as permission.
  * `holding_schedule()` **raises** on an unverified file rather than returning
    zeros. A cost model that silently answers $0 because nobody filled in the
    fee schedule is worse than one that refuses to answer.

Consumed by C6.5 (holding cost) and C4 dossiers.

Usage:
    python -m land.rules --status
    python -m land.rules --schedule ON --claims 10 --years 5
    python -m land.rules --validate
"""
from __future__ import annotations
import argparse, sys
import datetime as dt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config as C

RULES_DIR = C.REPO_ROOT / "rules"

#: Every field the schema requires. `null` is allowed only alongside a note
#: explaining why, which `validate()` enforces.
REQUIRED_FIELDS = [
    "juris", "staking_method", "unit_name", "unit_area_ha", "registration_cost",
    "licence", "work_requirement", "cash_in_lieu", "credit_banking", "transfer",
    "expiry_mechanics", "consultation_notes", "exempt_lands_notes",
    "verified_by", "verified_date", "source_urls",
]

#: A file counts as verified only when a human has signed and dated it.
def is_verified(rules: dict) -> bool:
    return bool(rules.get("verified_by")) and bool(rules.get("verified_date")) \
        and rules.get("verified_by") != "human"          # the placeholder, not a person


def load(juris: str) -> dict:
    import yaml
    p = RULES_DIR / f"{juris.upper()}.yaml"
    if not p.exists():
        raise FileNotFoundError(f"no rules file for {juris} — expected {p}")
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def available() -> list:
    return sorted(p.stem for p in RULES_DIR.glob("*.yaml")) if RULES_DIR.exists() else []


def missing_fields(rules: dict) -> list:
    """Required fields that are absent, or null without an explanatory note."""
    out = []
    for f in REQUIRED_FIELDS:
        if f not in rules:
            out.append(f)
            continue
        v = rules[f]
        if v is None and not (rules.get("notes") or {}).get(f):
            out.append(f)
        elif isinstance(v, dict):
            for k, sub in v.items():
                if sub is None and not (rules.get("notes") or {}).get(f"{f}.{k}"):
                    out.append(f"{f}.{k}")
    return out


def stakeable_now(juris: str) -> tuple:
    """(ok, missing) — whether this jurisdiction is cleared for a staking decision.

    False whenever the rules are unverified or incomplete. This is the gate the
    dossier generator consults before it may recommend acquiring ground.
    """
    try:
        rules = load(juris)
    except FileNotFoundError as e:
        return False, [str(e)]
    missing = missing_fields(rules)
    if not is_verified(rules):
        missing = ["verified_by/verified_date (no human sign-off)"] + missing
    return (not missing), missing


def holding_schedule(juris: str, n_claims: int, years: int) -> dict:
    """Cost of holding `n_claims` for `years`, from the verified fee schedule.

    Raises on an unverified jurisdiction — see the module docstring.
    """
    rules = load(juris)
    if not is_verified(rules):
        raise ValueError(
            f"{juris} rules are not human-verified — refusing to produce a cost "
            f"model from placeholder values. Fill rules/{juris.upper()}.yaml and "
            f"set verified_by/verified_date.")
    return compute_schedule(rules, n_claims, years)


def compute_schedule(rules: dict, n_claims: int, years: int) -> dict:
    """The arithmetic, separated from the file so it is unit-testable.

    `work_requirement` is a list of `{years: "1-2", amount_per_unit: N}` bands.
    Most jurisdictions escalate with claim age, so the band covering each year
    is looked up rather than assumed constant.
    """
    reg = (rules.get("registration_cost") or {}).get("amount")
    lic = ((rules.get("licence") or {}).get("cost") or {})
    lic_amount, lic_per = lic.get("amount"), lic.get("per")
    bands = rules.get("work_requirement") or []

    def band_for(year: int):
        for b in bands:
            spec = str(b.get("years", ""))
            if "-" in spec:
                lo, hi = spec.split("-", 1)
                lo = int(lo)
                hi = 10**9 if hi.strip() in ("N", "n", "+", "") else int(hi)
            else:
                lo = hi = int(spec)
            if lo <= year <= hi:
                return b
        return None

    # `any_work` is not redundant with `total_work`: a schedule whose bands all
    # have null amounts would otherwise sum to 0.0 and report a $0 holding cost,
    # which is the exact silent-zero this module exists to refuse.
    rows, total_work, any_work = [], 0.0, False
    for y in range(1, years + 1):
        b = band_for(y)
        per_unit = (b or {}).get("amount_per_unit")
        work = None if per_unit is None else per_unit * n_claims
        if work is not None:
            total_work += work
            any_work = True
        rows.append({"year": y, "band": (b or {}).get("years"),
                     "work_per_unit": per_unit, "work_total": work})
    work_total = total_work if any_work else None

    registration = None if reg is None else reg * n_claims
    lic_total = None
    if lic_amount is not None:
        # Licence cost is per N years, so it recurs across the horizon.
        span = lic_per if isinstance(lic_per, (int, float)) and lic_per else 1
        lic_total = lic_amount * max(1, -(-years // int(span)))

    parts = (registration, lic_total, work_total)
    known = [v for v in parts if v is not None]
    complete = all(v is not None for v in parts)
    return {
        "juris": rules.get("juris"), "claims": n_claims, "years": years,
        "registration_total": registration,
        "licence_total": lic_total,
        "work_by_year": rows,
        "work_total": work_total,
        # A "grand total" that silently omits the components nobody has filled
        # in reads as an answer. It is only reported when every component is
        # known; otherwise the partial is labelled as a partial.
        "grand_total": sum(known) if complete else None,
        "known_subtotal": sum(known) if known else None,
        "unknown_components": [n for n, v in
                               zip(("registration", "licence", "work"), parts)
                               if v is None],
        "currency": (rules.get("registration_cost") or {}).get("currency"),
        "complete": complete,
    }


def validate(verbose: bool = True) -> int:
    """Report the state of every rules file. Returns the count needing work."""
    bad = 0
    for juris in available():
        rules = load(juris)
        missing = missing_fields(rules)
        ok = is_verified(rules) and not missing
        if not ok:
            bad += 1
        status = "VERIFIED" if ok else ("UNVERIFIED" if not is_verified(rules)
                                        else "INCOMPLETE")
        print(f"  {juris:<5}{status:<12}"
              f"{'signed ' + str(rules.get('verified_date')) if is_verified(rules) else 'not signed off':<26}"
              f"{len(missing)} field(s) outstanding")
        if verbose and missing:
            for m in missing[:12]:
                print(f"        · {m}")
            if len(missing) > 12:
                print(f"        · … {len(missing)-12} more")
        for q in rules.get("open_questions") or []:
            print(f"        ? {q}")
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--schedule", metavar="JURIS")
    ap.add_argument("--claims", type=int, default=10)
    ap.add_argument("--years", type=int, default=5)
    args = ap.parse_args()

    if args.status or args.validate:
        n = validate()
        print(f"\n  {len(available())} jurisdiction(s); {n} not ready for a staking decision")
        if args.validate:
            sys.exit(1 if n else 0)
        return
    if args.schedule:
        ok, missing = stakeable_now(args.schedule)
        if not ok:
            print(f"  {args.schedule} is NOT cleared for a staking decision:")
            for m in missing:
                print(f"    · {m}")
            print("\n  holding_schedule() deliberately refuses to compute from "
                  "placeholder values.")
            sys.exit(1)
        import json
        print(json.dumps(holding_schedule(args.schedule, args.claims, args.years),
                         indent=2))
        return
    ap.print_help()


if __name__ == "__main__":
    main()
