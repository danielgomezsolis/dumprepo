#!/usr/bin/env python3
"""
Build an HTML report with PNG charts from the judge runs and the gold annotation.

  .venv/bin/python report.py                      # all runs/masked_* folders -> report/
  .venv/bin/python report.py --runs runs --data v.1 --out report

Reads, per run folder: judge.csv, key.csv, b3.csv (written by run_judge.py), and the dataset
file with the same name (the gold side, via analyze.py). Needs matplotlib and numpy.

Everything is paired by (corpus, condition, data_id, annotator, rep): the en and es files hold
the same records, so a language comparison is made record by record, not between averages.
"""
import argparse
import csv
import glob
import html
import os
import re
import statistics as st
from collections import Counter, defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

from analyze import gold_for
from run_judge import load_records, num

# ---- palette: reference instance of the dataviz skill (light surface), slots fixed by role
SURFACE, INK, INK2, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
LANG = {"en": "#2a78d6", "es": "#eb6834"}              # blue, orange
LANG_MARK = {"en": "o", "es": "D"}
VERDICT = {"leak": ("#1baf7a", "o"), "partial": ("#6250d6", "s"),
           "over": ("#eda100", "^"), "wrong-label": ("#e87ba4", "D")}   # aqua, violet, yellow, magenta
SEQ = ["#cde2fb", "#9ec5f4", "#5598e7", "#256abf", "#184f95", "#0d366b"]
COND_ORDER = ["full", "partial070", "direct_only"]
COND_NAME = {"full": "full", "partial070": "partial (0.70)", "direct_only": "direct only"}
CORPORA = ["tab", "panorama"]

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": AXIS, "axes.labelcolor": INK2, "xtick.color": MUTED, "ytick.color": MUTED,
    "text.color": INK, "axes.titlecolor": INK, "axes.titlesize": 11, "axes.titleweight": "bold",
    "axes.titlelocation": "left", "axes.labelsize": 9, "xtick.labelsize": 8, "ytick.labelsize": 8,
    "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True,
    "grid.color": GRID, "grid.linewidth": 0.6, "axes.axisbelow": True,
    "font.family": "sans-serif", "legend.frameon": False, "legend.fontsize": 8,
})


# ---------------------------------------------------------------- loading
def read_csv(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def parse_offsets(s):
    out = []
    for e in (s or "").split(";"):
        m = re.fullmatch(r"(leak|partial|wrong-label|over):(\d+)-(\d+)", e.strip())
        if m:
            out.append((m.group(1), int(m.group(2)), int(m.group(3))))
    return out


def run_name(name):
    m = re.fullmatch(r"masked_(full|direct_only|partial070)_(tab|panorama)_(en|es)", name)
    return m.groups() if m else None


def load_runs(runs_dir, data_dir):
    rows = []
    datasets = {os.path.basename(p)[:-6]: p for p in glob.glob(os.path.join(data_dir, "Masked*", "*.jsonl"))}
    b3_rows = []
    for d in sorted(glob.glob(os.path.join(runs_dir, "masked_*"))):
        name = os.path.basename(d)
        parsed = run_name(name)
        if not parsed or name not in datasets or not os.path.exists(os.path.join(d, "judge.csv")):
            continue
        cond, corpus, lang = parsed
        records = load_records(datasets[name])
        extra = []
        if cond == "direct_only":
            twin = "masked_full_%s_%s" % (corpus, lang)
            if twin in datasets:
                extra = load_records(datasets[twin])
        gold = gold_for(records, extra)
        by_line = {r["line"]: r for r in records}
        key_rows, judge = read_csv(os.path.join(d, "key.csv")), read_csv(os.path.join(d, "judge.csv"))
        if len(key_rows) != len(judge) or any(k["data_id"] != j["doc_id"] for k, j in zip(key_rows, judge)):
            print("skip %s: judge.csv and key.csv do not line up" % name)
            continue
        for k, j in zip(key_rows, judge):
            line = int(k["source_line"])
            g, rec = gold[line], by_line[line]
            rows.append({
                "run": name, "cond": cond, "corpus": corpus, "lang": lang,
                "data_id": k["data_id"], "annotator": k["annotator"], "rep": k["rep"], "call": k["doc_id"],
                "text_len": len(rec["text"]),
                "placeholders": num(j["placeholders"]), "entities": num(j["entities"]),
                "n_ok": num(j["n_ok"]), "n_wrong_label": num(j["n_wrong_label"]),
                "n_over": num(j["n_over"]), "n_partial": num(j["n_partial"]),
                "n_leak": num(j["n_leak"]), "n_low_conf": num(j["n_low_conf"]),
                "precision": num(j["precision"]), "recall": num(j["mention_recall"]),
                "entity_recall": num(j["entity_recall"]), "consistency": num(j["consistency"]),
                "risk": j["risk_singling_out"], "keywords": [x for x in j["keywords"].split(";") if x],
                "notes": j["notes"], "offsets": parse_offsets(j.get("error_offsets", "")),
                "judge": j, "text": rec["text"],
                "phs": {x["start_offset"]: (x["placeholder"], x.get("original_text", ""), x.get("entity_type", ""),
                                            x.get("identifier_type", ""))
                        for x in (rec["item"].get("placeholders") or []) if "start_offset" in x},
                "g_placeholders": g["gold_placeholders"], "g_leaks": g["gold_leaks"],
                "g_recall": g["gold_mention_recall"], "g_consistency": g["gold_consistency"],
            })
        b3_path = os.path.join(d, "b3.csv")
        if os.path.exists(b3_path):
            for c in read_csv(b3_path):
                c.update({"run": name, "cond": cond, "corpus": corpus, "lang": lang})
                b3_rows.append(c)
    return rows, b3_rows


# ---------------------------------------------------------------- helpers
def pkey(r):
    return (r["corpus"], r["cond"], r["data_id"], r["annotator"], r["rep"])


def pairs(rows):
    """{pair key: {lang: row}} for pairs that have both languages."""
    d = defaultdict(dict)
    for r in rows:
        d[pkey(r)][r["lang"]] = r
    return {k: v for k, v in d.items() if "en" in v and "es" in v}


def mean(xs):
    xs = [x for x in xs if x is not None]
    return st.mean(xs) if xs else None


def f3(x, nd=3):
    return "–" if x is None else ("%.*f" % (nd, x) if isinstance(x, float) else str(x))


def corr(xs, ys):
    pts = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pts) < 3:
        return None
    mx, my = mean([p[0] for p in pts]), mean([p[1] for p in pts])
    sx = sum((p[0] - mx) ** 2 for p in pts) ** 0.5
    sy = sum((p[1] - my) ** 2 for p in pts) ** 0.5
    return None if sx == 0 or sy == 0 else sum((p[0] - mx) * (p[1] - my) for p in pts) / (sx * sy)


