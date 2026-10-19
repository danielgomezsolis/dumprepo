#!/usr/bin/env python3
"""
Side-by-side English / Spanish page: one section per (condition x corpus), one card per record.

  .venv/bin/python compare_html.py                 # -> report/compare.html
  .venv/bin/python compare_html.py --runs runs --data v.1 --out report/compare.html

Each card puts the judge's CSV line for the English file next to the Spanish one (counts,
measures, risks, keywords, notes, error offsets, B3 problem cells), with the gold values.
Cells that differ between the languages are highlighted. One self-contained HTML file.
"""
import argparse
import html
import os
import re
from collections import defaultdict

from report import COND_NAME, COND_ORDER, CORPORA, LANG, VERDICT, f3, load_runs, mean, pairs

E = html.escape

COUNTS = [("placeholders", "placeholders", 0), ("entities", "entities", 0), ("n_ok", "ok", 0),
          ("n_wrong_label", "wrong label", 0), ("n_over", "over", 0), ("n_partial", "partial", 0),
          ("n_leak", "leak", 0), ("n_keep", "keep", 0), ("n_low_conf", "low confidence", 0)]
MEASURES = [("precision", "precision"), ("mention_recall", "mention recall"), ("entity_recall", "entity recall"),
            ("b3_p", "B³ p"), ("b3_r", "B³ r"), ("consistency", "consistency"),
            ("identity_coverage", "identity coverage"), ("f1", "F1"), ("label_agreement", "label agreement")]
RISKS = [("risk_singling_out", "singling out"), ("risk_linkability", "linkability"), ("risk_inference", "inference")]
TEXTS = [("exposed_categories", "exposed categories"), ("over_categories", "over-masked categories")]


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def val(j, k, nd):
    x = j.get(k, "")
    if x == "":
        return "–"
    n = num(x)
    if n is None:
        return E(x)
    return "%.*f" % (nd, n) if nd else str(int(n))


def row(label, a, b, gold=None, cls=""):
    diff = ' class="diff"' if a != b else ""
    g = "" if gold is None else "<td class=\"gold\">%s</td>" % gold
    return "<tr%s><th>%s</th><td>%s</td><td>%s</td>%s</tr>" % (' class="d"' if a != b else "", label, a, b, g)


def strip_svg(r):
    marks = []
    for verdict, s, e in r["offsets"]:
        color, shape = VERDICT[verdict]
        x = 1000.0 * ((s + e) / 2) / max(r["text_len"], 1)
        marks.append('<circle cx="%.1f" cy="12" r="5" fill="%s"><title>%s %d–%d</title></circle>' % (x, color, verdict, s, e))
    return ('<svg viewBox="0 0 1000 24" preserveAspectRatio="none" class="strip" role="img" aria-label="%d problem positions">'
            '<rect x="0" y="9" width="1000" height="6" rx="3" class="track"/>%s</svg>' % (len(r["offsets"]), "".join(marks)))


def chips(own, other):
    return "".join('<span class="chip%s">%s</span>' % ("" if k in other else " uniq", E(k)) for k in own) or '<span class="none">none</span>'


def offsets_text(r):
    return E("; ".join("%s:%d-%d" % o for o in r["offsets"])) or "–"


def b3_problems(cells):
    bad = []
    for c in cells:
        p, r = num(c["p"]), num(c["r"])
        if p is None or r is None or (p >= 0.9995 and r >= 0.9995):
            continue
        kind = " + ".join(x for x, b in (("merge", p < 0.9995), ("split", r < 0.9995)) if b)
        bad.append("%s ↔ %s · n=%s · p=%s r=%s · %s" % (E(c["entity"]), E(c["identity"]), c["n"], c["p"], c["r"], kind))
    return "<br>".join(bad) or '<span class="none">none</span>'


PH = re.compile(r"\[[A-Z_]+\d*\]")


def snap(text, verdict, s, e):
    """The judge counts characters by hand, so offsets drift a little. Placeholder verdicts snap to
    the nearest placeholder; leaks widen to whole words and drop trailing blanks."""
    n = len(text)
    s, e = max(0, min(s, n)), max(0, min(e, n))
    if verdict != "leak":
        best = min(PH.finditer(text), key=lambda m: abs(m.start() - s), default=None)
        if best is not None and abs(best.start() - s) <= 12:
            return best.start(), best.end()
    while s > 0 and text[s - 1].isalnum() and s < n and text[s].isalnum():
        s -= 1
    while e < n and e > 0 and text[e - 1].isalnum() and text[e].isalnum():
        e += 1
    while e > s and not text[e - 1].isalnum():
        e -= 1
    while s < e and not text[s].isalnum():
        s += 1
    return s, e


