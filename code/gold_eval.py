"""Evaluation on blind-annotated question sets (annotation_dev_v1 for development, gold_v1 for the frozen test).

Memory = every distinct page of the session (the rendered pages/<session>.md files the annotators saw).
Arms (same reader, same prompts):
  R        : BM25 top-5 pages -> reader answers JSON {operator, determined, answer, lower_bound, evidence_pages}
  Full     : all pages of the session (long-context baseline; capped at FULL_CHARS)
  R+G      : if R says not determined -> reader names missing pages -> fetched by title/URL match -> re-answer
  R+gate   : coverage gate on R's answer (page_coverage on the cited pages; non-monotone operators on a partial
             listing become "not determined" with the visible count as lower bound)
  R+G+gate : gate applied after G
  Full+gate: gate on Full
Generation only; judging is a separate stage (--judge) so the judge can be validated independently.

Usage:
  python gold_eval.py --set DIR --out OUT [--dry-run] [--limit N]           # generation
  python gold_eval.py --set DIR --out OUT --judge                           # LLM judging of determined answers
  python gold_eval.py --set DIR --out OUT --score                           # final tables (no model)
"""
import argparse
import collections
import json
import os
import re
import time

import page_coverage as C
import run_scope_experiments as R

TOPK = 5
PAGE_CHARS = 16000
FULL_CHARS = 360000
NONMONO = {"count", "list", "extreme", "negation", "change"}
ANS_FMT = ("Reply with only JSON: {\"operator\": \"<lookup|exists|count|list|extreme|negation|change>\", "
           "\"determined\": true|false, \"answer\": <short answer, a list, or null>, \"lower_bound\": <number or null>, "
           "\"evidence_pages\": [\"P..\"], \"reason\": \"<one sentence>\"}. Use determined=false and answer=null when "
           "the pages shown do not settle the question; then give what is known as lower_bound when it is a count.")
GAP_FMT = ("Do NOT answer yet. Which page of this session, not shown above, would you need to settle the question? "
           "Output only JSON: {\"needed\": [{\"page\": \"<words from its title or URL>\", \"why\": \"...\"}]} "
           "with at most two items, or an empty list if nothing is missing.")
JUDGE_FMT = ("You grade an answer against a gold answer for a question about a web session. The gold answer is "
             "authoritative. Reply with only JSON {\"correct\": true|false}. Count it correct if it states the same "
             "fact (formatting, extra words, order of list items do not matter); incorrect if any required item or "
             "value is wrong or missing.\n\nQuestion: {q}\nGold answer: {g}\nSystem answer: {a}")


def load_set(d):
    gold = [json.loads(l) for l in open(os.path.join(d, "gold.jsonl"), encoding="utf-8")]
    pages = {}
    for s in {g["session"] for g in gold}:
        md = open(os.path.join(d, "pages", s + ".md"), encoding="utf-8").read()
        task = md.split("Task the agent was doing:", 1)[1].split("\n## Page", 1)[0].strip()
        ps = []
        for blk in re.split(r"\n## Page ", md)[1:]:
            pid = blk.split(" ", 1)[0]
            url = (re.search(r"\nURL: (.*)", blk) or [None, ""])[1]
            body = (re.search(r"```\n(.*)\n```", blk, re.S) or [None, ""])[1]
            ps.append({"pid": pid, "url": url, "text": body[:PAGE_CHARS], "title": body.split("\n", 1)[0]})
        pages[s] = {"task": task, "pages": ps}
    return gold, pages


def block(p, with_note):
    n = C.note(C.read(p["text"])) if with_note else ""
    return f"## Page {p['pid']}\nURL: {p['url']}\n{n + chr(10) if n else ''}{p['text']}"


def msgs(task, blocks, q, fmt):
    body = (f"Memory: pages a web agent saw in an earlier session.\nTask of that session: {task}\n\n"
            + "\n\n".join(blocks) + f"\n\nQuestion: {q}\n\n{fmt}")
    return [{"role": "user", "content": body}]


PAGE_SCOPED = re.compile(r"\b(first page|on (?:the|this|that) page|listed first|first (?:row|result|item|entry|one)"
                         r"|top of the (?:list|page)|page \d+|first \d+)\b", re.I)
PAIRWISE = re.compile(r"\b(?:which|whether)\b.*\bor\b", re.I)