def style(ax, title=None, xlabel=None, ylabel=None):
    if title:
        ax.set_title(title)
    if xlabel:
        ax.set_xlabel(xlabel)
    if ylabel:
        ax.set_ylabel(ylabel)
    ax.tick_params(length=0)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(AXIS)


def lang_legend(fig, loc="upper right"):
    hs = [Line2D([0], [0], marker=LANG_MARK[l], color="none", markerfacecolor=LANG[l],
                 markeredgecolor=SURFACE, markersize=8, label={"en": "English", "es": "Spanish"}[l])
          for l in ("en", "es")]
    fig.legend(handles=hs, loc=loc, ncol=2, bbox_to_anchor=(0.99, 0.995))


def save(fig, figdir, name):
    fig.savefig(os.path.join(figdir, name), dpi=150, bbox_inches="tight")
    plt.close(fig)
    return "figs/" + name


# ---------------------------------------------------------------- figures
def off_by(r):
    return None if r["placeholders"] is None else int(round(r["placeholders"] - r["g_placeholders"]))


def fig_placeholders(rows, figdir):
    """Top: how far off the judge's placeholder count is (judged - gold), with counts on the bars.
    Bottom: judged against gold, one bubble per distinct point (size = records), worst misses named."""
    fig, axes = plt.subplots(2, 3, figsize=(11, 7.6), gridspec_kw={"height_ratios": [1, 1.25]})
    cats = ["≤ −3", "−2", "−1", "exact", "+1", "+2", "≥ +3"]
    def bucket(d):
        return min(max(d, -3), 3) + 3
    for ci, cond in enumerate(COND_ORDER):
        rs = [r for r in rows if r["cond"] == cond and off_by(r) is not None]
        ax = axes[0][ci]
        w = 0.38
        for dx, lang in ((-w / 2, "en"), (w / 2, "es")):
            sub = [r for r in rs if r["lang"] == lang]
            cnt = Counter(bucket(off_by(r)) for r in sub)
            xs = [b + dx for b in range(7)]
            ys = [cnt.get(b, 0) for b in range(7)]
            ax.bar(xs, ys, width=w, color=LANG[lang], zorder=3)
            for x, y in zip(xs, ys):
                if y:
                    ax.text(x, y, str(y), ha="center", va="bottom", fontsize=7, color=INK2)
        exact = {l: sum(off_by(r) == 0 for r in rs if r["lang"] == l) for l in ("en", "es")}
        tot = {l: sum(1 for r in rs if r["lang"] == l) for l in ("en", "es")}
        style(ax, COND_NAME[cond], "judged − gold placeholders", "records" if ci == 0 else None)
        ax.set_xticks(range(7))
        ax.set_xticklabels(cats)
        ax.set_ylim(0, ax.get_ylim()[1] * 1.12)
        ax.yaxis.get_major_locator().set_params(integer=True)
        ax.text(0.99, 0.97, "exact: en %d/%d · es %d/%d" % (exact["en"], tot["en"], exact["es"], tot["es"]),
                transform=ax.transAxes, ha="right", va="top", fontsize=8, color=INK2)

        ax = axes[1][ci]
        hi = max([r["g_placeholders"] for r in rs] + [r["placeholders"] for r in rs] + [1]) * 1.05
        ax.plot([0, hi], [0, hi], color=AXIS, lw=1, zorder=1)
        for lang in ("en", "es"):
            c = Counter((r["g_placeholders"], r["placeholders"]) for r in rs if r["lang"] == lang)
            pts = list(c)
            ax.scatter([a for a, _ in pts], [b for _, b in pts], s=[26 + 14 * (c[k] - 1) ** 0.8 * 3 for k in pts],
                       marker=LANG_MARK[lang], color=LANG[lang], alpha=0.55, edgecolor=SURFACE,
                       linewidth=1, zorder=3)
        worst = sorted([r for r in rs if off_by(r) != 0], key=lambda r: -abs(off_by(r)))[:4]
        for r in worst:
            ax.annotate(r["data_id"].replace("TAB-", "").replace("PANORAMA-", "") + " " + r["lang"],
                        (r["g_placeholders"], r["placeholders"]), xytext=(5, 5), textcoords="offset points",
                        fontsize=7, color=INK2)
        style(ax, None, "gold placeholders", "judged placeholders" if ci == 0 else None)
        ax.set_xlim(0, hi)
        ax.set_ylim(0, hi)
    lang_legend(fig)
    fig.text(0.01, -0.01, "Top: how many records the judge was off by 0, 1, 2 or 3+ placeholders. Bottom: one bubble per distinct "
             "(gold, judged) pair, bigger = more records; the four largest misses are named.", color=MUTED, fontsize=8)
    fig.suptitle("Did the judge count the same number of placeholders as the gold annotation?", x=0.01, ha="left",
                 fontsize=12, fontweight="bold", y=1.02)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    return save(fig, figdir, "placeholders_vs_gold.png")


