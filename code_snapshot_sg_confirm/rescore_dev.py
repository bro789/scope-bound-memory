"""Re-score stored dev results with the current scorer (no model calls).

Also reports S with one post-hoc interface repair (strip [ ] around conditions), labelled separately.
Usage: python rescore_dev.py RESULTS_JSON QUESTIONS_JSONL VIEWS_JSONL OUT_JSON
"""
import collections
import json
import re
import sys

import run_scope_experiments as R
from scope_memory import ScopeMemory


def main():
    res_path, q_path, v_path, out_path = sys.argv[1:5]
    rows = json.load(open(res_path))
    Q = {json.loads(l)["qid"]: json.loads(l) for l in open(q_path)}
    hay = {t for q in Q.values() for t in q["haystack"]}
    mem = ScopeMemory([json.loads(l) for l in open(v_path) if json.loads(l)["trajectory"] in hay])
    tot = collections.defaultdict(collections.Counter)
    by_type = collections.defaultdict(collections.Counter)
    for x in rows:
        q = Q[x["qid"]]
        k = "suff" if q["gold"]["source_sufficient"] else "insuff"
        preds = {a: x[a]["pred"] for a in ("B0", "B1", "B2", "S")}
        it = dict(x["S"]["intent"] or {})
        it["conditions"] = [re.sub(r"^\[(.*)\]$", r"\1", c.strip()) for c in (it.get("conditions") or [])]
        try:
            preds["S_bracketfix"] = R.executor_to_pred(mem.answer(it)) if x["S"]["intent"] else None
        except Exception:
            preds["S_bracketfix"] = None
        for a, p in preds.items():
            sc = R.score_dev(p, q)
            tot[a][k + "_correct"] += sc["correct"]
            tot[a][k + "_n"] += 1
            for f in ("unsupported", "wrong_refusal", "parse_fail"):
                tot[a][f] += sc[f]
            by_type[a][f'{q["type"]}:{k}:{"ok" if sc["correct"] else "X"}'] += 1
    out = {"totals": {a: dict(c) for a, c in tot.items()}, "by_type": {a: dict(sorted(c.items())) for a, c in by_type.items()}}
    json.dump(out, open(out_path, "w"), indent=1)
    for a, c in tot.items():
        print(a, f'suff {c["suff_correct"]}/{c["suff_n"]} insuff {c["insuff_correct"]}/{c["insuff_n"]} '
                 f'unsupported {c["unsupported"]} wrong_refusal {c["wrong_refusal"]} parse_fail {c["parse_fail"]}')


if __name__ == "__main__":
    main()