def _numbers(x):
    return [int(n.replace(",", "")) for n in re.findall(r"\d[\d,]*", json.dumps(x))]


def gate(pred, sess_pages, question=""):
    """Programmatic coverage gate (rules tuned on annotation_dev_v1 only); returns (pred, fired).

    Fires only when (a) the answer claims something about a whole listing that is non-monotone (count, list,
    extreme, a negative existence, or a change of a count/list), (b) a cited evidence page shows that listing
    only partially, and (c) the question is not explicitly scoped to the visible page, is not a comparison of
    two named items, and the answer is not simply the listing size the page itself states."""
    if not pred or not pred.get("determined"):
        return pred, False
    op = str(pred.get("operator", "")).lower()
    ans = pred.get("answer")
    neg = op == "exists" and str(ans).strip().lower() in ("no", "false", "none", "0")
    if op not in NONMONO and not neg:
        return pred, False
    if PAGE_SCOPED.search(question) or (op in ("extreme", "change") and PAIRWISE.search(question)):
        return pred, False
    if op == "change" and not isinstance(ans, list) and not _numbers(ans):
        return pred, False  # a single record's value before/after, not a listing
    by = {p["pid"]: p for p in sess_pages}
    covs = [C.read(by[e]["text"]) for e in pred.get("evidence_pages") or [] if e in by]
    partial = [c for c in covs if c["status"] == "partial"]
    if not partial:
        return pred, False
    stated = {c[k] for c in covs for k in ("total", "shown") if c[k] is not None}
    nums = _numbers(ans)
    if op in ("count", "change") and nums and all(n in stated for n in nums):
        return pred, False  # the page states this listing size itself
    lb = partial[0]["shown"]
    return dict(pred, determined=False, answer=None, lower_bound=lb, gated=True), True


# ---- gate v2 (2026-10-24): rules from error analysis on annotation_dev_v1 and gold_v1 (gold_v1 is now a dev set;
# confirmation must use a fresh blind set) ----
OBSERVED_SCOPED = re.compile(
    r"\b(did you see|you saw|(?:you )?(?:had )?seen|appear(?:ed|s)?|(?:was|were|is|are) (?:shown|displayed|visible|listed)"
    r"|show(?:ed|n)? (?:in|on)|according to|on the page|already (?:displayed|shown|visible)|you started on)\b", re.I)
NON_ROW_ROLES = ("columnheader", "button", "tab ", "option", "combobox", "heading", "menuitem", "checkbox", "radio")


def _row_like(item, pages_text):
    """True if the item occurs on some line that is not a column header / control / heading."""
    s = str(item).strip()
    if not s:
        return True
    found = False
    for t in pages_text:
        for ln in t.split("\n"):
            if s in ln:
                found = True
                if not ln.startswith(NON_ROW_ROLES):
                    return True
    return not found  # not found at all: no evidence it is a control, treat as row-like


def gate_v2(pred, sess_pages, question=""):
    """v1 rules plus: tightened pager evidence (page_coverage.read_v2); observation-scoped questions ("did you
    see", "appeared", "according to", ...) are never gated; counts/changes whose numbers are printed on a cited
    page, or are differences of printed numbers, are reads, not enumerations; list answers whose items occur only
    as column headers / controls are not listing rows."""
    if not pred or not pred.get("determined"):
        return pred, False
    op = str(pred.get("operator", "")).lower()
    ans = pred.get("answer")
    neg = op == "exists" and str(ans).strip().lower() in ("no", "false", "none", "0")
    if op not in NONMONO and not neg:
        return pred, False
    if PAGE_SCOPED.search(question) or OBSERVED_SCOPED.search(question) \
            or (op in ("extreme", "change") and PAIRWISE.search(question)):
        return pred, False
    if op == "change" and not isinstance(ans, list) and not _numbers(ans):
        return pred, False
    by = {p["pid"]: p for p in sess_pages}
    cited = [by[e]["text"] for e in pred.get("evidence_pages") or [] if e in by]
    covs = [C.read_v2(t) for t in cited]
    partial = [c for c in covs if c["status"] == "partial"]
    if not partial:
        return pred, False
    if (neg or (op == "count" and _numbers(ans) == [0])) and any(C.states_none(t) for t in cited):
        return pred, False  # the page itself states the listing is empty
    nums = _numbers(ans)
    if op in ("count", "change", "extreme") and nums:
        stated = set().union(*(C.stated_counts(t) for t in cited)) if cited else set()
        shown = {c["shown"] for c in partial if c["shown"] is not None}
        readable = (stated - shown) | {abs(a - b) for a in stated for b in stated if a != b}
        if all(n in readable for n in nums):
            return pred, False
    if op == "list" and isinstance(ans, list) and ans and not any(_row_like(i, cited) for i in ans):
        return pred, False
    lb = partial[0]["shown"]
    return dict(pred, determined=False, answer=None, lower_bound=lb, gated=True), True