def fig_recall_hist(rows, figdir):
    fig, axes = plt.subplots(1, 3, figsize=(11, 3.4), sharey=True)
    bins = [i / 10 for i in range(0, 11)]
    for ax, cond in zip(axes, COND_ORDER):
        w = 0.035
        for off, lang in ((-w / 2 - 0.002, "en"), (w / 2 + 0.002, "es")):
            vals = [r["recall"] for r in rows if r["cond"] == cond and r["lang"] == lang and r["recall"] is not None]
            cnt = Counter(min(int(v * 10), 9) for v in vals)
            ax.bar([b / 10 + 0.05 + off for b in range(10)], [cnt.get(b, 0) for b in range(10)], width=w,
                   color=LANG[lang], zorder=3)
        style(ax, COND_NAME[cond], "judged mention recall", "records" if cond == COND_ORDER[0] else None)
        ax.set_xticks(bins)
        ax.set_xticklabels(["0", "", ".2", "", ".4", "", ".6", "", ".8", "", "1"])
        ax.set_xlim(0, 1.0)
        ax.yaxis.get_major_locator().set_params(integer=True)
    lang_legend(fig)
    fig.suptitle("How is judged recall distributed?  (1.0 is counted in the last bar)", x=0.01, ha="left",
                 fontsize=12, fontweight="bold", y=1.04)
    return save(fig, figdir, "recall_histogram.png")


