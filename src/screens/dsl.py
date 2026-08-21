#!/usr/bin/env python3
"""
dsl.py — the screening language, parsed (C4.3).

Grammar, from PLAN_C4 4.3:

    screen "<name>" [commodity <system>] [juris ON,BC] {
      where:
        <feature> <op> <value|percentile(p, scope)>
        dist_to(<feature_class>) < <km>
        count(<point_class>[, radius_km]) == 0 | >= n
        tenure == open | expiring(<days>)
        no_barren(commodity=<c>[, min_depth_m=<d>])
        heat >= percentile(p, juris)
        within(<block_id> buffer <km>)
      rank by: <expression>
      limit: <n>
    }

Hand-written recursive descent over a line-oriented grammar rather than a parser
generator. The grammar is small and fixed, the error messages matter more than
the flexibility — a geologist writing a screen should be told which line is
wrong and what was expected there, not handed a shift/reduce conflict — and a
build dependency for eighty lines of parsing is a bad trade.

**Every predicate is a `Predicate` with a `kind`.** `compile.py` dispatches on
that and nothing else, so adding a predicate is one branch in each file and
never a change to the parser's shape.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

#: Comparison operators, longest first so `>=` is not read as `>` then `=`.
OPS = (">=", "<=", "==", "!=", ">", "<")

#: Percentile scopes. `juris` is the whole jurisdiction; `terrane` strata by the
#: fabric's terrane_id, matching how C2.3 normalises so that a screen and a
#: model mean the same thing by "anomalous".
SCOPES = ("juris", "terrane", "global")


class ScreenSyntaxError(ValueError):
    def __init__(self, line_no: int, line: str, expected: str):
        super().__init__(
            f"line {line_no}: {expected}\n    {line.strip()}")
        self.line_no, self.line, self.expected = line_no, line, expected


@dataclass
class Percentile:
    p: float
    scope: str = "juris"


@dataclass
class Predicate:
    kind: str                       # feature | dist_to | count | tenure |
                                    # no_barren | heat | within
    raw: str
    line_no: int
    name: str | None = None
    op: str | None = None
    value: object = None
    args: dict = field(default_factory=dict)


@dataclass
class Screen:
    name: str
    where: list = field(default_factory=list)
    rank_by: str | None = None
    limit: int | None = None
    commodity: str | None = None
    juris: list = field(default_factory=lambda: ["ON"])
    text: str = ""

    def describe(self) -> str:
        out = [f'screen "{self.name}"'
               + (f" commodity {self.commodity}" if self.commodity else "")
               + (f" juris {','.join(self.juris)}" if self.juris else "")]
        out.append("  where:")
        for p in self.where:
            out.append(f"    [{p.kind}] {p.raw}")
        if self.rank_by:
            out.append(f"  rank by: {self.rank_by}")
        if self.limit:
            out.append(f"  limit: {self.limit}")
        return "\n".join(out)


_HEADER = re.compile(
    r'^\s*screen\s+"(?P<name>[^"]+)"'
    r'(?:\s+commodity\s+(?P<commodity>[A-Za-z0-9_]+))?'
    r'(?:\s+juris\s+(?P<juris>[A-Za-z_,]+))?'
    r'\s*\{\s*$')

_PERCENTILE = re.compile(
    r'^percentile\(\s*(?P<p>[\d.]+)\s*(?:,\s*(?P<scope>\w+)\s*)?\)$')

_DIST_TO = re.compile(r'^dist_to\(\s*(?P<cls>[\w.]+)\s*\)\s*'
                      r'(?P<op>' + "|".join(map(re.escape, OPS)) + r')\s*'
                      r'(?P<km>[\d.]+)\s*$')

_COUNT = re.compile(r'^count\(\s*(?P<cls>[\w.]+)\s*'
                    r'(?:,\s*(?P<radius>[\d.]+)\s*)?\)\s*'
                    r'(?P<op>' + "|".join(map(re.escape, OPS)) + r')\s*'
                    r'(?P<n>\d+)\s*$')

_TENURE = re.compile(r'^tenure\s*==\s*(?P<what>open|claimed|expiring\(\s*\d+\s*\))\s*$')

_NO_BARREN = re.compile(r'^no_barren\((?P<args>.*)\)\s*$')

_WITHIN = re.compile(r'^within\(\s*(?P<what>[\w\-]+)'
                     r'(?:\s+buffer\s+(?P<km>[\d.]+))?\s*\)\s*$')

_HEAT = re.compile(r'^heat\s*(?P<op>' + "|".join(map(re.escape, OPS)) + r')\s*'
                   r'(?P<val>.+?)\s*$')

_FEATURE = re.compile(r'^(?P<name>[A-Za-z_][\w.]*)\s*'
                      r'(?P<op>' + "|".join(map(re.escape, OPS)) + r')\s*'
                      r'(?P<val>.+?)\s*$')


def _value(tok: str, line_no: int, line: str):
    tok = tok.strip()
    m = _PERCENTILE.match(tok)
    if m:
        scope = m.group("scope") or "juris"
        if scope not in SCOPES:
            raise ScreenSyntaxError(line_no, line,
                                    f"unknown percentile scope {scope!r}; "
                                    f"expected one of {', '.join(SCOPES)}")
        return Percentile(float(m.group("p")), scope)
    try:
        return float(tok)
    except ValueError:
        return tok.strip('"\'')


def parse(text: str) -> Screen:
    """Screen text → `Screen`. Raises `ScreenSyntaxError` with a line number."""
    lines = text.splitlines()
    scr: Screen | None = None
    section = None
    i = 0
    for raw in lines:
        i += 1
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if scr is None:
            m = _HEADER.match(line)
            if not m:
                raise ScreenSyntaxError(
                    i, raw, 'expected a header: screen "<name>" [commodity X] '
                            '[juris ON] {')
            scr = Screen(name=m.group("name"),
                         commodity=m.group("commodity"),
                         juris=[j.strip() for j in
                                (m.group("juris") or "ON").split(",")],
                         text=text)
            continue
        stripped = line.strip()
        if stripped == "}":
            section = None
            continue
        if stripped.rstrip(":") == "where":
            section = "where"
            continue
        if stripped.startswith("rank by:"):
            scr.rank_by = stripped.split(":", 1)[1].strip() or None
            section = None
            continue
        if stripped.startswith("limit:"):
            val = stripped.split(":", 1)[1].strip()
            if not val.isdigit():
                raise ScreenSyntaxError(i, raw, "limit must be an integer")
            scr.limit = int(val)
            section = None
            continue
        if section != "where":
            raise ScreenSyntaxError(
                i, raw, "expected `where:`, `rank by:`, `limit:` or `}`")
        scr.where.append(_predicate(stripped, i, raw))

    if scr is None:
        raise ScreenSyntaxError(1, text[:60], "empty screen")
    if not scr.where:
        raise ScreenSyntaxError(
            1, f'screen "{scr.name}"',
            "a screen with no `where:` predicates would return the whole "
            "fabric; say what you are looking for")
    return scr


def _predicate(s: str, line_no: int, raw: str) -> Predicate:
    m = _DIST_TO.match(s)
    if m:
        return Predicate("dist_to", s, line_no, name=m.group("cls"),
                         op=m.group("op"), value=float(m.group("km")))
    m = _COUNT.match(s)
    if m:
        return Predicate("count", s, line_no, name=m.group("cls"),
                         op=m.group("op"), value=int(m.group("n")),
                         args={"radius_km": float(m.group("radius") or 0) or None})
    m = _TENURE.match(s)
    if m:
        what = m.group("what")
        if what.startswith("expiring"):
            days = int(re.search(r"\d+", what).group(0))
            return Predicate("tenure", s, line_no, name="expiring",
                             args={"days": days})
        return Predicate("tenure", s, line_no, name=what)
    m = _NO_BARREN.match(s)
    if m:
        args = {}
        for part in (m.group("args") or "").split(","):
            if "=" in part:
                k, v = part.split("=", 1)
                k, v = k.strip(), v.strip().strip('"\'')
                args[k] = float(v) if re.fullmatch(r"[\d.]+", v) else v
        return Predicate("no_barren", s, line_no, args=args)
    m = _WITHIN.match(s)
    if m:
        return Predicate("within", s, line_no, name=m.group("what"),
                         args={"buffer_km": float(m.group("km") or 0)})
    m = _HEAT.match(s)
    if m and s.split()[0] == "heat":
        return Predicate("heat", s, line_no, name="heat", op=m.group("op"),
                         value=_value(m.group("val"), line_no, raw))
    m = _FEATURE.match(s)
    if m:
        return Predicate("feature", s, line_no, name=m.group("name"),
                         op=m.group("op"),
                         value=_value(m.group("val"), line_no, raw))
    raise ScreenSyntaxError(
        line_no, raw,
        "unrecognised predicate. Expected one of: <feature> <op> <value>, "
        "dist_to(x) < km, count(x[, km]) >= n, tenure == open|claimed|"
        "expiring(days), no_barren(commodity=Au), heat >= percentile(p), "
        "within(block buffer km)")
