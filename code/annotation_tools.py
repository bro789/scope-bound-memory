"""Blind annotation pipeline helpers (no model calls).

  questions  ROUND1_DIR OUT_DIR       : strip answers from round-1 annotations -> questions-only files for round 2
  compare    ROUND1_DIR ROUND2_DIR OUT : agreement between annotator (round 1) and verifier (round 2);
                                         writes the disagreement list for blind adjudication
  merge      ROUND1_DIR ROUND2_DIR ADJ_DIR OUT_JSONL : frozen gold = agreed items + adjudicated items
"""
import collections
import glob
import json
import os
import re
import sys


def load(d):
    out = {}
    for p in sorted(glob.glob(os.path.join(d, "*.jsonl"))):
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if line:
                x = json.loads(line)
                out[x["qid"]] = x
    return out


def norm(v):
    if isinstance(v, bool):
        v = "yes" if v else "no"
    if isinstance(v, (list, tuple)):
        return tuple(sorted(norm(x) for x in v))
    return re.sub(r"[^a-z0-9]+", "", str(v).lower())


def same_answer(a, b):
    if a is None or b is None:
        return a is None and b is None
    na, nb = norm(a), norm(b)
    if isinstance(na, tuple) or isinstance(nb, tuple):
        ta = na if isinstance(na, tuple) else (na,)
        tb = nb if isinstance(nb, tuple) else (nb,)
        return ta == tb
    return na == nb or (na and nb and (na in nb or nb in na) and min(len(na), len(nb)) >= 4)


def questions(r1, out):
    os.makedirs(out, exist_ok=True)
    by = collections.defaultdict(list)
    for x in load(r1).values():
        by[x["session"]].append({"qid": x["qid"], "question": x["question"]})
    for s, qs in by.items():
        with open(os.path.join(out, s + ".questions.jsonl"), "w", encoding="utf-8") as f:
            for q in sorted(qs, key=lambda q: q["qid"]):
                f.write(json.dumps(q, ensure_ascii=False) + "\n")
    print(json.dumps({"sessions": len(by), "questions": sum(len(v) for v in by.values())}))


def compare(r1, r2, out):
    a, b = load(r1), load(r2)
    rows, c = [], collections.Counter()
    for qid, x in sorted(a.items()):
        y = b.get(qid)
        if y is None:
            c["missing_in_round2"] += 1
            rows.append({"qid": qid, "issue": "missing"})
            continue
        det_ok = bool(x["determined"]) == bool(y["determined"])
        ans_ok = (not x["determined"]) or same_answer(x["answer"], y["answer"])
        c["n"] += 1
        c["determined_agree"] += det_ok
        c["full_agree"] += det_ok and ans_ok
        c["r1_determined"] += bool(x["determined"])
        c["r2_determined"] += bool(y["determined"])
        if not (det_ok and ans_ok):
            rows.append({"qid": qid, "issue": "determined" if not det_ok else "answer",
                         "r1": {k: x[k] for k in ("determined", "answer", "what_is_known", "evidence_pages")},
                         "r2": {k: y.get(k) for k in ("determined", "answer", "what_is_known", "evidence_pages")}})
    # Cohen's kappa on the determined label
    n = c["n"] or 1
    po = c["determined_agree"] / n
    p1, p2 = c["r1_determined"] / n, c["r2_determined"] / n
    pe = p1 * p2 + (1 - p1) * (1 - p2)
    c_out = dict(c)
    c_out["kappa_determined"] = round((po - pe) / (1 - pe), 4) if pe < 1 else None
    json.dump({"summary": c_out, "disagreements": rows}, open(out, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print(json.dumps(c_out))


def merge(r1, r2, adj, out):
    a, b, j = load(r1), load(r2), load(adj) if os.path.isdir(adj) else {}
    n = collections.Counter()
    with open(out, "w", encoding="utf-8") as f:
        for qid, x in sorted(a.items()):
            y = b.get(qid)
            agree = y is not None and bool(x["determined"]) == bool(y["determined"]) and \
                ((not x["determined"]) or same_answer(x["answer"], y["answer"]))
            if agree:
                gold, src = x, "agreed"
            elif qid in j:
                if j[qid].get("verdict") == "drop":
                    n["dropped"] += 1
                    continue
                gold, src = dict(x, **{k: j[qid][k] for k in ("determined", "answer", "what_is_known", "evidence_pages")
                                       if k in j[qid]}), "adjudicated"
            else:
                n["unresolved"] += 1
                continue
            n[src] += 1
            f.write(json.dumps(dict(gold, gold_source=src), ensure_ascii=False) + "\n")
    print(json.dumps(dict(n)))


if __name__ == "__main__":
    cmd = sys.argv[1]
    {"questions": questions, "compare": compare, "merge": merge}[cmd](*sys.argv[2:])