# ---- gate v3 (2026-10-24): structured question scope (parse_scope.py, a fixed 9B parser that reads only the
# question) + container-level coverage (container_coverage.py, from the raw indented AX tree) ----
def _norm_txt(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


def _tok(s):
    return set(t for t in _norm_txt(s).split() if len(t) > 2)


def choose_container(pred, scope, page_containers):
    """Container the answer ranges over: the one whose entries contain most answer items; otherwise the one
    whose label/name/columns/first entries best match the parsed collection."""
    cands = [c for pid in pred.get("evidence_pages") or [] for c in page_containers.get(pid, [])]
    cands = [c for c in cands if c["n_entries"] >= 1]
    # a listbox is a selection control (e.g. "Search a specific field"), not a listing of records
    cands = [c for c in cands if c["role"] != "listbox"] or cands
    if not cands:
        return None
    ans = pred.get("answer")
    items = [a for a in (ans if isinstance(ans, list) else [ans]) if a is not None and len(_norm_txt(a)) >= 3]
    if items:
        def hits(c):
            blob = " || ".join(_norm_txt(e) for e in c["entries"])
            return sum(1 for a in items if _norm_txt(a) in blob)
        best = max(cands, key=lambda c: (hits(c), c["status"] != "unknown", c["n_entries"]))
        if hits(best) > 0:
            return best
    want = _tok((scope or {}).get("collection", ""))
    if not want:
        return None

    def score(c):
        have = _tok(" ".join([c["label"], c["name"]] + c["columns"] + c["entries"][:3]))
        return len(want & have) / max(1, len(want))
    # ties (same heading over a grid, its filter listbox and a sidebar list): prefer the container that carries
    # coverage evidence, then the larger one
    best = max(cands, key=lambda c: (score(c), c["status"] != "unknown", c["n_entries"]))
    return best if score(best) > 0 else None


def gate_v3(pred, question, scope, page_containers):
    """Fires only when the parsed question asks about a whole collection (not just what was seen), the answer must be
    enumerated (not a stated number, not a single record, not the top of a sorted list), the operator is
    non-monotone, and the container holding the answer is shown only partially."""
    if not pred or not pred.get("determined") or not scope:
        return pred, False
    op = str(scope.get("operator") or pred.get("operator") or "").lower()
    ans = pred.get("answer")
    neg = op == "exists" and str(ans).strip().lower() in ("no", "false", "none", "0", "[]")
    if op not in NONMONO and not neg:
        return pred, False
    if scope.get("scope") != "whole" or scope.get("answer_source") != "enumerated" or scope.get("top_of_sorted"):
        return pred, False
    c = choose_container(pred, scope, page_containers)
    if c and c["status"] == "partial":
        return dict(pred, determined=False, answer=None, lower_bound=c["shown"], gated=True), True
    if c and c["status"] == "complete":
        return pred, False
    # container coverage unknown or no container matched: fall back to the page-level reading (v2 rules) of the
    # cited pages, but only for pages where the matched container (if any) is the page's largest listing
    return pred, False


def gate_v3b(pred, question, scope, page_containers, sess_pages):
    """v3 with fallback: structured scope decides WHETHER coverage matters; coverage is read per container when the
    container carries its own evidence, otherwise from the page (read_v2) if the answer's container is the page's
    main listing or no container could be matched."""
    if not pred or not pred.get("determined") or not scope:
        return pred, False
    op = str(scope.get("operator") or pred.get("operator") or "").lower()
    ans = pred.get("answer")
    neg = op == "exists" and str(ans).strip().lower() in ("no", "false", "none", "0", "[]")
    if op not in NONMONO and not neg:
        return pred, False
    if scope.get("scope") != "whole" or scope.get("answer_source") != "enumerated" or scope.get("top_of_sorted"):
        return pred, False
    if op in ("extreme", "change") and PAIRWISE.search(question):
        return pred, False  # compares two named records ("X or Y"), not a ranking over the collection
    by = {p["pid"]: p for p in sess_pages}
    nums = _numbers(ans)
    if op in ("count", "change", "extreme") and nums:
        # numbers the pages state (totals, tab counts, range totals) are reads, not enumerations
        cited = [by[e]["text"] for e in pred.get("evidence_pages") or [] if e in by]
        stated = set().union(*(C.stated_counts(t) for t in cited)) if cited else set()
        stated |= {x["total"] for e in pred.get("evidence_pages") or [] for x in page_containers.get(e, [])
                   if x.get("total")}
        diffs = {abs(a - b) for a in stated for b in stated if a != b} if op == "change" else set()
        if all(n in stated or n in diffs for n in nums):
            return pred, False
    c = choose_container(pred, scope, page_containers)
    items = [_norm_txt(a) for a in (ans if isinstance(ans, list) else [ans]) if a is not None and len(_norm_txt(a)) >= 3]
    cols = [h for e in pred.get("evidence_pages") or [] for x in page_containers.get(e, []) for h in x["columns"]]
    if items and all(any(i in _norm_txt(h) for h in cols) for i in items):
        return pred, False  # the answer ranges over the listing's columns, which are all shown
    if c and c["status"] == "partial":
        return dict(pred, determined=False, answer=None, lower_bound=c["shown"], gated=True), True
    if c and c["status"] == "complete":
        return pred, False
    for pid in pred.get("evidence_pages") or []:
        if pid not in by:
            continue
        conts = page_containers.get(pid, [])
        main = max(conts, key=lambda x: x["n_entries"]) if conts else None
        if c is not None and main is not None and c is not main and c["n_entries"] < main["n_entries"]:
            continue  # the answer lives in a secondary container; the page's pager belongs to another listing
        if (neg or op == "count") and C.states_none(by[pid]["text"]):
            return pred, False
        cov = C.read_v2(by[pid]["text"])
        if cov["status"] == "partial":
            return dict(pred, determined=False, answer=None, lower_bound=cov["shown"], gated=True), True
    return pred, False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=10 ** 6)
    ap.add_argument("--judge", action="store_true")
    ap.add_argument("--score", action="store_true")
    ap.add_argument("--export-judge", action="store_true")
    ap.add_argument("--import-judge", default=None, help="JSONL with {id, correct} from an external judge")
    ap.add_argument("--gate3", default=None, help="PARSE_JSON,CONTAINERS_JSON: add <arm>+gate3 to preds.json")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    gold, pages = load_set(a.set)
    gold = gold[: a.limit]
    if a.export_judge:
        return export_judge(gold, a.out)
    if a.import_judge:
        return import_judge(a.out, a.import_judge)
    if a.gate3:
        return add_gate3(gold, pages, a.out, *a.gate3.split(","))
    if a.score:
        return score(gold, a.set, a.out)
    reader = R.Reader(a.dry_run)
    if a.judge:
        return judge(gold, a.out, reader)
    t0 = time.time()
    items = []
    for g in gold:
        sp = pages[g["session"]]
        docs = [p["title"] + " " + p["url"] + " " + p["text"] for p in sp["pages"]]
        ranked = R.BM25(docs).top(g["question"], len(docs))
        ranked += [i for i in range(len(docs)) if i not in ranked]
        items.append({"g": g, "sp": sp, "ranked": ranked})
    r_msgs = [msgs(it["sp"]["task"], [block(it["sp"]["pages"][i], False) for i in it["ranked"][:TOPK]],
                   it["g"]["question"], ANS_FMT) for it in items]
    f_msgs = []
    for it in items:
        blocks, used = [], 0
        for p in it["sp"]["pages"]:  # session order
            b = block(p, False)
            if used + len(b) > FULL_CHARS:
                break
            blocks.append(b)
            used += len(b)
        f_msgs.append(msgs(it["sp"]["task"], blocks, it["g"]["question"], ANS_FMT))
    out = {"R": reader.chat(r_msgs)}
    json.dump(out["R"], open(os.path.join(a.out, "raw_R.json"), "w"), ensure_ascii=False)
    out["Full"] = reader.chat(f_msgs)
    json.dump(out["Full"], open(os.path.join(a.out, "raw_Full.json"), "w"), ensure_ascii=False)
    trig = [i for i, o in enumerate(out["R"]) if not (R.parse_json(o["text"]) or {}).get("determined", False) or a.dry_run]
    gap = reader.chat([msgs(items[i]["sp"]["task"], [block(items[i]["sp"]["pages"][j], False) for j in items[i]["ranked"][:TOPK]],
                            items[i]["g"]["question"], GAP_FMT) for i in trig])
    g_msgs, meta = [], []
    for i, go in zip(trig, gap):
        it = items[i]
        ctx = set(it["ranked"][:TOPK])
        needs = ((R.parse_json(go["text"]) or {}).get("needed") or [])[:2]
        fetched = []
        for n in needs:
            want = set(R.toks(str(n.get("page", "")) if isinstance(n, dict) else str(n)))
            if not want:
                continue
            best = max((j for j in range(len(it["sp"]["pages"])) if j not in ctx and j not in fetched),
                       key=lambda j: (len(want & set(R.toks(it["sp"]["pages"][j]["title"] + " " + it["sp"]["pages"][j]["url"]))), j),
                       default=None)
            if best is not None and want & set(R.toks(it["sp"]["pages"][best]["title"] + " " + it["sp"]["pages"][best]["url"])):
                fetched.append(best)
        blocks = [block(it["sp"]["pages"][j], False) for j in it["ranked"][:TOPK]] + \
                 [block(it["sp"]["pages"][j], True) for j in fetched]
        g_msgs.append(msgs(it["sp"]["task"], blocks, it["g"]["question"], ANS_FMT))
        meta.append({"needs": needs, "fetched": [it["sp"]["pages"][j]["pid"] for j in fetched]})
    out["G"] = reader.chat(g_msgs)
    json.dump(out["G"], open(os.path.join(a.out, "raw_G.json"), "w"), ensure_ascii=False)
    tpos = {i: k for k, i in enumerate(trig)}
    rows = []
    for i, it in enumerate(items):
        sp = it["sp"]["pages"]
        pr = R.parse_json(out["R"][i]["text"])
        pf = R.parse_json(out["Full"][i]["text"])
        pg = R.parse_json(out["G"][tpos[i]]["text"]) if i in tpos else pr
        row = {"qid": it["g"]["qid"], "preds": {"R": pr, "Full": pf, "R+G": pg}, "g_meta": meta[tpos[i]] if i in tpos else None,
               "tokens": {"R": out["R"][i].get("prompt_tokens"), "Full": out["Full"][i].get("prompt_tokens")}}
        for name, base in (("R+gate", pr), ("R+G+gate", pg), ("Full+gate", pf)):
            row["preds"][name], row.setdefault("gate_fired", {})[name] = gate(base, sp, it["g"]["question"])
            # gate v2 (frozen 2026-10-24 before gold_v2): reported as "<arm>+gate2"
            row["preds"][name + "2"], row["gate_fired"][name + "2"] = gate_v2(base, sp, it["g"]["question"])
        rows.append(row)
    json.dump(rows, open(os.path.join(a.out, "preds.json"), "w"), indent=1, ensure_ascii=False)
    print(json.dumps({"questions": len(rows), "triggered": len(trig), "seconds": round(time.time() - t0, 1)}))


