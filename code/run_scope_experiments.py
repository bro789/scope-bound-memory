"""Scope route GPU experiments (idea 2). vLLM offline, Qwen3.5-9B, thinking off, temperature 0.

Stages
  residual : 6 scope/coverage residual answers from main48 rerank arms.
             R0 = original rerank messages re-answered; G = reader names missing pages, we fetch
             those pages' raw AX from the saved haystack and re-answer; E = same appended budget
             filled by ordinary BM25 retrieval over the same haystack with the question as query.
  dev38    : frozen dev_v1 questions. B0 raw BM25 states, B1 scoped table rows, B2 = B1 + fixed
             scope-check prompt, S = LLM intent JSON + scope_memory executor, S_oracle = executor
             on the question's structured scope (upper bound, separates parse vs executor errors).

Usage: python run_scope_experiments.py --stage residual|dev38|all --out DIR [--dry-run] [--limit N]
--dry-run builds every prompt and reports sizes without importing vLLM.
"""
import argparse
import collections
import json
import math
import os
import re
import sys
import time

# paths only; overridable so the same code runs on another server (e.g. machine 6 after machine 4 was shut down)
ROOT = os.environ.get("SCOPE02_ROOT", "/mnt/nvme/vldb-20261021/memory")
sys.path.insert(0, os.path.join(ROOT, "scope02"))
from scope_memory import ScopeMemory, intent_from_question  # noqa: E402

MODEL = os.environ.get("SCOPE02_MODEL", os.path.join(ROOT, "models/Qwen3.5-9B"))
TRAJ = os.path.join(ROOT, "trajectories.jsonl")
HAY = os.path.join(ROOT, "lme_v2_small.json")
VIEWS = os.path.join(ROOT, "scope02/list_views_dev_pool.jsonl")
DEV = os.path.join(ROOT, "scope02/dev_v2/dev_questions_v2.jsonl")
RESIDUAL = [  # (question id, namespace) — rerank arm cases selected in residual_analysis_main48.md
    ("1bfd2eac", "official_r_c2022362_e97b0c61_v1"),
    ("1bfd2eac", "official_r_c2022362_e1d8ad4c_query_parallel_v2"),
    ("f3496ca5", "official_r_c2022362_e97b0c61_v1"),
    ("f3496ca5", "official_r_c2022362_e1d8ad4c_query_parallel_v2"),
    ("85251f36", "official_r_c2022362_e1d8ad4c_query_parallel_v2"),
    ("1bfd2eac", "official_r_c2022362_e97b0c61_v1#original"),
]
RES_REF = {"1bfd2eac": "change-request", "f3496ca5": "two", "85251f36": "hardware"}
PAGE_CHARS = 12000          # raw AX characters kept per fetched page
MAX_FETCH = 3               # pages fetched per G request
MAX_TOKENS = 4096
TOK = re.compile(r"[a-z0-9]+")


# ---------------------------------------------------------------- shared helpers
def toks(s):
    return TOK.findall(s.lower())


class BM25:
    def __init__(self, docs, k1=1.2, b=0.75):
        self.docs = [collections.Counter(toks(d)) for d in docs]
        self.len = [sum(c.values()) for c in self.docs]
        self.avg = sum(self.len) / max(1, len(self.len))
        df = collections.Counter(t for c in self.docs for t in c)
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.k1, self.b = k1, b

    def top(self, query, k):
        q = toks(query)
        sc = []
        for i, c in enumerate(self.docs):
            s = 0.0
            for t in q:
                if t in c:
                    f = c[t]
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.len[i] / self.avg))
            sc.append((s, i))
        sc.sort(reverse=True)
        return [i for s, i in sc[:k] if s > 0]


def load_states(traj_ids):
    out = []
    for line in open(TRAJ):
        r = json.loads(line)
        if r["id"] not in traj_ids:
            continue
        for s in r["states"]:
            ax = s["accessibility_tree"] or ""
            m = re.match(r"RootWebArea '([^']*)'", ax)
            out.append({"trajectory": r["id"], "state": s["state_index"], "goal": " ".join(r["goal"].split())[:300],
                        "title": m.group(1) if m else "", "url": s["url"] or "", "ax": ax})
    return out


def state_block(s, chars=PAGE_CHARS):
    return (f"### Raw page: {s['title']} (task: {s['goal']}; trajectory {s['trajectory']}, step {s['state']})\n"
            f"URL: {s['url'][:200]}\n{s['ax'][:chars]}")


