# Annotation guide: questions about what a web agent saw

You are writing benchmark questions for an **agent memory** test. A web agent worked through one browser session. Later, a colleague asks the agent questions about things it saw during that session. The memory can only use what appeared on the pages of that session.

You get one file per session. It contains the task the agent was doing, then every distinct page it saw, in order. Each page has an id (`P<number>`), the action that led to it, its URL, and its accessibility tree in compact text form.

## What to write
Write **6 questions per session**. Each question should be one a real colleague might plausibly ask afterwards. Mix the kinds below; use at least four different kinds per session, and do not force a kind that the session does not support:
- a single fact (a value of some field of some item);
- whether something exists or happened;
- how many items satisfy something;
- which items satisfy something (a list);
- a comparison or extreme (largest, earliest, which of two);
- a change between two points in the session (before vs. after an action).

For each question decide whether the pages of this session **determine** the answer:
- **determined**: a careful reader of these pages gets exactly one correct answer;
- **not determined**: the pages do not settle it. There are many possible reasons, for example the information never appeared, it appeared in a way that does not settle the question, or the question asks for more than what was shown. Aim for roughly half "determined" and half "not determined". Every question must still be natural and on-topic for the session. Do not write trick questions, and do not ask about things obviously unrelated to the session.

Refer to the session the way a colleague would ("in the session where you were asked to ..."), using the task text so that the session is unambiguous. Do not mention page ids, the accessibility tree, or these instructions in the question text.

## Answers
- determined: give the exact answer (a value, yes/no, a number, or a list of item identifiers or names exactly as written on the page) and the page ids that support it.
- not determined: give `null` as the answer, then in `what_is_known` state precisely what the pages do show (for example a partial list, a lower bound, or the value at a different time), and give the page ids involved.
- Read the pages carefully; prefer identifiers (numbers, IDs, names) exactly as written.

## Output
Write one JSON object per line (JSONL) to the output file you are given, with these keys:
`{"session": "<id>", "qid": "<id>-q1", "kind": "<single_fact|existence|count|list|comparison|change>", "question": "...", "determined": true|false, "answer": <value, list or null>, "what_is_known": "<text or empty>", "evidence_pages": ["P12", ...], "rationale": "<one or two sentences>"}`

Work only from the session files you are given and this guide. Do not open any other file in the project.