def add_gate3(gold, pages, out, parse_path, cont_path):
    """Gate v3 (frozen 2026-10-25 before gold_v3) applied post hoc to stored predictions; it only withholds answers,
    so no new judge items arise."""
    rows = json.load(open(os.path.join(out, "preds.json"), encoding="utf-8"))
    P = json.load(open(parse_path, encoding="utf-8"))
    CT = json.load(open(cont_path, encoding="utf-8"))
    G = {g["qid"]: g for g in gold}
    fired = collections.Counter()
    for r in rows:
        g = G[r["qid"]]
        sc = (P.get(r["qid"]) or {}).get("parse")
        for name, base in (("R+gate3", r["preds"]["R"]), ("R+G+gate3", r["preds"]["R+G"]), ("Full+gate3", r["preds"]["Full"])):
            r["preds"][name], r["gate_fired"][name] = gate_v3b(base, g["question"], sc, CT.get(g["session"], {}),
                                                               pages[g["session"]]["pages"])
            fired[name] += r["gate_fired"][name]
    json.dump(rows, open(os.path.join(out, "preds.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print(json.dumps({"questions": len(rows), "parsed": sum(1 for r in rows if (P.get(r["qid"]) or {}).get("parse")),
                      "fired": dict(fired)}))


def judge(gold, out, reader):
    rows = json.load(open(os.path.join(out, "preds.json"), encoding="utf-8"))
    G = {g["qid"]: g for g in gold}
    jobs, keys = [], []
    for r in rows:
        g = G[r["qid"]]
        if not g["determined"]:
            continue
        for arm, p in r["preds"].items():
            if p and p.get("determined") and p.get("answer") is not None:
                key = (r["qid"], json.dumps(p.get("answer"), ensure_ascii=False, sort_keys=True))
                if key not in keys:
                    keys.append(key)
                    jobs.append([{"role": "user", "content": JUDGE_FMT.format(
                        q=g["question"], g=json.dumps(g["answer"], ensure_ascii=False), a=key[1])}])
    res = reader.chat(jobs)
    verdict = {f"{k[0]}|{k[1]}": bool((R.parse_json(o["text"]) or {}).get("correct")) for k, o in zip(keys, res)}
    json.dump(verdict, open(os.path.join(out, "judge.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print(json.dumps({"judged": len(jobs)}))


def export_judge(gold, out):
    """Unique (question, system answer) pairs for determined gold questions, arm-blind, for an external judge."""
    rows = json.load(open(os.path.join(out, "preds.json"), encoding="utf-8"))
    G = {g["qid"]: g for g in gold}
    seen, items = set(), []
    for r in rows:
        g = G[r["qid"]]
        if not g["determined"]:
            continue
        for p in r["preds"].values():
            if p and p.get("determined") and p.get("answer") is not None:
                key = f"{r['qid']}|{json.dumps(p.get('answer'), ensure_ascii=False, sort_keys=True)}"
                if key not in seen:
                    seen.add(key)
                    items.append({"id": key, "question": g["question"], "gold": g["answer"],
                                  "system_answer": p.get("answer")})
    with open(os.path.join(out, "judge_items.jsonl"), "w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    print(json.dumps({"judge_items": len(items)}))


def import_judge(out, path):
    verdict = {}
    for line in open(path, encoding="utf-8"):
        if line.strip():
            x = json.loads(line)
            verdict[x["id"]] = bool(x["correct"])
    json.dump(verdict, open(os.path.join(out, "judge.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print(json.dumps({"verdicts": len(verdict)}))


def score(gold, set_dir, out):
    rows = json.load(open(os.path.join(out, "preds.json"), encoding="utf-8"))
    verdict = json.load(open(os.path.join(out, "judge.json"), encoding="utf-8"))
    G = {g["qid"]: g for g in gold}
    dom = {m["trajectory"]: m["domain"] for m in json.load(open(os.path.join(set_dir, "pages", "_index.json")))}
    tot = collections.defaultdict(collections.Counter)
    per = []
    for r in rows:
        g = G[r["qid"]]
        rec = {"qid": r["qid"]}
        for arm, p in r["preds"].items():
            det = bool(p and p.get("determined"))
            if g["determined"]:
                ok = det and verdict.get(f"{r['qid']}|{json.dumps(p.get('answer'), ensure_ascii=False, sort_keys=True)}", False)
                k = "det"
            else:
                ok = not det
                k = "undet"
            for d in ("all", dom.get(g["session"], "?")):
                tot[(arm, d)][k + "_n"] += 1
                tot[(arm, d)][k + "_correct"] += ok
                tot[(arm, d)]["unsupported"] += (not g["determined"]) and det
                tot[(arm, d)]["wrong_refusal"] += g["determined"] and not det
            rec[arm] = ok
        per.append(rec)
    json.dump({"per_question": per, "totals": {f"{a}|{d}": dict(c) for (a, d), c in tot.items()}},
              open(os.path.join(out, "scores.json"), "w"), indent=1)
    for (a, d), c in sorted(tot.items()):
        print(f"{a:10s} {d:10s} det {c['det_correct']}/{c['det_n']}  undet {c['undet_correct']}/{c['undet_n']}  "
              f"unsupported {c['unsupported']}  wrong_refusal {c['wrong_refusal']}")


if __name__ == "__main__":
    main()
