"""Natural-language rewrite of templated confirmation questions with a different model (Qwen3.6-35B-A3B-FP8).

Only the query part after the session sentence is rewritten; the session sentence stays verbatim so the named
session remains unique. Each rewrite must keep every condition value, column, value and key literally, contain
no square brackets and keep the operator's wording; otherwise it is retried (temperature 0.7, up to 3 tries)
and finally falls back to the template, flagged. The rewriter never sees gold answers.

Usage: python paraphrase_questions.py IN_JSONL OUT_JSONL [--dry-run] [--endpoint http://127.0.0.1:PORT --tag TAG]
With --endpoint it calls an already running OpenAI-compatible server (standard library only, 3 threads)
instead of loading the model with vLLM.
"""
import concurrent.futures
import json
import re
import sys
import urllib.request

MODEL = "/mnt/nvme/vldb-20261021/memory/scope02/models/Qwen3.6-35B-A3B-FP8"
OPS = r"(!=|>=|<=|=|>|<|is not empty|is empty|starts with|ends with|does not contain|contains|not in|in|" \
      r"greater than or equal|less than or equal|greater than|less than|does not equal|on|before|after|between)"
OP_WORDS = {"count": ("how many", "number of", "count"),
            "enumerate": ("list", "which", "what are", "name all", "all the", "every"),
            "exists": ("any", "is there", "was there", "were there", "at least", "did ", "does "),
            "difference": ("no longer", "disappear", "dropped", "removed", "missing", "not ", "gone"),
            "lookup": ("what", "which"), "lookup_latest": ("what", "which")}
PROMPT = """Rewrite the following request as a natural question that a ServiceNow user might ask a colleague.
Rules:
- Keep the meaning exactly: same table, same filter conditions, same column and value, same record identifiers, same kind of answer (a count, a list, yes/no, a value, or the records that disappeared).
- Copy every field value, identifier, name, date and hashtag exactly as written (same spelling and capitalisation).
- Express filter conditions in plain words instead of the bracketed notation (e.g. "where Active is true and the caller is X"); do not use square brackets and do not say "matching the filter".
- If the request says "with no filter conditions (the full list)", say clearly that it is about the whole unfiltered list.
- Output only the rewritten question.

Request: {req}"""


def split_question(q):
    # the session sentence may itself contain '", ' (quoted task text); the query part only uses single quotes
    head, sep, rest = q.rpartition('", ')
    return head + sep, rest


def required_literals(qx):
    s = qx["scope"]
    req = []
    for cond in s.get("asked_conditions", s["conditions"]):
        for part in re.split(r" \.or\. | \.and\. ", cond):
            m = re.match(rf"^(.*?) {OPS}(?: (.*))?$", part.strip())
            if not m:
                req.append(part.strip())
                continue
            field, _op, val = m.group(1), m.group(2), (m.group(3) or "").strip()
            req.append(val if val and val.lower() not in ("true", "false") else field)
    for k in ("column", "value"):
        if s.get(k):
            req.append(s[k])
    for lit in re.findall(r"'([^']+)'", split_question(qx["question"])[1]):
        req.append(lit)
    return sorted({r for r in req if r})


def check(qx, text):
    t = text.lower()
    problems = [f"missing literal: {r}" for r in required_literals(qx) if r.lower() not in t]
    if "[" in text or "]" in text:
        problems.append("square brackets")
    if "matching the filter" in t or " and " not in t and t.count(" = ") > 0:
        problems.append("too literal")
    if not any(w in t for w in OP_WORDS[qx["operator"]]):
        problems.append("operator wording")
    if "no filter conditions" not in qx["question"] and re.search(r"(full list|whole list|entire list|unfiltered)", t):
        problems.append("filtered request worded as full list")
    if "no filter conditions" in qx["question"] and not re.search(r"(whole|entire|full|all|unfiltered|without (any )?filter|no filter)", t):
        problems.append("full-list wording")
    return problems


def main():
    src, dst = sys.argv[1], sys.argv[2]
    dry = "--dry-run" in sys.argv
    qs = [json.loads(l) for l in open(src)]
    parts = [split_question(q["question"]) for q in qs]
    prompts = [[{"role": "user", "content": PROMPT.format(req=rest[0].upper() + rest[1:])}] for _h, rest in parts]
    outs = [None] * len(qs)
    probs = [None] * len(qs)
    endpoint = sys.argv[sys.argv.index("--endpoint") + 1] if "--endpoint" in sys.argv else None
    tag = sys.argv[sys.argv.index("--tag") + 1] if "--tag" in sys.argv else "scope02"
    if not dry:
        if endpoint:
            def gen(batch, temp, seed):
                def one(msgs):
                    body = {"model": "Qwen/Qwen3.6-35B-A3B-FP8", "messages": msgs, "temperature": temp,
                            "max_tokens": 300, "seed": seed, "user": tag,
                            "chat_template_kwargs": {"enable_thinking": False}}
                    req = urllib.request.Request(endpoint + "/v1/chat/completions", json.dumps(body).encode(),
                                                 {"Content-Type": "application/json"})
                    with urllib.request.urlopen(req, timeout=600) as r:
                        return json.load(r)["choices"][0]["message"]["content"] or ""
                with concurrent.futures.ThreadPoolExecutor(3) as ex:
                    return list(ex.map(one, batch))
        else:
            from vllm import LLM, SamplingParams
            llm = LLM(model=MODEL, tensor_parallel_size=2, max_model_len=8192, gpu_memory_utilization=0.85,
                      enforce_eager=True, limit_mm_per_prompt={"image": 0, "video": 0})

            def gen(batch, temp, seed):
                sp = SamplingParams(temperature=temp, max_tokens=300, seed=seed)
                res = llm.chat(batch, sp, chat_template_kwargs={"enable_thinking": False}, use_tqdm=False)
                return [r.outputs[0].text for r in res]
        todo = list(range(len(qs)))
        for attempt in range(4):
            if not todo:
                break
            res = gen([prompts[i] for i in todo], 0.0 if attempt == 0 else 0.7, attempt)
            nxt = []
            for i, r in zip(todo, res):
                text = r.strip().strip('"')
                p = check(qs[i], text)
                if not p:
                    outs[i], probs[i] = text, []
                else:
                    probs[i] = p
                    nxt.append(i)
            todo = nxt
    n_fallback = 0
    with open(dst, "w") as f:
        for q, (head, rest), o, p in zip(qs, parts, outs, probs):
            q = dict(q)
            q["template_question"] = q["question"]
            if o:
                q["question"] = head + o[0].lower() + o[1:] if o[:1].isupper() and not o[:2].isupper() else head + o
                q["paraphrase"] = {"status": "ok", "model": "Qwen3.6-35B-A3B-FP8"}
            else:
                n_fallback += 1
                q["paraphrase"] = {"status": "fallback_template", "last_problems": p}
            f.write(json.dumps(q, ensure_ascii=False) + "\n")
    print(json.dumps({"questions": len(qs), "fallback_template": n_fallback, "dry_run": dry}))


if __name__ == "__main__":
    main()
