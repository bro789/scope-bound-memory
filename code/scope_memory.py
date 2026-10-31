"""Minimal scope-bound memory: scope binding -> operator coverage check -> gap report.

Memory unit = one raw list-view observation with its scope
  O = (source trajectory, state/version, table, filter conditions, page a..b of N,
       container completeness, AX row nodes).
A query intent = {operator, anchor_trajectory, table, conditions, column?, value?, key_col?, key?}.

The executor never guesses: if the bound observations do not cover what the operator
needs, it returns `insufficient` with an observed lower bound and the minimal gap to
fetch (missing page range or unobserved filter). CPU only, no model calls.

`intent_from_question` uses the dev question's structured scope (oracle intent). That only
checks the executor; it is NOT an efficacy result, because the dev gold uses the same
coverage definitions. Efficacy needs an LLM intent parser against ordinary baselines.
"""
import json
import re
import sys

COUNT_OPS = {"count"}
KEY_COLS = ("Number", "Name", "User ID", "SLA definition", "Display name", "Short description", "Title")


def complete(o):
    p = o["page"]
    return p["first"] == 1 and p["last"] >= p["total"] and len(o["rows"]) == p["total"]


class ScopeMemory:
    def __init__(self, observations):
        # same cell cleaning as the dev-question generator ("<value> - Open record: <name>" -> "<value>")
        for o in observations:
            for r in o["rows"]:
                r["values"] = {c: (v.split(" - Open record: ")[0] if isinstance(v, str) else v)
                               for c, v in r["values"].items()}
        self.obs = observations

    @classmethod
    def from_jsonl(cls, path, trajectories=None):
        keep = set(trajectories) if trajectories else None
        out = []
        for line in open(path):
            o = json.loads(line)
            if keep is None or o["trajectory"] in keep:
                out.append(o)
        return cls(out)

    # 0. intent normalisation (v2): generic repairs of LLM intent slots, no question-specific rules
    def normalize(self, intent):
        it = dict(intent)
        # v2.1 (frozen before confirmation): drop [ ] the parser may copy from the question text
        it["conditions"] = [re.sub(r"^\[(.*)\]$", r"\1", str(c).strip()) for c in (it.get("conditions") or [])]
        src = [o for o in self.obs if o["trajectory"] == it.get("anchor_trajectory")]
        tables = sorted({o["table"] for o in src})
        if it.get("table") not in tables and tables:
            want = set(re.findall(r"[a-z0-9]+", str(it.get("table", "")).lower().replace("_", " ")))
            best = max(tables, key=lambda t: len(want & set(re.findall(r"[a-z0-9]+", t.lower()))))
            if want & set(re.findall(r"[a-z0-9]+", best.lower())):
                it["table"] = best
        cols = set()
        for o in src:
            if o["table"] == it.get("table"):
                cols.update(o["columns"])
        if it["operator"] in ("lookup", "lookup_latest") and it.get("key") is None and it.get("value") is not None:
            it["key_col"], it["key"], it["column"] = it.get("column"), it.get("value"), it.get("key_col")
            it["value"] = None
        if not it.get("key_col"):
            if it["operator"] in ("enumerate", "difference") and it.get("column") in cols and it.get("value") is None:
                it["key_col"], it["column"] = it["column"], None
            else:
                it["key_col"] = next((c for c in KEY_COLS if c in cols), None)
        # an asked condition that was never a breadcrumb but reads "Col = value" becomes a column filter
        if not self.bind(it) and it.get("column") is None:
            observed = {tuple(o["conditions"]) for o in src if o["table"] == it.get("table")}
            for obs_c in observed:
                rest = [c for c in it["conditions"] if c not in obs_c]
                if set(obs_c) <= set(it["conditions"]) and len(rest) == 1:
                    m = re.match(r"^(.+?)\s*=\s*'?(.*?)'?$", rest[0])
                    if m and m.group(1) in cols:
                        it["conditions"], it["column"], it["value"] = list(obs_c), m.group(1), m.group(2)
                        break
        return it

    # 1. scope binding: same source, same entity, same filter; ordered by version
    def bind(self, intent):
        b = [o for o in self.obs
             if o["trajectory"] == intent["anchor_trajectory"] and o["table"] == intent["table"]
             and o["conditions"] == intent["conditions"]]
        return sorted(b, key=lambda o: o["state"])

    def nearest_scopes(self, intent):
        """Observed filters of the same table and source, for reporting why binding failed."""
        return sorted({tuple(o["conditions"]) for o in self.obs
                       if o["trajectory"] == intent["anchor_trajectory"] and o["table"] == intent["table"]})

    # 2+3. operator-specific coverage check and execution
    def answer(self, intent):
        intent = self.normalize(intent)
        op = intent["operator"]
        bound = self.bind(intent)
        if not bound:
            return {"status": "insufficient", "reason": "no_observation_with_requested_scope",
                    "gap": {"fetch": "list", "table": intent["table"], "conditions": intent["conditions"]},
                    "observed_scopes": [list(s) for s in self.nearest_scopes(intent)]}
        last = bound[-1]
        kc = intent.get("key_col")
        col, val = intent.get("column"), intent.get("value")
        ev = [{"trajectory": last["trajectory"], "state": last["state"], "page": last["page"]}]

        def gap(o):
            p = o["page"]
            return {"fetch": "pages", "table": o["table"], "conditions": o["conditions"],
                    "rows": [p["last"] + 1, p["total"]] if p["first"] == 1 else [1, p["total"]],
                    "unparsed_rows_in_container": max(0, p["last"] - p["first"] + 1 - len(o["rows"]))}

        rows = last["rows"]
        match = [r for r in rows if col is None or r["values"].get(col) == val]
        if op == "lookup" or op == "lookup_latest":
            for o in reversed(bound):
                for r in o["rows"]:
                    if r["values"].get(kc) == intent["key"]:
                        return {"status": "answered", "answer": r["values"].get(col),
                                "evidence": [{"trajectory": o["trajectory"], "state": o["state"], "row_node": r["node"]}]}
            return {"status": "insufficient", "reason": "key_not_in_bound_observations", "gap": gap(last)}
        if op == "exists":
            for o in reversed(bound):
                for r in o["rows"]:
                    if r["values"].get(col) == val:
                        return {"status": "answered", "answer": "yes",
                                "evidence": [{"trajectory": o["trajectory"], "state": o["state"], "row_node": r["node"]}]}
            if complete(last):
                return {"status": "answered", "answer": "no", "evidence": ev}
            return {"status": "insufficient", "reason": "no_witness_and_partial_coverage",
                    "observed_lower_bound": 0, "gap": gap(last)}
        if op == "count":
            if col is None:  # the container header states the size of the filtered set
                return {"status": "answered", "answer": last["page"]["total"], "evidence": ev}
            if complete(last):
                return {"status": "answered", "answer": len(match), "evidence": ev}
            return {"status": "insufficient", "reason": "partial_coverage",
                    "observed_lower_bound": len(match), "gap": gap(last)}
        if op == "enumerate":
            keys = sorted(r["values"].get(kc) for r in match)
            if complete(last):
                return {"status": "answered", "answer": keys, "evidence": ev}
            return {"status": "insufficient", "reason": "partial_coverage",
                    "observed_lower_bound": len(keys), "observed_members": keys, "gap": gap(last)}
        if op == "difference":
            first = next((o for o in bound if o["state"] == intent.get("first_state")), bound[0])
            if not (complete(first) and complete(last)):
                bad = [o for o in (first, last) if not complete(o)]
                return {"status": "insufficient", "reason": "partial_coverage_of_a_version",
                        "gap": [gap(o) | {"state": o["state"]} for o in bad]}
            a = {r["values"].get(kc) for r in first["rows"]}
            b = {r["values"].get(kc) for r in rows}
            return {"status": "answered", "answer": sorted(x for x in a - b if x),
                    "evidence": [{"trajectory": first["trajectory"], "state": first["state"]}] + ev}
        raise ValueError(op)


