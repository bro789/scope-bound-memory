"""Pre-registered analysis of the confirmation run (see confirmation_set_preregistration.md). CPU only, no model calls.

Usage: python analyze_confirm.py RESULTS_JSON QUESTIONS_JSONL DEV_QUESTIONS_JSONL OUT_JSON
"""
import collections
import json
import math
import sys

import run_scope_experiments as R

ARMS = ("B0", "B1", "B2", "S")


def mcnemar(a, b):
    """Exact two-sided McNemar test on paired booleans a (S) vs b (B2)."""
    n10 = sum(x and not y for x, y in zip(a, b))
    n01 = sum(y and not x for x, y in zip(a, b))
    n = n10 + n01
    if n == 0:
        return {"S_only": 0, "B2_only": 0, "p": 1.0}
    k = min(n10, n01)
    p = min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)
    return {"S_only": n10, "B2_only": n01, "p": round(p, 6)}


def main():
    res_path, q_path, dev_path, out_path = sys.argv[1:5]
    rows = json.load(open(res_path))
    Q = {json.loads(l)["qid"]: json.loads(l) for l in open(q_path)}
    dev_tables = {json.loads(l)["scope"]["table"] for l in open(dev_path)}
    sc = {a: {} for a in ARMS + ("S_oracle",)}
    for x in rows:
        q = Q[x["qid"]]
        for a in ARMS:
            sc[a][x["qid"]] = R.score_dev(x[a]["pred"], q)
        sc["S_oracle"][x["qid"]] = x["S_oracle"]["score"]

    def table(subset):
        out = {}
        for a in sc:
            c = collections.Counter()
            for qid in subset:
                q, s = Q[qid], sc[a][qid]
                k = "suff" if q["gold"]["source_sufficient"] else "insuff"
                c[k + "_n"] += 1
                c[k + "_correct"] += s["correct"]
                c["unsupported"] += s["unsupported"]
                c["wrong_refusal"] += s["wrong_refusal"]
                c["parse_fail"] += s["parse_fail"]
            out[a] = dict(c)
        return out

    allq = [x["qid"] for x in rows]
    suff = [q for q in allq if Q[q]["gold"]["source_sufficient"]]
    insuff = [q for q in allq if not Q[q]["gold"]["source_sufficient"]]
    report = {
        "all": table(allq),
        "paraphrased_ok": table([q for q in allq if Q[q].get("paraphrase", {}).get("status") == "ok"]),
        "template_fallback": table([q for q in allq if Q[q].get("paraphrase", {}).get("status") != "ok"]),
        "tables_unseen_in_dev": table([q for q in allq if Q[q]["scope"]["table"] not in dev_tables]),
        "by_type": {t: table([q for q in allq if Q[q]["type"] == t]) for t in sorted({Q[q]["type"] for q in allq})},
        "mcnemar_S_vs_B2": {
            "insufficient_judgement": mcnemar([sc["S"][q]["correct"] for q in insuff], [sc["B2"][q]["correct"] for q in insuff]),
            "sufficient_accuracy": mcnemar([sc["S"][q]["correct"] for q in suff], [sc["B2"][q]["correct"] for q in suff]),
        },
        # S errors: oracle intent right but LLM intent wrong -> intent parsing; both wrong -> executor
        "S_error_source": dict(collections.Counter(
            ("intent_parse" if sc["S_oracle"][q]["correct"] else "executor") for q in allq if not sc["S"][q]["correct"])),
    }
    a, s = report["all"]["S"], report["all"]["B2"]
    acc = lambda c, k: c[k + "_correct"] / c[k + "_n"]
    report["preregistered_decision"] = {
        "S_insuff_acc": round(acc(a, "insuff"), 4), "B2_insuff_acc": round(acc(s, "insuff"), 4),
        "S_suff_acc": round(acc(a, "suff"), 4), "B2_suff_acc": round(acc(s, "suff"), 4),
        "pass": acc(a, "insuff") > acc(s, "insuff") and acc(a, "suff") >= acc(s, "suff") - 0.03,
    }
    json.dump(report, open(out_path, "w"), indent=1)
    print(json.dumps(report["preregistered_decision"]))
    for k in ("all", "paraphrased_ok", "template_fallback", "tables_unseen_in_dev"):
        print("==", k)
        for arm, c in report[k].items():
            print(f'  {arm:8s} suff {c.get("suff_correct", 0)}/{c.get("suff_n", 0)}  insuff {c.get("insuff_correct", 0)}/{c.get("insuff_n", 0)}'
                  f'  unsupported {c.get("unsupported", 0)}  wrong_refusal {c.get("wrong_refusal", 0)}')
    print("mcnemar", json.dumps(report["mcnemar_S_vs_B2"]))
    print("S errors", report["S_error_source"])


if __name__ == "__main__":
    main()
