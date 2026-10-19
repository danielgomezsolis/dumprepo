#!/usr/bin/env python3
"""
Run the anonymization-metrics skill as an isolated LLM judge: one record, one fresh
Claude Code call, one CSV line.

Isolation per call:
  - a new `claude -p` process with --no-session-persistence (nothing saved, nothing resumed);
  - an empty temporary working directory (no project CLAUDE.md or files to read);
  - the skill passed as the system prompt; other skills disabled;
  - no tools and no MCP servers, so the judge can only read (no code, no NLP);
  - only the anonymized `text` is sent; `text_original`, `placeholders`, `subjects`
    and the masking condition never leave this script;
  - an opaque doc_id (r00001-1), so the ID gives nothing away;
  - shuffled order.
  With --bare (needs ANTHROPIC_API_KEY), Claude Code also skips auto-memory, hooks and
  CLAUDE.md discovery, including your user-level ~/.claude/CLAUDE.md.

Usage:
  python3 run_judge.py dataset.jsonl [more.jsonl ...] --skill SKILL.md --policy-file policy_tab.txt \
      --tool "gold-masking" --model opus --reps 3 --jobs 4 --out runs/pilot

Outputs in --out:
  judge.csv     the skill's 35-column lines (header once), exactly as §8.2 of the skill,
                with doc_id = the record's own data_id (the judge only sees an opaque id)
  key.csv       opaque call id (rNNNNN-rep) -> source line, data_id, language, masking_condition, annotator, rep
  b3.csv        the B³ cells (§8.6 of the skill) behind each consistency figure, with call_id
  validity.csv  per call: format and arithmetic checks, attempts, cost, seconds, error
  raw/          every response, for audit

Re-running the same command resumes: doc_ids already in judge.csv are skipped.
Standard library only.
"""
import argparse
import csv
import datetime as dt
import io
import json
import os
import random
import shutil
import re
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

HEADER = ("eval_date,doc_id,language,tool,judge_model,mode,policy,scope,style,placeholders,"
          "entities,n_ok,n_wrong_label,n_over,n_partial,n_leak,n_keep,n_low_conf,precision,"
          "mention_recall,entity_recall,b3_p,b3_r,consistency,identity_coverage,f1,"
          "label_agreement,risk_singling_out,risk_linkability,risk_inference,"
          "exposed_categories,over_categories,keywords,notes,error_offsets").split(",")

B3_HEADER = ("eval_date,doc_id,tool,judge_model,entity,identity,n,entity_total,"
             "identity_total,p,r").split(",")

KEYWORDS = {"leak-surname", "leak-repeat-mention", "partial-in-tag", "split", "merge",
            "signer-declarant", "wrong-label", "overmask-city", "overmask-authority",
            "overmask-common-word", "generalization-ok", "year-kept", "low-context",
            "surrogate-unclear", "text-rewritten", "numbering-reset"}

PROMPT = """Evaluation run of the anonymization-metrics skill in your system prompt.

Settings for this run:
- mode: output_only (you have the anonymized text only)
- counter scope: document
- policy: {policy_name}
- tool: {tool}
- eval_date: {date}
- doc_id: {doc_id}
- language: {language}
- judge_model: {model}
{policy_block}
This is an unattended run. Ask no questions and write no report. Apply §2 to §5 and §8 of the skill to the record below, and check the line against §8.4 before answering. The record is the only document: there are no others.

Reply with two ```csv code blocks and nothing outside them:
1. exactly two lines, the §8.2 header and this document's data line;
2. the §8.6 B³ table: its header, then one line per cell (only the header if consistency is n/a).

<record>
{text}
</record>
"""


# ---------------------------------------------------------------- loading
def load_records(path, text_field="text"):
    """JSONL, a JSON list, or a CSV with a text column. Returns a list of dicts."""
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    items = []
    if path.lower().endswith(".csv"):
        for row in csv.DictReader(io.StringIO(raw)):
            items.append({"text": row[text_field], "metadata": row})
    else:
        s = raw.strip()
        if s.startswith("["):
            items = json.loads(s)
        else:
            items = [json.loads(line) for line in raw.splitlines() if line.strip()]
    records = []
    for i, it in enumerate(items, 1):
        meta = it.get("metadata", {}) or {}
        records.append({
            "line": i,
            "text": it[text_field] if text_field in it else it["text"],
            "data_id": str(meta.get("data_id", it.get("id", i))),
            "language": str(meta.get("language", it.get("language", ""))),
            "masking_condition": str(meta.get("masking_condition", "")),
            "annotator": str(meta.get("annotator", "")),
            "item": it,
        })
    return records