def boxed(text):
    m = re.findall(r"\\boxed\{([^{}]*)\}", text or "")
    return m[-1].strip() if m else None


def norm(x):
    if isinstance(x, bool):  # JSON true/false answers to yes/no questions
        x = "yes" if x else "no"
    return re.sub(r"[^a-z0-9]+", "", str(x).lower())


# ---------------------------------------------------------------- residual stage
def residual_items():
    items = []
    for qid, ns in RESIDUAL:
        arm = "rerank"
        if "#" in ns:
            ns, arm = ns.split("#")
        rec = json.load(open(os.path.join(ROOT, "runs", ns, f"{qid}_{arm}.json")))
        msgs = []
        for m in rec["messages_for_log"]:
            if isinstance(m["content"], str):
                msgs.append({"role": m["role"], "content": m["content"]})
                continue
            parts = []
            for p in m["content"]:
                if p["type"] == "text":
                    parts.append({"type": "text", "text": p["text"]})
                else:
                    parts.append({"type": "image_url", "image_url": {"url": "file://" + p["image_path"]}})
            msgs.append({"role": m["role"], "content": parts})
        items.append({"case": f"{qid}:{ns[22:30]}:{arm}", "qid": qid, "messages": msgs, "question": rec["question"]})
    return items


GAP_PROMPT = ("\n\nDo NOT answer the question yet. First list which pages, forms or records this question needs "
              "(every entity it compares or counts), and mark each one as present or missing in the memory context "
              "above; a page is missing if its relevant content (fields, options, tabs, rows) is not shown. Output "
              "only JSON: {\"needed\": [{\"page\": \"<page or form title as it would appear in the browser tab>\", "
              "\"present\": true|false, \"why\": \"<what is missing>\"}]}")


def append_text(messages, text):
    msgs = json.loads(json.dumps(messages))
    msgs[-1]["content"].append({"type": "text", "text": text})
    return msgs


def gap_messages(messages):
    """v2: replace the final question part (which ends with the \\boxed answer instruction) by the bare
    question plus the gap request, so the reader cannot answer instead of listing gaps."""
    msgs = json.loads(json.dumps(messages))
    last = msgs[-1]["content"][-1]
    q = last["text"].split("Mark your final answer")[0].split("Put your final answer")[0].rstrip()
    last["text"] = q + GAP_PROMPT
    return msgs


def missing_from(js):
    if not js:
        return []
    if "needed" in js:
        return [m for m in js["needed"] if not m.get("present", True)]
    return js.get("missing") or []


def fetch_pages(states, missing):
    """Pick, for each named missing page, the latest raw state whose title best matches."""
    picked = []
    for m in missing[:MAX_FETCH]:
        want = set(toks(m.get("page", "")))
        if not want:
            continue
        best = None
        for s in states:
            have = set(toks(s["title"]))
            ov = len(want & have) / len(want)
            prec = len(want & have) / max(1, len(have))
            key = (ov, prec, len(s["ax"]) > 2000, s["state"])
            if ov > 0 and (best is None or key > best[0]):
                best = (key, s)
        if best and best[1] not in picked:
            picked.append(best[1])
    return picked


# ---------------------------------------------------------------- dev38 stage
def obs_block(o):
    p = o["page"]
    head = (f"[source task: {' '.join(o['goal'].split())[:300]} | trajectory {o['trajectory']} | step {o['state']}] "
            f"table={o['table']} | filter={' AND '.join(o['conditions']) or '(none)'} | "
            f"showing rows {p['first']}-{p['last']} of {p['total']} | parsed rows {len(o['rows'])}")
    lines = [head]
    for r in o["rows"]:
        lines.append("  - " + "; ".join(f"{c}={v}" for c, v in r["values"].items() if v not in ("",)))
    return "\n".join(lines)


def dedupe(obs):
    out, last = [], {}
    for o in sorted(obs, key=lambda o: (o["trajectory"], o["state"])):
        sig = json.dumps([o["table"], o["conditions"], o["page"], [r["values"] for r in o["rows"]]], sort_keys=True)
        if last.get((o["trajectory"], o["table"])) == sig:
            continue
        last[(o["trajectory"], o["table"])] = sig
        out.append(o)
    return out


ANSWER_FMT = ("Reply with only JSON: {\"sufficient\": true|false, \"answer\": <answer or null>, \"observed_lower_bound\": "
              "<number or null>, \"reason\": \"<one sentence>\"}. For lists give a JSON list. If the memory does not "
              "determine the answer, set sufficient=false and answer=null.")
