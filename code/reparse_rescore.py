"""Re-parse every stored LLM reply with the current parse_json and re-score (no model calls).

Covers the main run (B0/B1/B2/S; S re-executes its re-parsed intent with the frozen executor) and the
evidence-selection run. Reports, for each arm, sufficient/insufficient accuracy, unsupported answers,
wrong refusals and parse failures, plus McNemar S vs each other arm.
Usage: python reparse_rescore.py QUESTIONS MAIN_RESULTS EVSEL_RESULTS VIEWS OUT_JSON
"""
import collections
import json
import sys

import analyze_confirm as A
import run_scope_experiments as R
from scope_memory import ScopeMemory


def main():
    q_path, main_path, ev_path, v_path, out_path = sys.argv[1:6]
    Q = {json.loads(l)["qid"]: json.loads(l) for l in open(q_path)}
    hay = {t for q in Q.values() for t in q["haystack"]}
    mem = ScopeMemory([json.loads(l) for l in open(v_path) if json.loads(l)["trajectory"] in hay])
    correct = collections.defaultdict(dict)
    tot = collections.defaultdict(collections.Counter)

    def add(arm, qid, pred):
        q = Q[qid]
        sc = R.score_dev(pred, q)
        k = "suff" if q["gold"]["source_sufficient"] else "insuff"
        tot[arm][k + "_n"] += 1
        tot[arm][k + "_correct"] += sc["correct"]
        for f in ("unsupported", "wrong_refusal", "parse_fail"):
            tot[arm][f] += sc[f]
        correct[arm][qid] = sc["correct"]

    for x in json.load(open(main_path)):
        for arm in ("B0", "B1", "B2"):
            add(arm, x["qid"], R.parse_json(x[arm]["out"]["text"]))
        intent = R.parse_json(x["S"]["out"]["text"])
        try:
            pred = R.executor_to_pred(mem.answer(intent)) if intent else None
        except Exception:
            pred = None
        add("S", x["qid"], pred)
    for x in json.load(open(ev_path)):
        for arm in ("B1_sel_scope", "B2_sel_scope", "B2_sel_table"):
            add(arm, x["qid"], R.parse_json(x[arm]["out"]["text"]))
    suff = [q for q in Q if Q[q]["gold"]["source_sufficient"]]
    insuff = [q for q in Q if not Q[q]["gold"]["source_sufficient"]]
    mc = {arm: {"insufficient": A.mcnemar([correct["S"][q] for q in insuff], [correct[arm][q] for q in insuff]),
                "sufficient": A.mcnemar([correct["S"][q] for q in suff], [correct[arm][q] for q in suff])}
          for arm in correct if arm != "S"}
    out = {"totals": {a: dict(c) for a, c in tot.items()}, "mcnemar_S_vs": mc}
    json.dump(out, open(out_path, "w"), indent=1)
    for a, c in tot.items():
        print(f'{a:13s} suff {c["suff_correct"]}/{c["suff_n"]}  insuff {c["insuff_correct"]}/{c["insuff_n"]}  '
              f'unsupported {c["unsupported"]}  wrong_refusal {c["wrong_refusal"]}  parse_fail {c["parse_fail"]}')
    for a, m in mc.items():
        print("S vs", a, json.dumps(m))


if __name__ == "__main__":
    main()
