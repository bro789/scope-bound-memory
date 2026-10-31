"""Question-scope parser for the v3 coverage gate. A fixed Qwen3.5-9B parser, decoupled from the reader. It reads
only the question (never the pages or any answer) and outputs structured scope fields. Few-shot examples are taken
from the development annotation set only.

Usage: python parse_scope.py --questions Q1.jsonl[,Q2.jsonl] --out OUT.json [--dry-run]
"""
import argparse
import json
import os

import run_scope_experiments as R

PROMPT = """You analyse questions that a colleague asks a web agent about pages it saw earlier in a browser session.
For the question below, output only JSON with these keys:
"operator": one of "lookup" (a value of one specific thing), "exists" (whether something exists/happened), "count", "list", "extreme" (largest/smallest/most/least/earliest/latest/first), "change" (before vs after);
"collection": the set of things the question ranges over, in a few words (e.g. "rows of the incident list", "items in order 148", "columns of the users list", "forums on the forum list"), or "" for a single thing;
"scope": "seen" ONLY if the question explicitly restricts itself to what was displayed or seen (e.g. "on the first page", "in the list you looked at", "did you see", "was X shown/displayed", "appeared on the page", "according to the page"); merely naming a list, page or category ("in the users list", "in the category", "in the lookup list", "on the site") does NOT make it "seen". Otherwise "whole" (it asks about the collection itself: which/how many/any/most among its members);
"answer_source": "stated" if the answer is a number or value that a page states directly (e.g. "how many results did the search return", "the total shown in the footer", "the count on the tab", "how many submissions according to the forum list"), "single_record" if it is about one specific record or object (its field, its value before and after a change), otherwise "enumerated" (the answer needs going through the members of a collection: counting them, listing them, comparing them, or checking that none matches);
"top_of_sorted": true if the question asks for the first/top/last entry of a list sorted by something, otherwise false.

Examples:
Q: In the session where you filtered incidents by caller, how many incidents did the list show before and after the filter was applied?
{"operator": "change", "collection": "rows of the incident list", "scope": "seen", "answer_source": "stated", "top_of_sorted": false}
Q: In the session where you looked up when I last ordered my body butter, which of my orders included body butter?
{"operator": "list", "collection": "my orders", "scope": "whole", "answer_source": "enumerated", "top_of_sorted": false}
Q: In the session where you sorted public projects by most stars, which project was listed first?
{"operator": "extreme", "collection": "public projects sorted by stars", "scope": "seen", "answer_source": "enumerated", "top_of_sorted": true}
Q: In the session where you updated my address, what was the default billing address before and after the change?
{"operator": "change", "collection": "", "scope": "whole", "answer_source": "single_record", "top_of_sorted": false}
Q: In the session where you created the user, did you see the new user's row in the user list after submitting?
{"operator": "exists", "collection": "rows of the user list", "scope": "seen", "answer_source": "enumerated", "top_of_sorted": false}
Q: In the session where you looked at the expense lines, how many expense lines belonged to user Julie Powers?
{"operator": "count", "collection": "expense lines", "scope": "whole", "answer_source": "enumerated", "top_of_sorted": false}
Q: In the session where you browsed the Headphones category, which product in the category had the highest price?
{"operator": "extreme", "collection": "products in the Headphones category", "scope": "whole", "answer_source": "enumerated", "top_of_sorted": false}
Q: In the session where you searched the user list, how many users in the list had the department Sales?
{"operator": "count", "collection": "users in the user list", "scope": "whole", "answer_source": "enumerated", "top_of_sorted": false}

Q: {q}
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    qs = [json.loads(l) for p in a.questions.split(",") for l in open(p, encoding="utf-8")]
    reader = R.Reader(a.dry_run)
    outs = reader.chat([[{"role": "user", "content": PROMPT.replace("{q}", q["question"])}] for q in qs])
    res = {}
    for q, o in zip(qs, outs):
        res[q["qid"]] = {"parse": R.parse_json(o["text"]), "raw": o["text"]}
    json.dump(res, open(a.out, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    ok = sum(1 for v in res.values() if v["parse"])
    print(json.dumps({"questions": len(qs), "parsed": ok}))


if __name__ == "__main__":
    main()