SCOPE_CHECK = ("Before answering, check the scope of the evidence: (1) it comes from the task the question names, "
               "(2) it is the same table/entity, (3) the filter matches exactly what is asked, (4) the page covers all "
               "rows (rows a-b of N with b=N, all rows parsed) when the question needs a complete set, a count with "
               "an extra condition, a negative answer or a difference, and (5) you use the most recent view when "
               "the list was shown more than once. If coverage is not enough, say so and give the observed lower bound.")
INTENT_PROMPT = """Convert the question into a query intent for a scoped list memory. Output only JSON with keys:
operator: one of lookup, lookup_latest, exists, count, enumerate, difference
anchor_trajectory: the trajectory id of the task named in the question (choose from the list)
table: one of the table names listed for that trajectory
conditions: the filter the question asks about, as a list of condition strings. If it matches one of the observed filters of that table, copy that list exactly; otherwise write the conditions in the same style. Use [] when the question asks about the full, unfiltered list
column, value: the column and value the question restricts or asks about (null if none)
key_col, key: for lookup/lookup_latest the identifying column and its value; for enumerate/difference key_col is the column to list
Use lookup_latest when the question says the list was shown more than once; difference for appeared-first-but-not-last.

Tasks in memory:
{tasks}

Question: {q}"""


def dev_items(views_by_traj, states_by_traj):
    items = []
    for line in open(DEV):
        q = json.loads(line)
        hay = q["haystack"]
        obs = dedupe([o for t in hay for o in views_by_traj.get(t, []) if o["table"] == q["scope"]["table"]])
        table_ctx = "\n\n".join(obs_block(o) for o in obs)
        states = [s for t in hay for s in states_by_traj.get(t, [])]
        bm = BM25([s["title"] + " " + s["goal"] + " " + s["ax"][:PAGE_CHARS] for s in states])
        raw_ctx = "\n\n".join(state_block(states[i]) for i in bm.top(q["question"], 5))
        tasks = []
        for t in hay:  # v2: full task text plus the tables and columns each session observed
            vs = views_by_traj.get(t, [])
            if not vs:
                continue
            tabs = collections.OrderedDict()
            filt = collections.defaultdict(list)
            for o in vs:
                tabs.setdefault(o["table"], set()).update(o["columns"])
                if o["conditions"] not in filt[o["table"]]:
                    filt[o["table"]].append(o["conditions"])
            # v2.1: also list the filter condition lists observed for each table (scope metadata held in memory)
            tasks.append(f"- trajectory {t}: {' '.join(vs[0]['goal'].split())[:300]}\n  tables:\n"
                         + "\n".join(f"    {tb} (columns: {', '.join(sorted(c))}; observed filters: "
                                     + " | ".join(json.dumps(f) for f in filt[tb][:15]) + ")"
                                     for tb, c in tabs.items()))
        tasks = "\n".join(tasks)
        items.append({"q": q, "table_ctx": table_ctx, "raw_ctx": raw_ctx, "tasks": tasks, "n_obs": len(obs)})
    return items


def dev_messages(it, arm):
    q = it["q"]["question"]
    if arm == "B0":
        body = f"Memory (raw pages from earlier sessions):\n{it['raw_ctx']}\n\nQuestion: {q}\n\n{ANSWER_FMT}"
    elif arm == "B1":
        body = f"Memory (list views observed in earlier sessions):\n{it['table_ctx']}\n\nQuestion: {q}\n\n{ANSWER_FMT}"
    elif arm == "B2":
        body = f"Memory (list views observed in earlier sessions):\n{it['table_ctx']}\n\nQuestion: {q}\n\n{SCOPE_CHECK}\n{ANSWER_FMT}"
    elif arm == "S":
        body = INTENT_PROMPT.format(tasks=it["tasks"], q=q)
    return [{"role": "user", "content": body}]


def parse_json(text):
    text = text or ""
    m = re.search(r"\{.*\}", text, re.S)
    cands = [m.group(0)] if m else []
    i = text.find("{")
    if i >= 0:  # replies that stop right before the closing brace
        cands.append(text[i:].rstrip().rstrip(",") + "}")
    for c in cands:
        try:
            return json.loads(c)
        except json.JSONDecodeError:
            continue
    # last resort: unescaped quotes inside "reason" break the JSON; read the answer fields one by one
    out = {}
    m = re.search(r'"sufficient"\s*:\s*(true|false)', text)
    if m:
        out["sufficient"] = m.group(1) == "true"
    m = re.search(r'"answer"\s*:\s*(.*?),\s*"(?:observed_lower_bound|reason|sufficient)"', text, re.S)
    if m:
        try:
            out["answer"] = json.loads(m.group(1))
        except json.JSONDecodeError:
            return None
    m = re.search(r'"observed_lower_bound"\s*:\s*(-?\d+|null)', text)
    if m:
        out["observed_lower_bound"] = None if m.group(1) == "null" else int(m.group(1))
    return out if "answer" in out else None