def render_text(r):
    """The masked record with each problem the judge pointed at marked and tagged."""
    text = r["text"]
    spans = sorted((snap(text, v, a, b) + (v,) for v, a, b in r["offsets"]), key=lambda x: (x[0], x[1]))
    out, pos = [], 0
    phs = r.get("phs", {})
    def plain(t, base):
        # E() leaves [A-Z0-9_] alone, so placeholder positions in the escaped text are still valid
        def sub(m):
            hit = phs.get(base + m.start())
            if not hit or hit[0] != m.group():
                return "<b class='ph'>%s</b>" % m.group()
            tag = E(hit[2] + (" · " + hit[3] if hit[3] else ""))
            return ("<b class='ph rv' tabindex='0' role='button' data-tag='%s' data-orig='%s' title='%s: click to show the original'>%s</b>"
                    % (m.group(), E(hit[1], quote=True), tag, m.group()))
        return PH.sub(sub, E(t))
    for s_, e_, v in spans:
        if e_ <= s_ or s_ < pos:          # empty, or overlaps a span already drawn
            continue
        out.append(plain(text[pos:s_], pos))
        out.append("<mark class='%s'>%s<sup>%s</sup></mark>" % (v.replace("-", ""), plain(text[s_:e_], s_), v))
        pos = e_
    out.append(plain(text[pos:], pos))
    return "<div class='txt'>%s</div>" % "".join(out)


def card(k, v, b3):
    en, es = v["en"], v["es"]
    je, js = en["judge"], es["judge"]
    dleak = (es["n_leak"] or 0) - (en["n_leak"] or 0)
    drec = None if es["recall"] is None or en["recall"] is None else es["recall"] - en["recall"]
    differs = any(je.get(c) != js.get(c) for c in ("n_ok", "n_wrong_label", "n_over", "n_partial", "n_leak", "placeholders"))
    badge = '<span class="badge diff">verdicts differ</span>' if differs else '<span class="badge same">same verdict counts</span>'
    summ = ('<summary><span class="id">%s</span><span class="ann">%s</span>%s'
            '<span class="m">recall %s / %s</span><span class="m">leaks %s / %s</span><span class="m">Δ leaks %+d</span></summary>' % (
                E(k[2]), E(k[3]), badge, f3(en["recall"]), f3(es["recall"]), f3(en["n_leak"], 0), f3(es["n_leak"], 0), dleak))
    rows = ["<tr class='hd'><th></th><th class='en'>English</th><th class='es'>Spanish</th><th>gold</th></tr>"]
    gold_map = {"placeholders": en["g_placeholders"], "n_leak": en["g_leaks"], "mention_recall": en["g_recall"],
                "consistency": en["g_consistency"]}
    for key, label, nd in COUNTS:
        g = gold_map.get(key)
        rows.append(row(label, val(je, key, nd), val(js, key, nd), "" if g is None else (f3(g) if isinstance(g, float) else g)))
    for key, label in MEASURES:
        g = gold_map.get(key)
        rows.append(row(label, val(je, key, 3), val(js, key, 3), "" if g is None else f3(g)))
    for key, label in RISKS:
        rows.append(row(label, E(je.get(key, "") or "–"), E(js.get(key, "") or "–")))
    for key, label in TEXTS:
        rows.append(row(label, E(je.get(key, "") or "–"), E(js.get(key, "") or "–")))
    table = "<table class='m'>%s</table>" % "".join(rows)
    two = lambda t, a, b: "<h5>%s</h5><div class='two'><div class='en'>%s</div><div class='es'>%s</div></div>" % (t, a, b)
    body = [two("Masked record, problems tagged <small>(click a bold placeholder to show the original; marks are the judge's offsets, snapped to whole words or placeholders)</small>",
                render_text(en), render_text(es)),
            table,
            two("Keywords <small>(highlighted = only in this language)</small>", chips(en["keywords"], es["keywords"]), chips(es["keywords"], en["keywords"])),
            two("Judge notes", "<p>%s</p>" % E(en["notes"] or "–"), "<p>%s</p>" % E(es["notes"] or "–")),
            two("Where the problems are <small>(position in the text; %d / %d marks)</small>" % (len(en["offsets"]), len(es["offsets"])),
                strip_svg(en) + "<code>%s</code>" % offsets_text(en), strip_svg(es) + "<code>%s</code>" % offsets_text(es)),
            two("B³ cells with p or r below 1", b3_problems(b3.get((en["run"], k[2]), [])), b3_problems(b3.get((es["run"], k[2]), [])))]
    return '<details class="card" data-id="%s" data-diff="%d">%s<div class="body">%s</div></details>' % (
        E(k[2].lower()), 1 if differs else 0, summ, "".join(body))