def fig_strip(rows, figdir):
    fig, axes = plt.subplots(2, 2, figsize=(10.5, 6.6), sharex=True)
    for ci, corpus in enumerate(CORPORA):
        for ri, (field, label) in enumerate((("recall", "judged mention recall"), ("n_leak", "judged leaks per record"))):
            ax = axes[ri][ci]
            for xi, cond in enumerate(COND_ORDER):
                for lang, dx in (("en", -0.16), ("es", 0.16)):
                    ys = [r[field] for r in rows if r["cond"] == cond and r["corpus"] == corpus
                          and r["lang"] == lang and r[field] is not None]
                    n = len(ys)
                    jit = [dx + ((i % 5) - 2) * 0.025 for i in range(n)]
                    ax.scatter([xi + j for j in jit], ys, s=26, marker=LANG_MARK[lang], color=LANG[lang],
                               alpha=0.8, edgecolor=SURFACE, linewidth=0.8, zorder=3)
                    if ys:
                        ax.hlines(mean(ys), xi + dx - 0.13, xi + dx + 0.13, color=INK, lw=2, zorder=4)
            style(ax, "%s%s" % (corpus.upper() if corpus == "tab" else "PANORAMA", "") if ri == 0 else None,
                  None, label if ci == 0 else None)
            ax.set_xticks(range(3))
            ax.set_xticklabels([COND_NAME[c] for c in COND_ORDER])
            if field == "recall":
                ax.set_ylim(-0.05, 1.05)
    lang_legend(fig)
    fig.text(0.01, -0.02, "Black bar = mean of the records in that group.", color=MUTED, fontsize=8)
    fig.suptitle("Does the judge tell the masking conditions apart?", x=0.01, ha="left", fontsize=12,
                 fontweight="bold", y=1.02)
    fig.tight_layout()
    return save(fig, figdir, "condition_strips.png")


def fig_dumbbell(rows, figdir, field, label, fname, title):
    ps = pairs(rows)
    fig, axes = plt.subplots(3, 2, figsize=(10.5, 9), sharex="row")
    for ri, cond in enumerate(COND_ORDER):
        for ci, corpus in enumerate(CORPORA):
            ax = axes[ri][ci]
            items = sorted([(k, v) for k, v in ps.items() if k[0] == corpus and k[1] == cond],
                           key=lambda kv: kv[0][2])
            for yi, (k, v) in enumerate(items):
                a, b = v["en"][field], v["es"][field]
                if a is None or b is None:
                    continue
                ax.plot([a, b], [yi, yi], color=AXIS, lw=2, zorder=2)
                # English larger underneath, Spanish smaller on top, so equal values show both
                for lang, val, size in (("en", a, 95), ("es", b, 34)):
                    ax.scatter(val, yi, s=size, marker=LANG_MARK[lang], color=LANG[lang],
                               edgecolor=SURFACE, linewidth=1, zorder=3 if lang == "en" else 4)
            ax.set_yticks(range(len(items)))
            ax.set_yticklabels([k[2].replace("TAB-", "").replace("PANORAMA-", "") for k, _ in items])
            ax.invert_yaxis()
            style(ax, "%s · %s" % (corpus.upper() if corpus == "tab" else "PANORAMA", COND_NAME[cond]),
                  label if ri == 2 else None)
            if field == "recall":
                ax.set_xlim(-0.03, 1.03)
            ax.grid(axis="y", visible=False)
    lang_legend(fig)
    fig.suptitle(title, x=0.01, ha="left", fontsize=12, fontweight="bold", y=1.0)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    return save(fig, figdir, fname)


def fig_diff(rows, figdir):
    ps = pairs(rows)
    metrics = (("recall", "mention recall"), ("n_leak", "leaks"), ("placeholders", "placeholders"))
    fig, axes = plt.subplots(1, 3, figsize=(11, 3.6))
    for ax, (field, label) in zip(axes, metrics):
        for yi, cond in enumerate(COND_ORDER):
            diffs = [v["es"][field] - v["en"][field] for k, v in ps.items()
                     if k[1] == cond and v["es"][field] is not None and v["en"][field] is not None]
            diffs.sort()
            jit = [((i % 5) - 2) * 0.05 for i in range(len(diffs))]
            ax.scatter(diffs, [yi + j for j in jit], s=24, color="#6250d6", alpha=0.75,
                       edgecolor=SURFACE, linewidth=0.8, zorder=3)
            if diffs:
                ax.vlines(mean(diffs), yi - 0.3, yi + 0.3, color=INK, lw=2, zorder=4)
        ax.axvline(0, color=INK2, lw=1, zorder=2)
        ax.set_yticks(range(3))
        ax.set_yticklabels([COND_NAME[c] for c in COND_ORDER] if ax is axes[0] else [])
        ax.invert_yaxis()
        style(ax, label, "Spanish − English, per record")
        ax.grid(axis="y", visible=False)
    fig.text(0.01, -0.02, "Each dot is one record judged in both languages; black bar = mean difference; 0 = identical.",
             color=MUTED, fontsize=8)
    fig.suptitle("How much does the verdict change between languages, record by record?", x=0.01, ha="left",
                 fontsize=12, fontweight="bold", y=1.04)
    return save(fig, figdir, "language_differences.png")


