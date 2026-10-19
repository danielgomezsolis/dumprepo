#!/usr/bin/env python3
"""
Compare the judge's CSV lines with the gold annotation in the dataset.

  python3 analyze.py dataset.jsonl --run runs/pilot

The gold side is computed here, by this script, from fields the judge never saw:
`placeholders` (one per masked gold span, with its entity_id) and the metadata.
  gold_placeholders   masked spans in the record
  gold_leaks          spans that needed masking but were left visible. Under "full" this is 0.
                      Under "direct_only" it is masked(full twin) - masked(direct_only), where the
                      twin is the record with the same data_id, annotator and language.
  gold_mention_recall masked / (masked + gold_leaks)
  gold_consistency    B-cubed between the tool's tags and the gold entity_ids

Writes compare.csv (one line per judgment), summary.csv (per language x condition) and
b3_gold.csv (the B-cubed cell table behind gold_consistency, one line per entity x tag cell,
the gold counterpart of the judge's b3.csv), and prints both summaries. Standard library only.
"""
import argparse
import csv
import os
import statistics as st
from collections import defaultdict

from run_judge import load_records, num


def b3_cells(pairs):
    """pairs: list of (entity, identity). Returns the 'Computing B3 by hand' table, one dict
    per (entity, identity) cell: n rows in the cell, E entity total, I identity total, p, r."""
    cell, ent, ide = defaultdict(int), defaultdict(int), defaultdict(int)
    for e, i in pairs:
        cell[(e, i)] += 1
        ent[e] += 1
        ide[i] += 1
    return [{"entity": e, "identity": i, "n": c, "entity_total": ent[e],
             "identity_total": ide[i], "p": c / ide[i], "r": c / ent[e]}
            for (e, i), c in sorted(cell.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1])))]


def b3(pairs):
    """pairs: list of (entity, identity). Returns (p, r, f)."""
    if not pairs:
        return None, None, None
    cells = b3_cells(pairs)
    n = len(pairs)
    p = sum(c["n"] * c["p"] for c in cells) / n
    r = sum(c["n"] * c["r"] for c in cells) / n
    return p, r, 2 * p * r / (p + r)


def gold_for(records, extra=()):
    """extra: records only used to find the 'full' twin of a direct_only record."""
    by_line, masked = {}, {}
    for rec in list(records) + list(extra):
        it = rec["item"]
        ph = it.get("placeholders", []) or []
        meta = it.get("metadata", {}) or {}
        m = meta.get("n_spans_masked", len(ph))
        masked[(rec["data_id"], rec["annotator"], rec["language"], rec["masking_condition"])] = m
        if rec not in records:
            continue
        p, r, f = b3([(x["entity_id"], x["placeholder"]) for x in ph if "entity_id" in x])
        by_line[rec["line"]] = {
            "gold_placeholders": len(ph), "gold_entities": len({x.get("entity_id") for x in ph}),
            "gold_masked": m, "gold_b3_p": p, "gold_b3_r": r, "gold_consistency": f,
        }
    for rec in records:
        g = by_line[rec["line"]]
        cond = rec["masking_condition"]
        leaks = None
        if cond == "full":
            leaks = 0
        elif cond == "direct_only":
            full = masked.get((rec["data_id"], rec["annotator"], rec["language"], "full"))
            if full is not None:
                leaks = max(0, full - g["gold_masked"])
        g["gold_leaks"] = leaks
        g["gold_mention_recall"] = (g["gold_masked"] / (g["gold_masked"] + leaks)
                                    if leaks is not None and g["gold_masked"] + leaks else None)
    return by_line


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def mean(xs):
    xs = [x for x in xs if x is not None]
    return st.mean(xs) if xs else None