# ---------------------------------------------------------------- checks
def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def check_offsets(d, n, text_len):
    """error_offsets: 'verdict:start-end' entries, ordered, in range, one per problem row."""
    entries = [e for e in d.get("error_offsets", "").split(";") if e]
    if len(entries) != n["n_leak"] + n["n_partial"] + n["n_wrong_label"] + n["n_over"]:
        return False
    last = -1
    for e in entries:
        m = re.fullmatch(r"(leak|partial|wrong-label|over):(\d+)-(\d+)", e)
        if not m:
            return False
        start, end = int(m.group(2)), int(m.group(3))
        if not (last <= start < end <= text_len):
            return False
        last = start
    return True


def check_line(d, text_len):
    """Arithmetic and vocabulary checks on one parsed line. Returns dict of booleans."""
    c = {}
    n = {k: num(d[k]) for k in ("placeholders", "n_ok", "n_wrong_label", "n_over",
                                "n_partial", "n_leak")}
    if None in n.values():
        return {"counts_numeric": False}
    c["counts_numeric"] = True
    masked = n["n_ok"] + n["n_wrong_label"] + n["n_over"] + n["n_partial"]
    # ok rows for deleted text don't exist in output-only mode, so the sum must match
    c["placeholder_sum"] = abs(masked - n["placeholders"]) < 0.5
    pd = n["n_ok"] + n["n_wrong_label"] + n["n_partial"]
    p_exp = pd / (pd + n["n_over"]) if pd + n["n_over"] else None
    rd = n["n_ok"] + n["n_wrong_label"] + n["n_partial"] + n["n_leak"]
    r_exp = (n["n_ok"] + n["n_wrong_label"]) / rd if rd else None
    for col, exp in (("precision", p_exp), ("mention_recall", r_exp)):
        got = num(d[col])
        c[col + "_matches"] = (got is None and (exp is None or d[col] == "")) or \
            (got is not None and exp is not None and abs(got - exp) <= 0.0015)
    c["offsets_valid"] = check_offsets(d, n, text_len)
    kws = [k for k in d["keywords"].split(";") if k]
    c["keywords_in_vocab"] = all(k in KEYWORDS for k in kws)
    return c


def csv_blocks(text):
    """Rows of every ```csv block in the reply (the whole reply if there is none)."""
    bodies = re.findall(r"```(?:csv)?\s*\n(.*?)```", text, re.S) or [text]
    return [[r for r in csv.reader(io.StringIO(b.strip())) if r] for b in bodies]


def parse_reply(text):
    """Find the §8.2 line and the §8.6 B3 cells. Returns (dict, cells, error)."""
    d, cells, b3_found = None, [], False
    for rows in csv_blocks(text):
        for i, r in enumerate(rows):
            head = [x.strip() for x in r]
            if head == HEADER and d is None and i + 1 < len(rows):
                data = rows[i + 1]
                if len(data) != len(HEADER):
                    return None, [], "data line has %d fields, expected %d" % (len(data), len(HEADER))
                d = dict(zip(HEADER, [x.strip() for x in data]))
            elif head == B3_HEADER and not b3_found:
                b3_found = True
                for c in rows[i + 1:]:
                    if len(c) != len(B3_HEADER):
                        return None, [], "B3 line has %d fields, expected %d" % (len(c), len(B3_HEADER))
                    cells.append(dict(zip(B3_HEADER, [x.strip() for x in c])))
    if d is None:
        return None, [], "header from §8.2 not found"
    if not b3_found:
        return None, [], "B3 header from §8.6 not found"
    return d, cells, ""


def check_b3(d, cells):
    """The cells must add up to the n-weighted b3_p and b3_r of the main line."""
    if not cells:
        return d["b3_p"] == "" and d["b3_r"] == ""
    try:
        n = [float(c["n"]) for c in cells]
        ok = all(abs(float(c["p"]) - float(c["n"]) / float(c["identity_total"])) <= 0.0015 and
                 abs(float(c["r"]) - float(c["n"]) / float(c["entity_total"])) <= 0.0015
                 for c in cells)
        wp = sum(x * float(c["p"]) for x, c in zip(n, cells)) / sum(n)
        wr = sum(x * float(c["r"]) for x, c in zip(n, cells)) / sum(n)
        return ok and abs(wp - float(d["b3_p"])) <= 0.0015 and abs(wr - float(d["b3_r"])) <= 0.0015
    except (ValueError, ZeroDivisionError):
        return False