def same(a, b):
    if isinstance(b, list):
        return isinstance(a, list) and sorted(map(norm, a)) == sorted(map(norm, b))
    return a is not None and norm(a) == norm(b)


def score_dev(pred, q):
    g = q["gold"]
    if pred is None:
        return {"correct": False, "unsupported": False, "wrong_refusal": False, "parse_fail": True}
    ans = pred.get("answer")
    # some replies omit the flag; an explicit answer then counts as claiming sufficiency
    suff = bool(pred["sufficient"]) if "sufficient" in pred else ans is not None
    if g["source_sufficient"]:
        ok = suff and same(ans, g["answer"])
        return {"correct": ok, "unsupported": False, "wrong_refusal": not suff, "parse_fail": False}
    lb = pred.get("observed_lower_bound")
    return {"correct": not suff, "unsupported": suff and ans is not None, "wrong_refusal": False, "parse_fail": False,
            "lower_bound_ok": (lb is not None and g["observed_lower_bound"] is not None and str(lb) == str(g["observed_lower_bound"]))}


def executor_to_pred(res):
    if res["status"] == "answered":
        return {"sufficient": True, "answer": res["answer"]}
    return {"sufficient": False, "answer": None, "observed_lower_bound": res.get("observed_lower_bound")}


# ---------------------------------------------------------------- runner
class Reader:
    def __init__(self, dry):
        self.dry = dry
        if dry:
            return
        from vllm import LLM, SamplingParams
        # defaults = the 9B setting of all earlier runs; env overrides only for the stronger-reader ablation
        tp = int(os.environ.get("SCOPE02_TP", "1"))
        self.llm = LLM(model=MODEL, dtype="auto" if "FP8" in MODEL else "bfloat16",
                       max_model_len=int(os.environ.get("SCOPE02_MAXLEN", "200000")),
                       gpu_memory_utilization=float(os.environ.get("SCOPE02_GPU_UTIL", "0.88")),
                       tensor_parallel_size=tp, max_num_seqs=8, enforce_eager=True,
                       limit_mm_per_prompt={"image": 32, "video": 0}, allowed_local_media_path=ROOT)
        self.sp = SamplingParams(temperature=0.0, max_tokens=MAX_TOKENS)

    def chat(self, batch):
        if self.dry:
            return [{"text": "", "finish": "dry", "prompt_chars": len(json.dumps(m)), "prompt_tokens": None,
                     "output_tokens": 0} for m in batch]
        t = time.time()
        outs = self.llm.chat(batch, self.sp, chat_template_kwargs={"enable_thinking": False}, use_tqdm=False)
        dt = time.time() - t
        return [{"text": o.outputs[0].text, "finish": o.outputs[0].finish_reason,
                 "prompt_tokens": len(o.prompt_token_ids), "output_tokens": len(o.outputs[0].token_ids),
                 "batch_seconds": dt} for o in outs]