def fig_error_map(rows, figdir, cond):
    fig, axes = plt.subplots(1, 2, figsize=(11, 5.4))
    for ax, corpus in zip(axes, CORPORA):
        rs = [r for r in rows if r["cond"] == cond and r["corpus"] == corpus]
        ids = sorted({r["data_id"] for r in rs})
        y = 0
        ticks, labels = [], []
        for did in ids:
            for lang in ("en", "es"):
                r = next((x for x in rs if x["data_id"] == did and x["lang"] == lang), None)
                if r is None:
                    continue
                ax.hlines(y, 0, 1, color=GRID, lw=5, zorder=1, capstyle="round")
                for verdict, s, e in r["offsets"]:
                    color, mk = VERDICT[verdict]
                    ax.scatter((s + e) / 2 / max(r["text_len"], 1), y, s=26, marker=mk, color=color,
                               edgecolor=SURFACE, linewidth=0.6, zorder=3, alpha=0.95)
                ticks.append(y)
                labels.append("%s %s" % (did.replace("TAB-", "").replace("PANORAMA-", ""), lang))
                y += 1
            y += 0.5
        ax.set_yticks(ticks)
        ax.set_yticklabels(labels, fontsize=7)
        ax.invert_yaxis()
        ax.set_xlim(-0.01, 1.01)
        ax.grid(axis="y", visible=False)
        style(ax, "TAB" if corpus == "tab" else "PANORAMA", "position in the text (0 = start, 1 = end)")
    hs = [Line2D([0], [0], marker=mk, color="none", markerfacecolor=c, markeredgecolor=SURFACE,
                 markersize=8, label=v) for v, (c, mk) in VERDICT.items()]
    fig.legend(handles=hs, loc="upper right", ncol=4, bbox_to_anchor=(0.99, 1.0))
    fig.suptitle("Where in the text did the judge find problems?  ·  %s" % COND_NAME[cond], x=0.01, ha="left",
                 fontsize=12, fontweight="bold", y=1.0)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    return save(fig, figdir, "error_map_%s.png" % cond)


def fig_keywords(rows, figdir):
    cols = [(c, l) for c in COND_ORDER for l in ("en", "es")]
    cnt = {col: Counter() for col in cols}
    n = Counter()
    for r in rows:
        n[(r["cond"], r["lang"])] += 1
        for k in set(r["keywords"]):
            cnt[(r["cond"], r["lang"])][k] += 1
    total = Counter()
    for c in cnt.values():
        total.update(c)
    kws = [k for k, _ in total.most_common()]
    if not kws:
        return None
    grid = [[cnt[col][k] for col in cols] for k in kws]
    fig, ax = plt.subplots(figsize=(8.2, 0.42 * len(kws) + 1.8))
    from matplotlib.colors import LinearSegmentedColormap
    cmap = LinearSegmentedColormap.from_list("seq", SEQ)
    top = max(max(r) for r in grid) or 1
    ax.imshow(grid, cmap=cmap, vmin=0, vmax=top, aspect="auto")
    for i, row in enumerate(grid):
        for j, v in enumerate(row):
            ax.text(j, i, str(v), ha="center", va="center", fontsize=8,
                    color="#fcfcfb" if v / top > 0.5 else INK)
    ax.set_xticks(range(len(cols)))
    ax.set_xticklabels(["%s\n%s\n(n=%d)" % (COND_NAME[c], l, n[(c, l)]) for c, l in cols], fontsize=8)
    ax.set_yticks(range(len(kws)))
    ax.set_yticklabels(kws)
    ax.xaxis.tick_top()
    ax.grid(False)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.tick_params(length=0)
    for x in (1.5, 3.5):
        ax.axvline(x, color=SURFACE, lw=3)
    fig.suptitle("Which findings does the judge flag?  (records carrying each keyword)", x=0.01, ha="left",
                 fontsize=12, fontweight="bold", y=1.0 + 0.0)
    return save(fig, figdir, "keywords.png")


# ---------------------------------------------------------------- tables
def table(head, body, cls=""):
    th = "".join("<th>%s</th>" % h for h in head)
    tr = "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % c for c in r) for r in body)
    return '<div class="scroll"><table class="%s"><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>' % (cls, th, tr)


