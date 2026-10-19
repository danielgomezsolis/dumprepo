#!/usr/bin/env bash
# Run run_judge.py on every masked JSONL under v.1/Masked*/, one output folder per file:
#   runs/<file name without .jsonl>/
# Re-running resumes each folder (run_judge.py skips calls already done).
#
# Usage:  ./run_all.sh [extra run_judge.py options]
#   ./run_all.sh --model opus --reps 3 --jobs 4
#   ./run_all.sh --limit 5                      # pilot on 5 records per file
# Env:    SKILL (default SKILL.md)  POLICY_FILE (optional)  DATA_DIR (default v.1)  RUNS_DIR (default runs)
set -u
cd "$(dirname "$0")"

SKILL="${SKILL:-SKILL.md}"
DATA_DIR="${DATA_DIR:-v.1}"
RUNS_DIR="${RUNS_DIR:-runs}"

policy=()
[ -n "${POLICY_FILE:-}" ] && policy=(--policy-file "$POLICY_FILE")

shopt -s nullglob
files=("$DATA_DIR"/Masked*/*.jsonl)
[ ${#files[@]} -eq 0 ] && { echo "no files in $DATA_DIR/Masked*/" >&2; exit 1; }

failed=()
for f in "${files[@]}"; do
    name="$(basename "$f" .jsonl)"
    echo "=== $name -> $RUNS_DIR/$name"
    python3 run_judge.py "$f" --skill "$SKILL" "${policy[@]}" --limit 10 \
        --tool "$name" --out "$RUNS_DIR/$name" "$@" || failed+=("$name")
done

if [ ${#failed[@]} -gt 0 ]; then
    echo "failed: ${failed[*]}" >&2
    exit 1
fi
echo "all ${#files[@]} files done"