def run_residual(reader, out, limit):
    items = residual_items()[:limit]
    hay = {it["qid"]: json.load(open(HAY))[it["qid"]] for it in items}
    states = {qid: load_states(set(t)) for qid, t in hay.items()}
    r0 = reader.chat([it["messages"] for it in items])
    gap = reader.chat([gap_messages(it["messages"]) for it in items])
    g_msgs, e_msgs, meta = [], [], []
    for it, gp in zip(items, gap):
        missing = missing_from(parse_json(gp["text"]))
        pages = fetch_pages(states[it["qid"]], missing)
        add = "\n\n".join(state_block(s) for s in pages)
        bm = BM25([s["title"] + " " + s["ax"][:PAGE_CHARS] for s in states[it["qid"]]])
        extra, used = [], 0
        for i in bm.top(it["question"], 20):
            if used >= max(len(add), 1):
                break
            blk = state_block(states[it["qid"]][i])[: max(len(add), 1) - used]
            extra.append(blk)
            used += len(blk)
        g_msgs.append(append_text(it["messages"], "\n\n### Supplementary raw pages fetched for the missing items:\n" + (add or "(none found)")))
        e_msgs.append(append_text(it["messages"], "\n\n### Additional retrieved raw pages:\n" + ("\n\n".join(extra) or "(none)")))
        meta.append({"missing": missing, "fetched": [(s["trajectory"], s["state"], s["title"]) for s in pages],
                     "appended_chars": len(add), "extra_chars": used})
    g = reader.chat(g_msgs)
    e = reader.chat(e_msgs)
    rows = []
    for it, a, b, c, d, m in zip(items, r0, gap, g, e, meta):
        ref = RES_REF[it["qid"]]
        row = {"case": it["case"], "ref": ref, "meta": m}
        for name, o in (("R0", a), ("gap", b), ("G", c), ("E", d)):
            row[name] = o | {"boxed": boxed(o["text"])}
            if name != "gap":
                row[name]["correct"] = boxed(o["text"]) is not None and norm(boxed(o["text"])) == norm(ref)
        rows.append(row)
    json.dump(rows, open(os.path.join(out, "residual_results.json"), "w"), indent=1, ensure_ascii=False)
    summ = {k: sum(r[k].get("correct", False) for r in rows) for k in ("R0", "G", "E")}
    summ["n"] = len(rows)
    print("residual", json.dumps(summ))
    return summ


def run_dev38(reader, out, limit):
    views_by_traj = collections.defaultdict(list)
    for line in open(VIEWS):
        o = json.loads(line)
        views_by_traj[o["trajectory"]].append(o)
    qs = [json.loads(l) for l in open(DEV)][:limit]
    trajs = {t for q in qs for t in q["haystack"]}
    states_by_traj = collections.defaultdict(list)
    for s in load_states(trajs):
        states_by_traj[s["trajectory"]].append(s)
    items = dev_items(views_by_traj, states_by_traj)[:limit]
    mem = ScopeMemory([o for t in trajs for o in views_by_traj.get(t, [])])
    res = {}
    for arm in ("B0", "B1", "B2", "S"):
        res[arm] = reader.chat([dev_messages(it, arm) for it in items])
        json.dump(res[arm], open(os.path.join(out, f"raw_{arm}.json"), "w"), ensure_ascii=False)  # keep outputs even if scoring fails
    rows, tot = [], collections.defaultdict(collections.Counter)
    for i, it in enumerate(items):
        q = it["q"]
        row = {"qid": q["qid"], "type": q["type"], "sufficient": q["gold"]["source_sufficient"], "n_obs": it["n_obs"]}
        for arm in ("B0", "B1", "B2"):
            pred = parse_json(res[arm][i]["text"])
            row[arm] = {"out": res[arm][i], "pred": pred, "score": score_dev(pred, q)}
        intent = parse_json(res["S"][i]["text"])
        try:
            ex = mem.answer(intent) if intent else {"status": "parse_fail"}
            pred = executor_to_pred(ex) if intent else None
        except Exception as err:  # malformed intent counts against S
            ex, pred = {"status": "executor_error", "error": repr(err)}, None
        row["S"] = {"out": res["S"][i], "intent": intent, "exec": ex, "pred": pred, "score": score_dev(pred, q)}
        # the oracle reads its structured slots from the template wording, also for paraphrased questions
        ex_o = mem.answer(intent_from_question(q | {"question": q.get("template_question", q["question"])}))
        row["S_oracle"] = {"exec": ex_o, "score": score_dev(executor_to_pred(ex_o), q)}
        for arm in ("B0", "B1", "B2", "S", "S_oracle"):
            sc = row[arm]["score"]
            k = "suff" if q["gold"]["source_sufficient"] else "insuff"
            tot[arm][k + "_correct"] += sc["correct"]
            tot[arm][k + "_n"] += 1
            tot[arm]["unsupported"] += sc["unsupported"]
            tot[arm]["wrong_refusal"] += sc["wrong_refusal"]
            tot[arm]["parse_fail"] += sc["parse_fail"]
            if arm != "S_oracle":
                tot[arm]["prompt_tokens"] += row[arm]["out"].get("prompt_tokens") or 0
                tot[arm]["prompt_chars"] += row[arm]["out"].get("prompt_chars") or 0
        rows.append(row)
    json.dump(rows, open(os.path.join(out, "dev38_results.json"), "w"), indent=1, ensure_ascii=False, default=str)
    summ = {a: dict(c) for a, c in tot.items()}
    json.dump(summ, open(os.path.join(out, "dev38_summary.json"), "w"), indent=1)
    print("dev38", json.dumps(summ))
    return summ