def section_summary(ps):
    def m(lang, f):
        return mean([v[lang][f] for v in ps.values()])
    rows = [("records", len(ps), len(ps))]
    cells = []
    for label, f, nd in (("placeholders (mean)", "placeholders", 1), ("leaks (mean)", "n_leak", 2), ("over (mean)", "n_over", 2),
                         ("mention recall (mean)", "recall", 3), ("consistency (mean)", "consistency", 3)):
        cells.append("<tr><th>%s</th><td>%s</td><td>%s</td></tr>" % (label, f3(m("en", f), nd), f3(m("es", f), nd)))
    same = sum(1 for v in ps.values() if all(v["en"]["judge"].get(c) == v["es"]["judge"].get(c)
                                              for c in ("n_ok", "n_wrong_label", "n_over", "n_partial", "n_leak", "placeholders")))
    cells.append("<tr><th>records with identical verdict counts</th><td colspan='2'>%d of %d</td></tr>" % (same, len(ps)))
    return ("<table class='m sum'><tr class='hd'><th>%d records, both languages</th><th class='en'>English</th><th class='es'>Spanish</th></tr>%s</table>"
            % (len(ps), "".join(cells)))


CSS = """
:root{--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--line:#e1e0d9;--en:%(en)s;--es:%(es)s;--hl:#fff1c2}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--line:#2c2c2a;--en:#3987e5;--es:#d95926;--hl:#3a3217}}
:root[data-theme="dark"]{--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--line:#2c2c2a;--en:#3987e5;--es:#d95926;--hl:#3a3217}
*{box-sizing:border-box}body{margin:0;background:var(--page);color:var(--ink);font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
header.top{position:sticky;top:0;z-index:5;background:var(--page);border-bottom:1px solid var(--line);padding:10px 16px}
header.top .row{max-width:1200px;margin:0 auto;display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center}
h1{font-size:17px;margin:0 14px 0 0}nav a{color:var(--ink2);text-decoration:none;padding:3px 9px;border:1px solid var(--line);border-radius:14px;font-size:13px}
nav a:hover{background:var(--line)}label{color:var(--ink2);font-size:13px}input[type=search]{background:var(--surface);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:4px 8px}
main{max-width:1200px;margin:0 auto;padding:8px 16px 64px}
section{margin-top:34px}h2{font-size:19px;margin:0 0 8px;padding-top:12px;border-top:1px solid var(--line)}h5{margin:14px 0 4px;font-size:13px;color:var(--ink2)}h5 small{font-weight:400;color:var(--muted)}
table.m{border-collapse:collapse;width:100%%;font-size:13px}table.m th,table.m td{padding:4px 9px;border-bottom:1px solid var(--line);text-align:left}
table.m th{font-weight:500;color:var(--ink2)}table.m td{font-variant-numeric:tabular-nums}table.m .gold{color:var(--muted)}
tr.hd th{font-weight:700}th.en,.two .en h5{color:var(--en)}th.es{color:var(--es)}
tr.d td{background:var(--hl)}table.sum{max-width:560px;margin-bottom:10px}
details.card{background:var(--surface);border:1px solid var(--line);border-radius:10px;margin:8px 0}
details.card>summary{cursor:pointer;padding:9px 14px;display:flex;flex-wrap:wrap;gap:6px 14px;align-items:center;list-style:none}
details.card>summary::-webkit-details-marker{display:none}details[open]>summary{border-bottom:1px solid var(--line)}
.id{font-weight:700}.ann{color:var(--muted)}.m{color:var(--ink2);font-variant-numeric:tabular-nums}
.badge{font-size:12px;padding:1px 8px;border-radius:10px}.badge.diff{background:var(--hl)}.badge.same{border:1px solid var(--line);color:var(--muted)}
.body{padding:6px 14px 14px}.two{display:grid;grid-template-columns:1fr 1fr;gap:16px}.two p{margin:0;color:var(--ink2);font-size:13px}
.two .en{border-top:3px solid var(--en);padding-top:6px}.two .es{border-top:3px solid var(--es);padding-top:6px}
.chip{display:inline-block;border:1px solid var(--line);border-radius:10px;padding:0 8px;margin:0 4px 4px 0;font-size:12px}.chip.uniq{background:var(--hl)}
.none{color:var(--muted)}svg.strip{width:100%%;height:22px;display:block}svg .track{fill:var(--line)}code{font-size:12px;color:var(--ink2);word-break:break-word}
.txt{white-space:pre-wrap;font-size:13px;line-height:1.6;max-height:440px;overflow:auto;border:1px solid var(--line);border-radius:6px;padding:8px 10px;background:var(--page)}
.ph.rv{cursor:pointer}.ph.rv:hover,.ph.rv:focus{outline:2px solid var(--en);outline-offset:1px}
.ph.shown{background:transparent;border:1px dashed var(--ink2);font-weight:500}
.ph{color:var(--ink);background:var(--line);border-radius:3px;padding:0 2px;font-weight:600}
mark{color:var(--ink);border-radius:3px;padding:0 2px;border-bottom:2px solid}mark sup{font-size:9px;font-weight:700;margin-left:2px;text-transform:uppercase}
mark.leak{background:rgba(27,175,122,.28);border-color:#1baf7a}mark.over{background:rgba(237,161,0,.30);border-color:#eda100}
mark.partial{background:rgba(98,80,214,.25);border-color:#6250d6}mark.wronglabel{background:rgba(232,123,164,.30);border-color:#e87ba4}
.legend{font-size:12px;color:var(--muted);margin:2px 0 8px}.legend i{display:inline-block;width:10px;height:10px;border-radius:50%%;margin:0 4px 0 10px;vertical-align:-1px}
@media (max-width:760px){.two{grid-template-columns:1fr}}
""" % {"en": LANG["en"], "es": LANG["es"]}

