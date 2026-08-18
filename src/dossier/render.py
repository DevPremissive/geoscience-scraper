#!/usr/bin/env python3
"""
render.py — dossier JSON → self-contained HTML (C4.1).

Renders locally to a file. Nothing is uploaded: this document is a decision
record about where to spend money, and where it goes is the operator's call.

Two rendering rules carried over from the schema, because a renderer that
prettifies them away would defeat the point:

  * an unavailable section renders a visible NOT AVAILABLE block with its
    reason, in the same visual weight as a populated section — a gap the reader
    can miss is a gap that reads as "nothing there";
  * every fact shows its provenance inline, not in a footnote.

Usage:
    python -m dossier.render --dossier <path.json>
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config as C

TEMPLATE = """<!doctype html>
<meta charset="utf-8">
<title>{{ d.target_id }} — target dossier</title>
<style>
 :root{--ink:#1a1d21;--mut:#5b6570;--line:#d8dde3;--warn:#8a5a00;--warnbg:#fff8e6;
       --na:#7a2d2d;--nabg:#fdf1f1;--ok:#0f5132}
 body{font:15px/1.55 -apple-system,Segoe UI,Roboto,sans-serif;color:var(--ink);
      max-width:900px;margin:2rem auto;padding:0 1.5rem}
 h1{font-size:1.5rem;margin:0 0 .2rem} h2{font-size:1.05rem;margin:2rem 0 .6rem;
      padding-bottom:.3rem;border-bottom:2px solid var(--line)}
 .meta{color:var(--mut);font-size:.85rem;margin-bottom:1.5rem}
 .status{display:inline-block;padding:.15rem .5rem;border-radius:3px;
      background:#eef2f6;font-weight:600;font-size:.8rem}
 table{border-collapse:collapse;width:100%;margin:.6rem 0;font-size:.86rem}
 th,td{text-align:left;padding:.35rem .5rem;border-bottom:1px solid var(--line);
      vertical-align:top}
 th{color:var(--mut);font-weight:600}
 .fact{margin:.35rem 0}
 .fact .lbl{color:var(--mut)} .fact .val{font-weight:600}
 .prov{color:var(--mut);font-size:.75rem}
 .note{background:var(--warnbg);border-left:3px solid var(--warn);
      padding:.5rem .7rem;margin:.5rem 0;font-size:.85rem}
 .na{background:var(--nabg);border-left:3px solid var(--na);padding:.7rem .9rem;
      margin:.5rem 0}
 .na b{color:var(--na)}
 .gate{background:#f4f0ff;border-left:3px solid #5b3fa8;padding:.7rem .9rem;
      margin:1rem 0;font-size:.87rem}
 .scroll{overflow-x:auto}
</style>
<h1>{{ d.target_id }}</h1>
<div class="meta">
  <span class="status">{{ d.status|upper }}</span>
  &nbsp;{{ d.jurisdiction }} · dossier {{ d.dossier_version }} ·
  fabric {{ d.fabric_version }} · features {{ d.feature_snapshot }} ·
  generated {{ d.generated_at }}
</div>

{% if d.licence_gates %}
<div class="gate"><b>Licence</b><br>
{% for g in d.licence_gates %}{{ g }}<br>{% endfor %}
This is the <b>internal</b> profile, which the licence permits. The sales
profile is blocked until written permission is on file.</div>
{% endif %}

{% for s in d.sections %}
<h2>{{ loop.index }}. {{ s.title }}</h2>
{% if not s.available %}
  <div class="na"><b>NOT AVAILABLE</b> — {{ s.unavailable_reason }}</div>
{% else %}
  {% for f in s.facts %}
  <div class="fact"><span class="lbl">{{ f.label }}:</span>
    <span class="val">{{ f.value }}{% if f.unit %} {{ f.unit }}{% endif %}</span>
    <span class="prov">[{{ f.provenance.source_id }} @ {{ f.provenance.snapshot_date }}]</span>
    {% if f.note %}<div class="note">{{ f.note }}</div>{% endif %}
  </div>
  {% endfor %}
  {% for name, rows in s.tables.items() %}
    {% if rows %}
    <div class="scroll"><table>
      <tr>{% for k in rows[0].keys() %}<th>{{ k }}</th>{% endfor %}</tr>
      {% for r in rows %}<tr>{% for v in r.values() %}<td>{{ v }}</td>{% endfor %}</tr>{% endfor %}
    </table></div>
    {% endif %}
  {% endfor %}
  {% for n in s.notes %}<div class="note">{{ n }}</div>{% endfor %}
{% endif %}
{% endfor %}

<h2>Provenance</h2>
<p class="prov">Every value above carries its source and snapshot date inline.
A value that could not be stamped with provenance is not reported — assembly
fails rather than emitting it (Master §4).</p>
"""


def render(dossier_path: Path, out: Path | None = None) -> Path:
    from jinja2 import Template
    d = json.loads(Path(dossier_path).read_text(encoding="utf-8"))
    html = Template(TEMPLATE).render(d=_Obj(d))
    out = out or Path(dossier_path).with_suffix(".html")
    out.write_text(html, encoding="utf-8")
    return out


class _Obj:
    """Attribute access over the plain JSON, so the template stays readable."""
    def __init__(self, d):
        self._d = d
    def __getattr__(self, k):
        v = self._d.get(k)
        if isinstance(v, dict):
            return _Obj(v)
        if isinstance(v, list):
            return [_Obj(x) if isinstance(x, dict) else x for x in v]
        return v
    def items(self):
        return self._d.items()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dossier", required=True)
    ap.add_argument("--out")
    args = ap.parse_args()
    p = render(Path(args.dossier), Path(args.out) if args.out else None)
    print(f"  → {p}  ({p.stat().st_size/1024:.0f} KB)")
    print("  local file only — nothing uploaded")


if __name__ == "__main__":
    main()
