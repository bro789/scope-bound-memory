"""Gap-driven minimal refetch (G) on raw-state retrieval memory. vLLM offline Qwen3.5-9B, thinking off, T=0.

Memory = every raw state of the question's haystack trajectories, rendered with ax_compact (same for all arms).
  R        : BM25 top-5 states -> reader answers (JSON with sufficient / answer / lower bound).
  For every question where R says insufficient (or cannot be parsed):
  R+G      : reader lists the missing list views as JSON (session, table, filter); each is located by its stored
             scope fields (session, table, breadcrumb conditions) among the parsed list-view observations, and the
             latest state of the best group (plus its first state when the group changed) is appended; re-answer.
  R+E      : the same number of characters filled with the next BM25-ranked states; re-answer.
  R+Goracle: the question's required-evidence states appended; re-answer (upper bound for the fetch itself).
Fetch hit = an appended state shows the same observation (session, table, filter, page, rows) as a required one.

Usage: python g_experiment.py --questions Q1.jsonl[,Q2.jsonl] --out DIR [--dry-run] [--limit N]
"""
import argparse
import collections
import copy
import json
import os
import re
import time

import run_scope_experiments as R
from ax_compact import compact
from scope_memory import ScopeMemory

TOPK = 5
STATE_CHARS = 16000
MAX_NEEDS = 2
GAP_FMT = ("Do NOT answer yet. The memory above may be missing the list view this question needs. Output only JSON: "
           "{\"needed\": [{\"trajectory\": \"<trajectory id from the memory headers or the task text, or null>\", "
           "\"table\": \"<list or table name>\", \"filter\": \"<filter conditions as written in the question, or "
           "'none' for the full list>\", \"why\": \"<what is missing>\"}]}. List at most two items.")


def render(s):
    return (f"### Raw page: {s['title']} (task: {s['goal']}; trajectory {s['trajectory']}, step {s['state']})\n"
            + compact(s["ax"], STATE_CHARS))


def schema(obs):
    """Intent-parser schema in the frozen S v2.1 format, built only from the observations the system holds."""
    by_t = collections.OrderedDict()
    for o in obs:
        by_t.setdefault(o["trajectory"], []).append(o)
    lines = []
    for t, vs in by_t.items():
        tabs, filt = collections.OrderedDict(), collections.defaultdict(list)
        for o in vs:
            tabs.setdefault(o["table"], set()).update(o["columns"])
            if o["conditions"] not in filt[o["table"]]:
                filt[o["table"]].append(o["conditions"])
        lines.append(f"- trajectory {t}: {' '.join(vs[0]['goal'].split())[:300]}\n  tables:\n"
                     + "\n".join(f"    {tb} (columns: {', '.join(sorted(c))}; observed filters: "
                                 + " | ".join(json.dumps(f) for f in filt[tb][:15]) + ")" for tb, c in tabs.items()))
    return "\n".join(lines)


def coverage_note(o):
    """v2 (tuned on dev): machine-read coverage of a list view, prefixed to every APPENDED page in G, E and Goracle."""
    p = o["page"]
    whole = p["first"] == 1 and p["last"] >= p["total"] and len(o["rows"]) == p["total"]
    flt = " AND ".join(o["conditions"]) or "none"
    state = ("COMPLETE: all rows of this filtered list are visible" if whole else
             f"INCOMPLETE: only rows {p['first']}-{p['last']} of {p['total']} are visible "
             f"({len(o['rows'])} parsed); counts, lists, negatives and differences over the whole list cannot be "
             f"read from this page alone")
    return f"[Coverage note: list view of table '{o['table']}', filter: {flt}; showing rows {p['first']}-{p['last']} of {p['total']}. {state}]\n"


def view_sig(o):
    return json.dumps([o["trajectory"], o["table"], o["conditions"], o["page"], [r["values"] for r in o["rows"]]],
                      sort_keys=True)


def tokset(s):
    return set(R.toks(str(s or "")))


def jacc(a, b):
    return len(a & b) / max(1, len(a | b))