def run_evsel(reader, out, limit):
    """Evidence-selection control: the same reader and prompts as B1/B2, but fed only selected observations.
    sel_scope = the observations bound by the question's true scope (source, table, filter; all versions), i.e.
    exactly what the S executor reads; when that filter was never observed, the same-source same-table observations.
    sel_table = all same-source same-table observations (right session and entity, no filter/version selection)."""
    views = [json.loads(l) for l in open(VIEWS)]
    qs = [json.loads(l) for l in open(DEV)][:limit]
    hay = {t for q in qs for t in q["haystack"]}
    mem = ScopeMemory([o for o in views if o["trajectory"] in hay])
    batches = {a: [] for a in ("B1_sel_scope", "B2_sel_scope", "B2_sel_table")}
    meta = []
    for q in qs:
        it = mem.normalize(intent_from_question(q | {"question": q.get("template_question", q["question"])}))
        same_tab = [o for o in mem.obs if o["trajectory"] == it["anchor_trajectory"] and o["table"] == it["table"]]
        bound = mem.bind(it)
        sel_scope = dedupe(bound if bound else same_tab)
        sel_table = dedupe(same_tab)
        ctx_s = "\n\n".join(obs_block(o) for o in sel_scope)
        ctx_t = "\n\n".join(obs_block(o) for o in sel_table)
        mem_s = f"Memory (list views observed in earlier sessions):\n{ctx_s}\n\nQuestion: {q['question']}\n\n"
        mem_t = f"Memory (list views observed in earlier sessions):\n{ctx_t}\n\nQuestion: {q['question']}\n\n"
        batches["B1_sel_scope"].append([{"role": "user", "content": mem_s + ANSWER_FMT}])
        batches["B2_sel_scope"].append([{"role": "user", "content": mem_s + SCOPE_CHECK + "\n" + ANSWER_FMT}])
        batches["B2_sel_table"].append([{"role": "user", "content": mem_t + SCOPE_CHECK + "\n" + ANSWER_FMT}])
        meta.append({"qid": q["qid"], "bound": bool(bound), "n_sel_scope": len(sel_scope), "n_sel_table": len(sel_table)})
    res = {}
    for arm, b in batches.items():
        res[arm] = reader.chat(b)
        json.dump(res[arm], open(os.path.join(out, f"raw_{arm}.json"), "w"), ensure_ascii=False)
    rows, tot = [], collections.defaultdict(collections.Counter)
    for i, q in enumerate(qs):
        row = dict(meta[i], type=q["type"], sufficient=q["gold"]["source_sufficient"])
        k = "suff" if q["gold"]["source_sufficient"] else "insuff"
        for arm in batches:
            pred = parse_json(res[arm][i]["text"])
            sc = score_dev(pred, q)
            row[arm] = {"out": res[arm][i], "pred": pred, "score": sc}
            tot[arm][k + "_correct"] += sc["correct"]
            tot[arm][k + "_n"] += 1
            for f in ("unsupported", "wrong_refusal", "parse_fail"):
                tot[arm][f] += sc[f]
            tot[arm]["prompt_tokens"] += res[arm][i].get("prompt_tokens") or 0
        rows.append(row)
    json.dump(rows, open(os.path.join(out, "evsel_results.json"), "w"), indent=1, ensure_ascii=False, default=str)
    summ = {a: dict(c) for a, c in tot.items()}
    json.dump(summ, open(os.path.join(out, "evsel_summary.json"), "w"), indent=1)
    print("evsel", json.dumps(summ))
    return summ


def main():
    global DEV
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="all")
    ap.add_argument("--out", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=10 ** 6)
    ap.add_argument("--questions", default=DEV, help="question jsonl for the dev38 stage (dev v2 or confirmation)")
    a = ap.parse_args()
    DEV = a.questions
    os.makedirs(a.out, exist_ok=True)
    reader = Reader(a.dry_run)
    t = time.time()
    log = {"stage": a.stage, "dry_run": a.dry_run, "limit": a.limit, "start": t}
    if a.stage in ("residual", "all"):
        log["residual"] = run_residual(reader, a.out, a.limit)
    if a.stage == "evsel":
        log["evsel"] = run_evsel(reader, a.out, a.limit)
    if a.stage in ("dev38", "all"):
        log["dev38"] = run_dev38(reader, a.out, a.limit)
    log["seconds"] = time.time() - t
    json.dump(log, open(os.path.join(a.out, "run_log.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