def summary_table(rows):
    body = []
    for cond in COND_ORDER:
        for corpus in CORPORA:
            for lang in ("en", "es"):
                rs = [r for r in rows if r["cond"] == cond and r["corpus"] == corpus and r["lang"] == lang]
                if not rs:
                    continue
                recs = [r["recall"] for r in rs]
                gaps = [abs(r["recall"] - r["g_recall"]) for r in rs if r["recall"] is not None and r["g_recall"] is not None]
                body.append([COND_NAME[cond], corpus, lang, len(rs),
                             f3(mean([r["placeholders"] for r in rs]), 1), f3(mean([r["g_placeholders"] for r in rs]), 1),
                             "%d / %d" % (sum(off_by(r) == 0 for r in rs), sum(off_by(r) is not None for r in rs)),
                             f3(mean([r["n_leak"] for r in rs]), 1), f3(mean([r["g_leaks"] for r in rs]), 1),
                             f3(mean(recs)), f3(mean([r["g_recall"] for r in rs])), f3(mean(gaps)),
                             f3(mean([r["consistency"] for r in rs])), f3(mean([r["n_over"] for r in rs]), 1)])
    return table(["condition", "corpus", "lang", "n", "placeholders", "gold", "count exact", "leaks", "gold leaks", "recall",
                  "gold recall", "mean |gap|", "consistency", "over"], body, "num")


def miss_table(rows, top=25):
    miss = sorted([r for r in rows if off_by(r)], key=lambda r: (-abs(off_by(r)), r["data_id"]))
    total = sum(1 for r in rows if off_by(r) is not None)
    if not miss:
        return "<p>The judge's placeholder count matched the gold count in all %d judgments.</p>" % total
    body = [[COND_NAME[r["cond"]], r["corpus"], r["lang"], r["data_id"], f3(r["placeholders"], 0),
             r["g_placeholders"], "%+d" % off_by(r)] for r in miss[:top]]
    note = "<p>%d of %d judgments were off (%.1f%%)%s.</p>" % (
        len(miss), total, 100.0 * len(miss) / total,
        "; showing the %d largest misses" % top if len(miss) > top else "")
    return note + table(["condition", "corpus", "lang", "record", "judged", "gold", "judged − gold"], body, "num")


def agreement_table(rows):
    ps = pairs(rows)
    body = []
    for label, sel in [("all", lambda k: True)] + [(COND_NAME[c], (lambda c: lambda k: k[1] == c)(c)) for c in COND_ORDER]:
        sub = {k: v for k, v in ps.items() if sel(k)}
        if not sub:
            continue
        def d(f):
            return [v["es"][f] - v["en"][f] for v in sub.values() if v["es"][f] is not None and v["en"][f] is not None]
        dr, dl, dp = d("recall"), d("n_leak"), d("placeholders")
        same = mean([1.0 if all(v["en"][f] == v["es"][f] for f in ("n_ok", "n_wrong_label", "n_over", "n_partial", "n_leak")) else 0.0
                     for v in sub.values()])
        body.append([label, len(sub), f3(mean(dr)), "%d / %d / %d" % (sum(x > 0 for x in dr), sum(x == 0 for x in dr), sum(x < 0 for x in dr)),
                     f3(mean(dl), 2), f3(mean(dp), 2),
                     f3(corr([v["en"]["recall"] for v in sub.values()], [v["es"]["recall"] for v in sub.values()]), 2),
                     f3(same, 2)])
    return table(["pairs", "n", "mean Δ recall (es−en)", "es higher / equal / lower", "mean Δ leaks",
                  "mean Δ placeholders", "recall correlation", "identical verdict counts"], body, "num")


def record_table(rows, cond, corpus):
    ps = pairs([r for r in rows if r["cond"] == cond and r["corpus"] == corpus])
    body = []
    for k, v in sorted(ps.items(), key=lambda kv: kv[0][2]):
        e, s = v["en"], v["es"]
        def mark(a, b):
            return ' class="diff"' if a != b else ""
        body.append([k[2], k[3].replace("annotator", "a"),
                     "%s / %s / %s" % (f3(e["placeholders"], 0), f3(s["placeholders"], 0), e["g_placeholders"]),
                     "%s / %s / %s" % (f3(e["n_leak"], 0), f3(s["n_leak"], 0), "–" if e["g_leaks"] is None else e["g_leaks"]),
                     "%s / %s / %s" % (f3(e["recall"]), f3(s["recall"]), f3(e["g_recall"])),
                     "%s / %s" % (f3(e["consistency"]), f3(s["consistency"])),
                     html.escape(", ".join(e["keywords"]) or "–"), html.escape(", ".join(s["keywords"]) or "–")])
    return table(["record", "ann.", "placeholders<br>en / es / gold", "leaks<br>en / es / gold",
                  "recall<br>en / es / gold", "consistency<br>en / es", "keywords en", "keywords es"], body, "num rec")