def intent_from_question(q):
    """Oracle intent taken from the dev question's structured scope (executor check only)."""
    s = q["scope"]
    it = {"operator": q["operator"], "anchor_trajectory": q["anchor_trajectory"], "table": s["table"],
          "conditions": s.get("asked_conditions", s["conditions"]), "column": s.get("column"),
          "value": s.get("value"), "first_state": s.get("first_state")}
    qt = q["question"]
    if q["operator"] in ("lookup", "lookup_latest"):
        # "... what was the '<col>' of [the record with] <key_col> '<key>'?"
        tail = qt.rsplit("what was the '", 1)[1]
        it["column"] = tail.split("'", 1)[0]
        kpart = tail.split(" of ", 1)[1].replace("the record with ", "")
        it["key_col"], it["key"] = kpart.split(" '", 1)[0], kpart.split(" '", 1)[1].rsplit("'", 1)[0]
    elif q["operator"] in ("enumerate", "difference"):
        it["key_col"] = qt.split("list the ", 1)[1].split(" of every", 1)[0] if "list the " in qt \
            else qt.split("Which records (", 1)[1].split(")", 1)[0]
    if q["operator"] == "lookup_latest":
        it["value"] = None
    return it


def score(pred, gold):
    if not gold["source_sufficient"]:
        return pred["status"] == "insufficient"
    return pred["status"] == "answered" and pred["answer"] == gold["answer"]


if __name__ == "__main__":
    views, questions = sys.argv[1], sys.argv[2]
    qs = [json.loads(l) for l in open(questions)]
    mem = ScopeMemory.from_jsonl(views, {t for q in qs for t in q["haystack"]})
    ok = 0
    for q in qs:
        p = mem.answer(intent_from_question(q))
        good = score(p, q["gold"])
        ok += good
        if not good:
            print("MISMATCH", q["qid"], q["type"], json.dumps(p)[:300])
    print(json.dumps({"questions": len(qs), "executor_consistent": ok}))