def fmt(x, nd=3):
    return "" if x is None else ("%.*f" % (nd, x) if isinstance(x, float) else str(x))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dataset")
    ap.add_argument("--run", required=True)
    ap.add_argument("--twin", nargs="*", default=[],
                    help="other dataset files holding the 'full' twins of a direct_only run, "
                         "e.g. v.1/MaskedFull/masked_full_tab_es.jsonl")
    ap.add_argument("--text-field", default="text")
    a = ap.parse_args()

    records = load_records(a.dataset, a.text_field)
    extra = [x for t in a.twin for x in load_records(t, a.text_field)]
    gold = gold_for(records, extra)
    # judge.csv carries the record's data_id, key.csv the opaque call id; the two files are
    # written together, one line each per judgment, so line i of one is line i of the other
    key_rows = read_csv(os.path.join(a.run, "key.csv"))
    judge = read_csv(os.path.join(a.run, "judge.csv"))
    if len(key_rows) != len(judge) or any(k["data_id"] != j["doc_id"]
                                          for k, j in zip(key_rows, judge)):
        raise SystemExit("judge.csv and key.csv do not line up (older run? rerun into a new --out)")
    for k, j in zip(key_rows, judge):
        j["call_id"] = k["doc_id"]
    key = {j["call_id"]: k for k, j in zip(key_rows, judge)}
    validity = {}
    for v in read_csv(os.path.join(a.run, "validity.csv")):
        validity[v["doc_id"]] = v  # last attempt wins

    rows = []
    for j in judge:
        k = key.get(j["call_id"])
        if not k:
            continue
        g = gold[int(k["source_line"])]
        v = validity.get(j["call_id"], {})
        jr, gr = num(j["mention_recall"]), g["gold_mention_recall"]
        rows.append({
            "doc_id": j["call_id"], "rep": k["rep"], "data_id": k["data_id"],
            "language": k["language"], "condition": k["masking_condition"],
            "annotator": k["annotator"],
            "placeholders": num(j["placeholders"]), "gold_placeholders": g["gold_placeholders"],
            "placeholders_exact": num(j["placeholders"]) == g["gold_placeholders"],
            "n_leak": num(j["n_leak"]), "gold_leaks": g["gold_leaks"],
            "n_over": num(j["n_over"]), "n_wrong_label": num(j["n_wrong_label"]),
            "mention_recall": jr, "gold_mention_recall": gr,
            "recall_abs_err": abs(jr - gr) if jr is not None and gr is not None else None,
            "consistency": num(j["consistency"]), "gold_consistency": g["gold_consistency"],
            "keywords": j["keywords"],
            "line_valid": all(v.get(c) == "True" for c in
                              ("placeholder_sum", "precision_matches",
                               "mention_recall_matches", "keywords_in_vocab")),
        })

    cols = list(rows[0].keys()) if rows else []
    with open(os.path.join(a.run, "compare.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: fmt(r[c]) if isinstance(r[c], float) else r[c] for c in cols})

    # ---- the B3 table behind gold_consistency, once per judged record
    b3_cols = ["source_line", "data_id", "language", "condition", "annotator", "entity",
               "identity", "n", "entity_total", "identity_total", "p", "r"]
    judged = {int(key[r["doc_id"]]["source_line"]) for r in rows}
    by_line = {rec["line"]: rec for rec in records}
    with open(os.path.join(a.run, "b3_gold.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=b3_cols)
        w.writeheader()
        for line in sorted(judged):
            rec = by_line[line]
            ph = rec["item"].get("placeholders", []) or []
            for c in b3_cells([(x["entity_id"], x["placeholder"]) for x in ph if "entity_id" in x]):
                w.writerow({"source_line": line, "data_id": rec["data_id"],
                            "language": rec["language"], "condition": rec["masking_condition"],
                            "annotator": rec["annotator"],
                            **{k: fmt(v) if isinstance(v, float) else v for k, v in c.items()}})

    # ---- per language x condition
    groups = defaultdict(list)
    for r in rows:
        groups[(r["language"], r["condition"])].append(r)
        groups[("ALL", r["condition"])].append(r)
    summ = []
    for (lang, cond), rs in sorted(groups.items()):
        full = cond == "full"
        summ.append({
            "language": lang, "condition": cond, "judgments": len(rs),
            "lines_valid": mean([1.0 if r["line_valid"] else 0.0 for r in rs]),
            "placeholders_exact": mean([1.0 if r["placeholders_exact"] else 0.0 for r in rs]),
            "placeholders_mae": mean([abs(r["placeholders"] - r["gold_placeholders"])
                                      for r in rs if r["placeholders"] is not None]),
            "judged_leaks": mean([r["n_leak"] for r in rs]),
            "gold_leaks": mean([r["gold_leaks"] for r in rs]),
            # under "full" nothing that needs masking is left, so any leak is a false alarm
            "false_leak_share": mean([1.0 if (r["n_leak"] or 0) > 0 else 0.0 for r in rs]) if full else None,
            "judged_over": mean([r["n_over"] for r in rs]),
            "judged_recall": mean([r["mention_recall"] for r in rs]),
            "gold_recall": mean([r["gold_mention_recall"] for r in rs]),
            "recall_mae": mean([r["recall_abs_err"] for r in rs]),
            "judged_consistency": mean([r["consistency"] for r in rs]),
            "gold_consistency": mean([r["gold_consistency"] for r in rs]),
        })

    # ---- discrimination: is direct_only judged worse than its full twin?
    pairs = defaultdict(dict)
    for r in rows:
        pairs[(r["data_id"], r["annotator"], r["language"], r["rep"])][r["condition"]] = r
    disc = defaultdict(list)
    for (_, _, lang, _), d in pairs.items():
        if "full" in d and "direct_only" in d:
            f_, w_ = d["full"]["mention_recall"], d["direct_only"]["mention_recall"]
            if f_ is not None and w_ is not None:
                s = 1.0 if f_ > w_ else 0.5 if f_ == w_ else 0.0
                disc[lang].append(s)
                disc["ALL"].append(s)

    # ---- stability across reps
    reps = defaultdict(list)
    for r in rows:
        reps[(r["data_id"], r["annotator"], r["language"], r["condition"])].append(r)
    sd_recall, sd_leak, kw_jac = [], [], []
    for rs in reps.values():
        if len(rs) < 2:
            continue
        rec = [r["mention_recall"] for r in rs if r["mention_recall"] is not None]
        if len(rec) > 1:
            sd_recall.append(st.pstdev(rec))
        lk = [r["n_leak"] for r in rs if r["n_leak"] is not None]
        if len(lk) > 1:
            sd_leak.append(st.pstdev(lk))
        sets = [set(k for k in r["keywords"].split(";") if k) for r in rs]
        jac = [len(x & y) / len(x | y) if x | y else 1.0
               for i, x in enumerate(sets) for y in sets[i + 1:]]
        kw_jac.append(st.mean(jac))

    with open(os.path.join(a.run, "summary.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(summ[0].keys()) if summ else ["language"])
        w.writeheader()
        for s in summ:
            w.writerow({k: fmt(v) if isinstance(v, float) else v for k, v in s.items()})

    print("\nBy language and condition")
    show = ["language", "condition", "judgments", "lines_valid", "placeholders_exact",
            "judged_leaks", "gold_leaks", "false_leak_share", "judged_recall", "gold_recall",
            "recall_mae", "judged_consistency", "gold_consistency"]
    print("  ".join("%-12s" % c[:12] for c in show))
    for s in summ:
        print("  ".join("%-12s" % fmt(s[c]) for c in show))
    print("\nDiscrimination (direct_only judged worse than its full twin; 1 = always, 0.5 = chance)")
    for lang, ss in sorted(disc.items()):
        print("  %-6s %s over %d pairs" % (lang, fmt(st.mean(ss)), len(ss)))
    if sd_recall or kw_jac:
        print("\nStability across reps (%d records)" % len(kw_jac))
        print("  mean SD of mention recall  %s" % fmt(mean(sd_recall)))
        print("  mean SD of leak count      %s" % fmt(mean(sd_leak)))
        print("  mean keyword Jaccard       %s" % fmt(mean(kw_jac)))
    print("\nFiles: %s, %s, %s" % tuple(os.path.join(a.run, n) for n in
                                      ("compare.csv", "summary.csv", "b3_gold.csv")))


if __name__ == "__main__":
    main()