JS = """
const q=document.getElementById('q'),d=document.getElementById('d');
function apply(){const t=q.value.trim().toLowerCase();document.querySelectorAll('details.card').forEach(c=>{
 c.hidden=(t&&!c.dataset.id.includes(t))||(d.checked&&c.dataset.diff!=='1')});
 document.querySelectorAll('section').forEach(s=>{const n=s.querySelectorAll('details.card:not([hidden])').length;s.querySelector('.cnt').textContent=n})}
q.addEventListener('input',apply);d.addEventListener('change',apply);
function flip(b,on){const o=b.dataset.orig;if(o===undefined||o==='')return;b.textContent=on?o:b.dataset.tag;b.classList.toggle('shown',on)}
document.addEventListener('click',e=>{const b=e.target.closest('.ph.rv');if(b)flip(b,!b.classList.contains('shown'))});
document.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){const b=e.target.closest&&e.target.closest('.ph.rv');if(b){e.preventDefault();flip(b,!b.classList.contains('shown'))}}});
document.getElementById('rev').addEventListener('click',e=>{const on=e.target.dataset.on!=='1';e.target.dataset.on=on?'1':'0';e.target.textContent=on?'hide all originals':'show all originals';
 document.querySelectorAll('details[open] .ph.rv').forEach(b=>flip(b,on))});
document.getElementById('open').addEventListener('click',()=>document.querySelectorAll('details.card:not([hidden])').forEach(c=>c.open=true));
document.getElementById('close').addEventListener('click',()=>document.querySelectorAll('details.card').forEach(c=>c.open=false));
"""


def build(rows, b3_rows):
    b3 = defaultdict(list)
    for c in b3_rows:
        b3[(c["run"], c["doc_id"])].append(c)
    secs, nav = [], []
    for cond in COND_ORDER:
        for corpus in CORPORA:
            ps = pairs([r for r in rows if r["cond"] == cond and r["corpus"] == corpus])
            if not ps:
                continue
            sid = "%s-%s" % (corpus, cond)
            title = "%s · %s" % (corpus.upper(), COND_NAME[cond])
            nav.append('<a href="#%s">%s</a>' % (sid, title))
            cards = "".join(card(k, v, b3) for k, v in sorted(ps.items(), key=lambda kv: kv[0][2]))
            secs.append('<section id="%s"><h2>masked_%s_%s_en  ·  masked_%s_%s_es <small class="none">(%s, <span class="cnt">%d</span> shown)</small></h2>%s%s</section>' % (
                sid, cond, corpus, cond, corpus, title, len(ps), section_summary(ps), cards))
    legend = '<div class="legend">Problem marks:%s &nbsp;·&nbsp; yellow = differs between English and Spanish</div>' % "".join(
        '<i style="background:%s"></i>%s' % (c, v) for v, (c, _) in VERDICT.items())
    head = ('<header class="top"><div class="row"><h1>English ↔ Spanish, side by side</h1><nav>%s</nav></div>'
            '<div class="row" style="margin-top:6px"><input id="q" type="search" placeholder="filter by record id">'
            '<label><input id="d" type="checkbox"> only records whose verdicts differ</label>'
            '<button id="open">expand shown</button><button id="close">collapse all</button>'
            '<button id="rev" data-on="0">show all originals</button>'
            '<span class="none">Originals are in this file: keep it local.</span></div></header>' % "".join(nav))
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            "<title>English Spanish comparison</title><style>%s</style></head><body>%s<main>%s%s</main><script>%s</script></body></html>"
            % (CSS, head, legend, "".join(secs), JS))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default="runs")
    ap.add_argument("--data", default="v.1")
    ap.add_argument("--out", default="report/compare.html")
    a = ap.parse_args()
    rows, b3_rows = load_runs(a.runs, a.data)
    if not rows:
        raise SystemExit("no judged runs found in %s" % a.runs)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(build(rows, b3_rows))
    print("wrote", a.out)


if __name__ == "__main__":
    main()