def b3_problem_table(b3_rows):
    body = []
    for c in b3_rows:
        try:
            p, r = float(c["p"]), float(c["r"])
        except ValueError:
            continue
        if p >= 0.9995 and r >= 0.9995:
            continue
        kind = " + ".join(x for x, bad in (("merge", p < 0.9995), ("split", r < 0.9995)) if bad)
        body.append([c["run"].replace("masked_", ""), c["doc_id"], html.escape(c["entity"]), html.escape(c["identity"]),
                     c["n"], c["entity_total"], c["identity_total"], c["p"], c["r"], kind])
    if not body:
        return "<p>No cell has p or r below 1: no splits or merges in these records.</p>"
    return table(["run", "record", "entity", "identity", "n", "E", "I", "p", "r", "problem"], body, "num")


def disagreement_blocks(rows, top=6):
    ps = pairs(rows)
    scored = sorted(ps.items(), key=lambda kv: -(abs((kv[1]["es"]["n_leak"] or 0) - (kv[1]["en"]["n_leak"] or 0)) * 10
                                                  + abs((kv[1]["es"]["placeholders"] or 0) - (kv[1]["en"]["placeholders"] or 0))))
    out = []
    for k, v in scored[:top]:
        e, s = v["en"], v["es"]
        out.append('<div class="pair"><h4>%s · %s · %s</h4><div class="two">%s</div></div>' % (
            html.escape(k[2]), k[0], COND_NAME[k[1]],
            "".join('<div><strong>%s</strong> · %s placeholders, %s leaks, recall %s<br><small>keywords: %s</small><p>%s</p></div>' % (
                l.upper(), f3(r["placeholders"], 0), f3(r["n_leak"], 0), f3(r["recall"]),
                html.escape(", ".join(r["keywords"]) or "none"), html.escape(r["notes"]))
                for l, r in (("en", e), ("es", s)))))
    return "".join(out)


# ---------------------------------------------------------------- page
CSS = """
:root{--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--line:#e1e0d9;--accent:#2a78d6;--warn:#fff4d6}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--line:#2c2c2a;--accent:#3987e5;--warn:#3a3217}}
:root[data-theme="dark"]{--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--line:#2c2c2a;--accent:#3987e5;--warn:#3a3217}
*{box-sizing:border-box}body{margin:0;background:var(--page);color:var(--ink);font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1100px;margin:0 auto;padding:32px 16px 64px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:19px;margin:44px 0 6px;padding-top:8px;border-top:1px solid var(--line)}h3{font-size:15px;margin:26px 0 6px}h4{margin:0 0 6px;font-size:14px}
p,li{color:var(--ink2)}.lede{font-size:16px;max-width:760px}
.note{background:var(--warn);border-radius:8px;padding:10px 14px;color:var(--ink)}
figure{margin:14px 0 6px;background:#fcfcfb;border:1px solid var(--line);border-radius:10px;padding:10px;overflow:hidden}
figure img{display:block;width:100%;height:auto}figcaption{font-size:13px;color:#52514e;padding:6px 4px 2px}
.scroll{overflow-x:auto;margin:10px 0}table{border-collapse:collapse;width:100%;font-size:13px}
th,td{padding:6px 9px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}th{color:var(--ink2);font-weight:600;vertical-align:bottom}
table.num td:nth-child(n+4){font-variant-numeric:tabular-nums}table.rec td:nth-child(n+7){white-space:normal}
.pair{border:1px solid var(--line);background:var(--surface);border-radius:10px;padding:12px 14px;margin:10px 0}
.two{display:grid;grid-template-columns:1fr 1fr;gap:16px}.two p{margin:6px 0 0;font-size:13px}
@media (max-width:700px){.two{grid-template-columns:1fr}}
code{background:var(--line);padding:1px 5px;border-radius:4px;font-size:13px}
"""


def fig_html(src, alt, caption):
    if not src:
        return ""
    return '<figure><img src="%s" alt="%s"><figcaption>%s</figcaption></figure>' % (src, html.escape(alt), caption)