class Locator:
    """Find list-view observations by scope fields; generic token matching, no question-specific rules."""

    def __init__(self, views, goals):
        self.views = views
        self.goals = goals  # trajectory -> task text
        self.groups = collections.defaultdict(list)
        for o in views:
            self.groups[(o["trajectory"], o["table"], tuple(o["conditions"]))].append(o)

    def resolve_traj(self, need, question):
        t = str(need.get("trajectory") or "").strip()
        if t in self.goals:
            return t
        q = tokset(question)
        return max(self.goals, key=lambda x: jacc(tokset(self.goals[x]), q))

    def locate(self, need, question):
        traj = self.resolve_traj(need, question)
        want_tab = tokset(need.get("table"))
        f = str(need.get("filter") or "")
        want_c = tokset(f)
        full = not want_c or bool(re.search(r"\b(none|no filter|full|all|unfiltered|empty)\b", f.lower()))
        best = None
        for (t, tab, conds), obs in self.groups.items():
            if t != traj:
                continue
            c_tok = tokset(" ".join(conds))
            sc = (jacc(tokset(tab), want_tab),
                  (1.0 if not conds else 0.0) if full and not (want_c - {"none", "no", "filter", "full", "all", "list"})
                  else jacc(c_tok, want_c))
            key = (sc[0] + sc[1], len(obs))
            if best is None or key > best[0]:
                best = (key, (t, tab, conds))
        if best is None:
            return []
        obs = sorted(self.groups[best[1]], key=lambda o: o["state"])
        picks = [obs[-1]]
        if view_sig(obs[0]) != view_sig(obs[-1]):
            picks.insert(0, obs[0])
        return picks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=10 ** 6)
    ap.add_argument("--with-s", action="store_true", help="add the S@R and S+G arms (frozen S executor on held views)")
    ap.add_argument("--no-annotate", dest="annotate", action="store_false",
                    help="v1 behaviour: appended pages without coverage notes")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    qs = [json.loads(l) for p in a.questions.split(",") for l in open(p)][: a.limit]
    trajs = {t for q in qs for t in q["haystack"]}
    states = collections.defaultdict(list)
    for s in R.load_states(trajs):
        states[s["trajectory"]].append(s)
    views = [json.loads(l) for l in open(R.VIEWS)]
    views = [o for o in views if o["trajectory"] in trajs]
    vsig = {(o["trajectory"], o["state"]): view_sig(o) for o in views}
    vobs = {(o["trajectory"], o["state"]): o for o in views}

    def render_add(s):  # appended pages carry the coverage note when they are parsed list views
        o = vobs.get((s["trajectory"], s["state"]))
        return (coverage_note(o) if o and a.annotate else "") + render(s)
    reader = R.Reader(a.dry_run)
    t0 = time.time()

    items = []
    for q in qs:
        st = [s for t in q["haystack"] for s in states[t]]
        docs = [s["title"] + " " + s["goal"] + " " + compact(s["ax"], STATE_CHARS) for s in st]
        ranked = R.BM25(docs).top(q["question"], 40)
        ctx = [st[i] for i in ranked[:TOPK]]
        mem = "Memory (raw pages from earlier sessions):\n" + "\n\n".join(render(s) for s in ctx)
        loc = Locator([o for o in views if o["trajectory"] in q["haystack"]],
                      {t: next((s["goal"] for s in states[t]), "") for t in q["haystack"]})
        items.append({"q": q, "st": st, "ranked": ranked, "ctx": ctx, "mem": mem, "loc": loc})

    def ask(mem, q, extra=""):
        return [{"role": "user", "content": f"{mem}{extra}\n\nQuestion: {q['question']}\n\n{R.ANSWER_FMT}"}]

    r_out = reader.chat([ask(it["mem"], it["q"]) for it in items])
    json.dump(r_out, open(os.path.join(a.out, "raw_R.json"), "w"), ensure_ascii=False)
    trig = [i for i, o in enumerate(r_out) if not (R.parse_json(o["text"]) or {}).get("sufficient", False)
            or a.dry_run]
    gap_out = reader.chat([[{"role": "user", "content": f"{items[i]['mem']}\n\nQuestion: {items[i]['q']['question']}\n\n{GAP_FMT}"}]
                           for i in trig])
    json.dump(gap_out, open(os.path.join(a.out, "raw_gap.json"), "w"), ensure_ascii=False)

    have_key = lambda s: (s["trajectory"], s["state"])
    g_msgs, e_msgs, o_msgs, meta = [], [], [], []
    for i, go in zip(trig, gap_out):
        it = items[i]
        in_ctx = {have_key(s) for s in it["ctx"]}
        needs = ((R.parse_json(go["text"]) or {}).get("needed") or [])[:MAX_NEEDS]
        if a.dry_run:  # exercise the locator on the gold scope
            sc = it["q"]["scope"]
            needs = [{"trajectory": it["q"]["anchor_trajectory"], "table": sc["table"],
                      "filter": " AND ".join(sc.get("asked_conditions", sc["conditions"])) or "none"}]
        fetched = []
        for n in needs:
            if not isinstance(n, dict):
                continue
            for o in it["loc"].locate(n, it["q"]["question"]):
                s = next((x for x in states[o["trajectory"]] if x["state"] == o["state"]), None)
                if s and have_key(s) not in in_ctx and s not in fetched:
                    fetched.append(s)
        add_g = "\n\n".join(render_add(s) for s in fetched)
        extra_e, used = [], 0
        for j in it["ranked"][TOPK:]:
            if used >= len(add_g):
                break
            blk = render_add(it["st"][j])[: len(add_g) - used]
            extra_e.append(blk)
            used += len(blk)
        req = [s for t, idx in {(it["q"]["anchor_trajectory"], e["state"]) for e in it["q"]["gold"]["required_evidence"]
                                if "state" in e}
               for s in states[t] if s["state"] == idx and have_key(s) not in in_ctx]
        add_o = "\n\n".join(render_add(s) for s in req)
        hdr = "\n\n### Additional raw pages:\n"
        g_msgs.append(ask(it["mem"], it["q"], hdr + (add_g or "(none found)")))
        e_msgs.append(ask(it["mem"], it["q"], hdr + ("\n\n".join(extra_e) or "(none)")))
        o_msgs.append(ask(it["mem"], it["q"], hdr + (add_o or "(none)")))
        req_sigs = {vsig.get((it["q"]["anchor_trajectory"], e["state"])) for e in it["q"]["gold"]["required_evidence"]
                    if "state" in e} - {None}
        hit = lambda ss: any(vsig.get(have_key(s)) in req_sigs for s in ss)
        e_states = [it["st"][j] for j in it["ranked"][TOPK:TOPK + len(extra_e)]]
        meta.append({"needs": needs, "fetched": [have_key(s) for s in fetched], "g_chars": len(add_g),
                     "e_chars": used, "g_hit": hit(fetched), "e_hit": hit(e_states),
                     "ctx_hit": hit(it["ctx"])})
    outs = {}
    for name, msgs in (("G", g_msgs), ("E", e_msgs), ("Goracle", o_msgs)):
        outs[name] = reader.chat(msgs)
        json.dump(outs[name], open(os.path.join(a.out, f"raw_{name}.json"), "w"), ensure_ascii=False)

    rows, tot = [], collections.defaultdict(collections.Counter)
    tpos = {i: k for k, i in enumerate(trig)}

    # S on top of retrieval: the frozen S executor runs over the list views the system actually holds
    #   S@R : list views among the BM25 context only;   S+G : those plus the pages G fetched.
    # Executor answer -> final; executor "partial coverage" -> insufficient; no binding -> reader's own answer.
    s_preds = {"S@R": [None] * len(items), "S+G": [None] * len(items)}
    s_log = {"S@R": [None] * len(items), "S+G": [None] * len(items)}
    if a.with_s:
        avail = {}
        for i, it in enumerate(items):
            ctx_obs = [vobs[have_key(s)] for s in it["ctx"] if have_key(s) in vobs]
            got = [vobs[k] for k in (meta[tpos[i]]["fetched"] if i in tpos else []) if k in vobs]
            avail[("S@R", i)] = ctx_obs
            avail[("S+G", i)] = ctx_obs + [o for o in got if o not in ctx_obs]
        jobs = [(arm, i) for i in range(len(items)) for arm in ("S@R", "S+G") if avail[(arm, i)]
                and not (arm == "S+G" and len(avail[("S+G", i)]) == len(avail[("S@R", i)]))]
        msgs = [[{"role": "user", "content": R.INTENT_PROMPT.format(tasks=schema(avail[j]), q=items[j[1]]["q"]["question"])}]
                for j in jobs]
        s_out = reader.chat(msgs)
        json.dump([[list(j), o] for j, o in zip(jobs, s_out)], open(os.path.join(a.out, "raw_S.json"), "w"),
                  ensure_ascii=False)
        intents = {j: R.parse_json(o["text"]) for j, o in zip(jobs, s_out)}
        for i in range(len(items)):
            reader_text = {"S@R": r_out[i]["text"],
                           "S+G": outs["G"][tpos[i]]["text"] if i in tpos else r_out[i]["text"]}
            for arm in ("S@R", "S+G"):
                key = (arm, i) if (arm, i) in intents else ("S@R", i)
                intent, ex = intents.get(key), None
                if intent and avail[(arm, i)]:
                    try:
                        ex = ScopeMemory(copy.deepcopy(avail[(arm, i)])).answer(intent)
                    except Exception as err:
                        ex = {"status": "executor_error", "error": repr(err)}
                if ex and ex.get("status") == "answered":
                    pred, how = R.executor_to_pred(ex), "executor"
                elif ex and str(ex.get("reason", "")).startswith(("partial_coverage", "no_witness")):
                    pred, how = R.executor_to_pred(ex), "executor_insufficient"
                else:
                    pred, how = R.parse_json(reader_text[arm]), "reader_fallback"
                s_preds[arm][i] = pred
                s_log[arm][i] = {"how": how, "intent": intent, "exec": ex, "n_avail": len(avail[(arm, i)])}

    for i, it in enumerate(items):
        q = it["q"]
        row = {"qid": q["qid"], "type": q["type"], "sufficient": q["gold"]["source_sufficient"],
               "triggered": i in tpos, "R": {"out": r_out[i]}}
        if i in tpos:
            row["meta"] = meta[tpos[i]]
        for arm in ("R", "R+G", "R+E", "R+Goracle") + (("S@R", "S+G") if a.with_s else ()):
            if arm in s_preds:
                pred = s_preds[arm][i]
                row.setdefault("s_log", {})[arm] = s_log[arm][i]
            else:
                text = r_out[i]["text"] if (arm == "R" or i not in tpos) else outs[arm[2:]][tpos[i]]["text"]
                pred = R.parse_json(text)
            sc = R.score_dev(pred, q)
            row.setdefault("scores", {})[arm] = sc
            k = "suff" if q["gold"]["source_sufficient"] else "insuff"
            tot[arm][k + "_n"] += 1
            tot[arm][k + "_correct"] += sc["correct"]
            for f in ("unsupported", "wrong_refusal", "parse_fail"):
                tot[arm][f] += sc[f]
        rows.append(row)
    fetch = {"triggered": len(trig),
             "triggered_sufficient": sum(items[i]["q"]["gold"]["source_sufficient"] for i in trig),
             "G_hit": sum(m["g_hit"] for m in meta), "E_hit": sum(m["e_hit"] for m in meta),
             "ctx_already_hit": sum(m["ctx_hit"] for m in meta),
             "G_fetched_any": sum(bool(m["fetched"]) for m in meta),
             "G_chars": sum(m["g_chars"] for m in meta), "E_chars": sum(m["e_chars"] for m in meta)}
    summ = {"arms": {k: dict(v) for k, v in tot.items()}, "fetch": fetch, "seconds": time.time() - t0}
    json.dump(rows, open(os.path.join(a.out, "g_results.json"), "w"), indent=1, ensure_ascii=False, default=str)
    json.dump(summ, open(os.path.join(a.out, "g_summary.json"), "w"), indent=1)
    print(json.dumps(summ))


if __name__ == "__main__":
    main()
