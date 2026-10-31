"""Pre-registered analysis of the G confirmation run (see G_confirmation_set_preregistration.md). CPU only.

Usage: python analyze_g.py G_RESULTS QUESTIONS CONFIRM_V1_QUESTIONS OUT_JSON
"""
import collections
import json
import sys

from analyze_confirm import mcnemar

ARMS = ("R", "R+G", "R+E", "R+Goracle")


def main():
    res_path, q_path, v1_path, out_path = sys.argv[1:5]
    rows = json.load(open(res_path))
    Q = {json.loads(l)["qid"]: json.loads(l) for l in open(q_path)}
    v1_anchors = {json.loads(l)["anchor_trajectory"] for l in open(v1_path)}

    def table(sub):
        out = {}
        for a in ARMS:
            c = collections.Counter()
            for x in sub:
                k = "suff" if x["sufficient"] else "insuff"
                s = x["scores"][a]
                c[k + "_n"] += 1
                c[k + "_correct"] += s["correct"]
                c["unsupported"] += s["unsupported"]
                c["wrong_refusal"] += s["wrong_refusal"]
            out[a] = dict(c)
        return out

    suff = [x for x in rows if x["sufficient"]]
    insuff = [x for x in rows if not x["sufficient"]]
    trig = [x for x in rows if x["triggered"]]
    rep = {
        "all": table(rows),
        "anchor_in_confirm_v1": table([x for x in rows if Q[x["qid"]]["anchor_trajectory"] in v1_anchors]),
        "anchor_new": table([x for x in rows if Q[x["qid"]]["anchor_trajectory"] not in v1_anchors]),
        "paraphrased_ok": table([x for x in rows if Q[x["qid"]].get("paraphrase", {}).get("status") == "ok"]),
        "by_type": {t: table([x for x in rows if x["type"] == t]) for t in sorted({x["type"] for x in rows})},
        "mcnemar_G_vs_E": {
            "sufficient": mcnemar([x["scores"]["R+G"]["correct"] for x in suff], [x["scores"]["R+E"]["correct"] for x in suff]),
            "insufficient": mcnemar([x["scores"]["R+G"]["correct"] for x in insuff], [x["scores"]["R+E"]["correct"] for x in insuff]),
            "fetch_hit_on_triggered": mcnemar([x["meta"]["g_hit"] for x in trig], [x["meta"]["e_hit"] for x in trig]),
        },
        "fetch": {"triggered": len(trig), "G_hit": sum(x["meta"]["g_hit"] for x in trig),
                  "E_hit": sum(x["meta"]["e_hit"] for x in trig),
                  "ctx_already_hit": sum(x["meta"]["ctx_hit"] for x in trig),
                  "G_fetched_any": sum(bool(x["meta"]["fetched"]) for x in trig)},
    }
    a = rep["all"]
    acc = lambda arm, k: a[arm][k + "_correct"] / a[arm][k + "_n"]
    rep["preregistered_decision"] = {
        "G_suff": round(acc("R+G", "suff"), 4), "E_suff": round(acc("R+E", "suff"), 4),
        "G_insuff": round(acc("R+G", "insuff"), 4), "E_insuff": round(acc("R+E", "insuff"), 4),
        "pass": acc("R+G", "suff") > acc("R+E", "suff") and acc("R+G", "insuff") >= acc("R+E", "insuff") - 0.03,
        "mechanism_pass": rep["mcnemar_G_vs_E"]["fetch_hit_on_triggered"]["p"] < 0.05
        and rep["fetch"]["G_hit"] > rep["fetch"]["E_hit"],
    }
    json.dump(rep, open(out_path, "w"), indent=1)
    print(json.dumps(rep["preregistered_decision"]))
    for k in ("all", "anchor_in_confirm_v1", "anchor_new", "paraphrased_ok"):
        print("==", k)
        for arm, c in rep[k].items():
            print(f'  {arm:10s} suff {c.get("suff_correct", 0)}/{c.get("suff_n", 0)}  insuff {c.get("insuff_correct", 0)}/{c.get("insuff_n", 0)}'
                  f'  unsupported {c.get("unsupported", 0)}  wrong_refusal {c.get("wrong_refusal", 0)}')
    print("fetch", json.dumps(rep["fetch"]))
    print("mcnemar", json.dumps(rep["mcnemar_G_vs_E"]))
    for t, v in rep["by_type"].items():
        print(t, {arm: f'{v[arm].get("suff_correct", 0)}/{v[arm].get("suff_n", 0)} {v[arm].get("insuff_correct", 0)}/{v[arm].get("insuff_n", 0)}' for arm in ARMS})


if __name__ == "__main__":
    main()