def build(rows, b3_rows, outdir):
    figdir = os.path.join(outdir, "figs")
    os.makedirs(figdir, exist_ok=True)
    n_runs = len({r["run"] for r in rows})
    per = Counter(r["run"] for r in rows)
    fig = {}
    fig["ph"] = fig_placeholders(rows, figdir)
    fig["hist"] = fig_recall_hist(rows, figdir)
    fig["strip"] = fig_strip(rows, figdir)
    fig["dr"] = fig_dumbbell(rows, figdir, "recall", "judged mention recall", "pairs_recall.png",
                             "Same record, two languages: judged mention recall")
    fig["dl"] = fig_dumbbell(rows, figdir, "n_leak", "judged leaks", "pairs_leaks.png",
                             "Same record, two languages: judged leaks")
    fig["diff"] = fig_diff(rows, figdir)
    fig["map"] = {c: fig_error_map(rows, figdir, c) for c in COND_ORDER}
    fig["kw"] = fig_keywords(rows, figdir)

    p = []
    p.append("<h1>Judge analysis: English against Spanish</h1>")
    p.append('<p class="lede">%d runs, %d judgments (%s records per run). The English and Spanish files hold the same records, '
             'so every language comparison below is paired record by record. “Gold” is the dataset’s own annotation, which the judge never sees.</p>'
             % (n_runs, len(rows), "/".join(str(x) for x in sorted(set(per.values())))))
    p.append('<p class="note"><strong>Small sample.</strong> Each run has only the first %s records, so differences are suggestive, '
             'not conclusive. Layout pass: rerun on the full data to get numbers worth reporting.</p>' % max(per.values()))

    p.append("<h2>1. Summary by condition and language</h2>")
    p.append("<p>Averages per group, with the gold values beside them. <code>gold leaks</code> and <code>gold recall</code> "
             "are known only for <code>full</code> (0 leaks) and <code>direct only</code> (from its full twin); the partial condition has no gold leak count.</p>")
    p.append(summary_table(rows))

    p.append("<h2>2. Per record, one language at a time</h2>")
    p.append(fig_html(fig["ph"], "Scatter of judged against gold placeholder counts, one panel per condition",
                      "Compares the number of placeholders only: the judge’s count against the gold count. An exact match means the same total, "
                      "not proof that it found the same placeholders."))
    p.append("<h3>Records where the placeholder count was off</h3>")
    p.append(miss_table(rows))
    p.append(fig_html(fig["hist"], "Histogram of judged mention recall per condition and language",
                      "Number of records per recall band. Under the full condition every leak the judge reports is a false alarm."))
    p.append(fig_html(fig["strip"], "Strip plots of recall and leak counts by condition, language and corpus",
                      "Each point is one record. A judge that sees the conditions should show recall falling and leaks rising from full to direct only."))
    p.append("<h3>Where the problems are in the text</h3>")
    p.append("<p>Built from <code>error_offsets</code>: each mark is one problem, placed by its relative position in the record. "
             "Offsets come from the judge counting characters, so positions are approximate.</p>")
    for c in COND_ORDER:
        p.append(fig_html(fig["map"][c], "Error map for the %s condition" % COND_NAME[c],
                          "Condition: %s. One row per record and language." % COND_NAME[c]))

    p.append("<h2>3. Comparing the languages</h2>")
    p.append(agreement_table(rows))
    p.append(fig_html(fig["diff"], "Per-record difference between Spanish and English for recall, leaks and placeholders",
                      "Dots at 0 mean both languages got the same result for that record."))
    p.append(fig_html(fig["dr"], "Dumbbell chart of recall per record, English against Spanish",
                      "Short bars: the judge treats the two languages alike. Long bars are the records worth reading."))
    p.append(fig_html(fig["dl"], "Dumbbell chart of leak counts per record, English against Spanish", "Same view for leak counts."))
    p.append("<h3>Findings flagged by the judge</h3>")
    p.append(fig_html(fig["kw"], "Heatmap of keyword counts by condition and language",
                      "Count of records carrying each keyword from the skill’s vocabulary."))
    p.append("<h3>Records where the two languages differ most</h3>")
    p.append("<p>Ranked by the change in leak count, then in placeholder count. The notes are the judge’s own and hold no original text.</p>")
    p.append(disagreement_blocks(rows))

    p.append("<h2>4. Consistency problems (B³)</h2>")
    p.append("<p>Only cells with <code>p</code> or <code>r</code> below 1 are listed: <code>p</code> below 1 is a merge "
             "(two entities share a tag), <code>r</code> below 1 is a split (one entity carries two tags). The full cell tables are in each run’s <code>b3.csv</code>.</p>")
    p.append(b3_problem_table(b3_rows))

    p.append("<h2>5. All records</h2>")
    for cond in COND_ORDER:
        for corpus in CORPORA:
            p.append("<h3>%s · %s</h3>" % (COND_NAME[cond], corpus))
            p.append(record_table(rows, cond, corpus))

    page = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            "<title>Judge analysis</title><style>%s</style></head><body><main>%s</main></body></html>" % (CSS, "\n".join(p)))
    path = os.path.join(outdir, "report.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default="runs")
    ap.add_argument("--data", default="v.1")
    ap.add_argument("--out", default="report")
    a = ap.parse_args()
    rows, b3_rows = load_runs(a.runs, a.data)
    if not rows:
        raise SystemExit("no judged runs found in %s" % a.runs)
    print("%d judgments from %d runs" % (len(rows), len({r["run"] for r in rows})))
    print("wrote", build(rows, b3_rows, a.out))


if __name__ == "__main__":
    main()