# ---------------------------------------------------------------- one call
def call_claude(args, system_prompt, prompt):
    cmd = [args.claude_bin, "-p", "--output-format", "json", "--no-session-persistence",
           "--tools", "", "--strict-mcp-config", "--disable-slash-commands",
           "--system-prompt", system_prompt]
    if args.model:
        cmd += ["--model", args.model]
    if args.effort:
        cmd += ["--effort", args.effort]
    if args.bare:
        cmd += ["--bare"]
    # don't let a call inherit the session of a Claude Code that launched this script
    env = {k: v for k, v in os.environ.items()
           if k not in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_REMOTE_SESSION_ID")}
    with tempfile.TemporaryDirectory(prefix="judge_") as cwd:
        p = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                           cwd=cwd, env=env, timeout=args.timeout)
    if p.returncode != 0 and not p.stdout.strip():
        raise RuntimeError("claude exited %d: %s" % (p.returncode, p.stderr.strip()[:300]))
    try:
        j = json.loads(p.stdout)
        if j.get("is_error"):
            raise RuntimeError("claude error: %s" % str(j.get("result", ""))[:300])
        return j.get("result", ""), j.get("total_cost_usd", ""), j
    except json.JSONDecodeError:
        return p.stdout, "", {}


def judge_one(args, system_prompt, policy_block, rec, rep, date):
    doc_id = "r%05d-%d" % (rec["line"], rep)
    prompt = PROMPT.format(policy_name=args.policy_name, tool=args.tool, date=date,
                           doc_id=doc_id, language=rec["language"] or "unknown",
                           model=args.model or "default", policy_block=policy_block,
                           text=rec["text"])
    t0, last_err, cost_total = time.time(), "", 0.0
    for attempt in range(1, args.retries + 2):
        try:
            reply, cost, meta = call_claude(args, system_prompt, prompt)
        except Exception as e:  # timeout, CLI error
            last_err = str(e)
            continue
        cost_total += float(cost or 0)
        with open(os.path.join(args.out, "raw", "%s_a%d.txt" % (doc_id, attempt)), "w",
                  encoding="utf-8") as f:
            f.write(reply)
        d, cells, err = parse_reply(reply)
        if d is None:
            last_err = err
            continue
        # identification fields are fixed by the run, whatever the judge wrote
        d.update({"eval_date": date, "doc_id": doc_id, "tool": args.tool,
                  "judge_model": args.model or meta.get("model", "default"),
                  "mode": "output_only", "policy": args.policy_name, "scope": "document"})
        if rec["language"]:
            d["language"] = rec["language"]
        checks = check_line(d, len(rec["text"]))
        checks["b3_table_matches"] = check_b3(d, cells)
        for c in cells:
            c.update({"eval_date": date, "tool": args.tool, "judge_model": d["judge_model"]})
        return doc_id, d, checks, cells, attempt, cost_total, time.time() - t0, ""
    return doc_id, None, {}, [], args.retries + 1, cost_total, time.time() - t0, last_err


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dataset", nargs="+", help="one or more JSONL/JSON/CSV files (e.g. the _en and _es files)")
    ap.add_argument("--skill", required=True, help="path to the skill's SKILL.md")
    ap.add_argument("--policy-file", help="policy text given to the judge")
    ap.add_argument("--policy-name", default=None, help="defaults to the file name, or 'default'")
    ap.add_argument("--tool", default="", help="name of the anonymization tool or condition set")
    ap.add_argument("--model", default="", help="e.g. opus, sonnet, or a full model name")
    ap.add_argument("--effort", default="", help="low|medium|high|xhigh|max")
    ap.add_argument("--bare", action="store_true", help="strictest isolation; needs ANTHROPIC_API_KEY")
    ap.add_argument("--reps", type=int, default=1, help="independent judgments per record")
    ap.add_argument("--jobs", type=int, default=2, help="calls in parallel")
    ap.add_argument("--retries", type=int, default=1, help="fresh retries on a bad reply")
    ap.add_argument("--timeout", type=int, default=900, help="seconds per call")
    ap.add_argument("--limit", type=int, default=0, help="only the first N records (pilot)")
    ap.add_argument("--languages", default="", help="comma list to keep, e.g. en,es")
    ap.add_argument("--seed", type=int, default=13)
    ap.add_argument("--text-field", default="text")
    ap.add_argument("--claude-bin", default="claude")
    ap.add_argument("--out", default="runs/run1")
    args = ap.parse_args()

    # calls run in a temporary directory, so resolve the CLI path first
    found = shutil.which(args.claude_bin)
    if not found:
        sys.exit("Claude Code CLI not found: %s" % args.claude_bin)
    args.claude_bin = os.path.abspath(found)
    if args.bare and not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("--bare needs ANTHROPIC_API_KEY (it never reads your Claude login)")
    os.makedirs(os.path.join(args.out, "raw"), exist_ok=True)
    with open(args.skill, encoding="utf-8") as f:
        system_prompt = f.read()
    policy_block = ""
    if args.policy_file:
        with open(args.policy_file, encoding="utf-8") as f:
            policy_block = "\nPolicy for this run:\n<policy>\n%s\n</policy>\n" % f.read().strip()
    if args.policy_name is None:
        args.policy_name = (os.path.splitext(os.path.basename(args.policy_file))[0]
                            if args.policy_file else "default")

    records = []
    for path in args.dataset:
        for r in load_records(path, args.text_field):
            r["line"] = len(records) + 1  # numbering continues across files, so doc_ids stay unique
            r["source_file"] = os.path.basename(path)
            records.append(r)
    if args.languages:
        keep = set(args.languages.split(","))
        records = [r for r in records if r["language"] in keep]
    if args.limit:
        records = records[:args.limit]

    judge_path = os.path.join(args.out, "judge.csv")
    done = set()
    if os.path.exists(judge_path):
        with open(judge_path, encoding="utf-8", newline="") as f:
            rd = csv.reader(f)
            if next(rd, None) != HEADER:
                sys.exit("%s exists with a different header; use another --out" % judge_path)
    key_in = os.path.join(args.out, "key.csv")
    if os.path.exists(key_in):
        with open(key_in, encoding="utf-8", newline="") as f:
            done = {r[0] for r in csv.reader(f) if r and r[0] != "doc_id"}

    tasks = [(r, k) for r in records for k in range(1, args.reps + 1)
             if "r%05d-%d" % (r["line"], k) not in done]
    random.Random(args.seed).shuffle(tasks)
    date = dt.date.today().isoformat()
    print("%d records, %d calls to make (%d already done)" % (len(records), len(tasks), len(done)))

    lock = threading.Lock()
    new_judge = not os.path.exists(judge_path)
    jf = open(judge_path, "a", encoding="utf-8", newline="")
    jw = csv.writer(jf)
    if new_judge:
        jw.writerow(HEADER)
    key_path = os.path.join(args.out, "key.csv")
    kf_new = not os.path.exists(key_path)
    kf = open(key_path, "a", encoding="utf-8", newline="")
    kw = csv.writer(kf)
    if kf_new:
        kw.writerow(["doc_id", "rep", "source_line", "data_id", "language",
                     "masking_condition", "annotator", "source_file"])
    b3_path = os.path.join(args.out, "b3.csv")
    b3_new = not os.path.exists(b3_path)
    bf = open(b3_path, "a", encoding="utf-8", newline="")
    bw = csv.writer(bf)
    if b3_new:
        bw.writerow(["call_id"] + B3_HEADER)
    val_path = os.path.join(args.out, "validity.csv")
    vcols = ["doc_id", "parsed", "counts_numeric", "placeholder_sum", "precision_matches",
             "mention_recall_matches", "keywords_in_vocab", "offsets_valid",
             "b3_table_matches", "attempts", "cost_usd",
             "seconds", "error"]
    vf_new = not os.path.exists(val_path)
    vf = open(val_path, "a", encoding="utf-8", newline="")
    vw = csv.writer(vf)
    if vf_new:
        vw.writerow(vcols)

    ok = bad = 0
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
        futs = {ex.submit(judge_one, args, system_prompt, policy_block, r, k, date): (r, k)
                for r, k in tasks}
        for fut in as_completed(futs):
            r, k = futs[fut]
            doc_id, d, checks, cells, attempts, cost, secs, err = fut.result()
            with lock:
                if d is not None:
                    # the judge only saw the opaque id; the real id goes in the CSV (key.csv keeps both)
                    jw.writerow([r["data_id"] if c == "doc_id" else d[c] for c in HEADER])
                    kw.writerow([doc_id, k, r["line"], r["data_id"], r["language"],
                                 r["masking_condition"], r["annotator"], r["source_file"]])
                    for c in cells:
                        bw.writerow([doc_id] + [r["data_id"] if h == "doc_id" else c[h]
                                                for h in B3_HEADER])
                    ok += 1
                else:
                    bad += 1
                vw.writerow([doc_id, d is not None] +
                            [checks.get(c, "") for c in vcols[2:9]] +
                            [attempts, "%.4f" % cost, "%.0f" % secs, err])
                for fh in (jf, kf, vf, bf):
                    fh.flush()
                print("%-10s %s  %s" % (doc_id, "ok " if d else "BAD", err), flush=True)
    for fh in (jf, kf, vf, bf):
        fh.close()
    print("done: %d lines written, %d failed. Files in %s" % (ok, bad, args.out))


if __name__ == "__main__":
    main()
