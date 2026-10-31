# Verification guide: answer questions about a web session

A web agent worked through one browser session. You get the session file (the task, then every distinct page the agent saw, each with an id `P<number>`, the action that led to it, its URL and its accessibility tree in compact text) and a list of questions a colleague later asked about that session.

For each question, read the pages carefully and decide:
- **determined**: the pages of this session settle the question, so a careful reader gets exactly one correct answer. Give that answer (a value, yes/no, a number, or a list of identifiers or names exactly as written on the page) and the supporting page ids.
- **not determined**: the pages do not settle it. Give `null` as the answer, and in `what_is_known` say precisely what the pages do show (a partial list, a lower bound, a value at another time, and so on), with page ids.

Judge only from these pages, not from general knowledge of how such websites usually behave. If a question is ambiguous or refers to something that does not fit the session, mark it not determined and say why.

## Output
One JSON object per line (JSONL) in the output file you are given:
`{"qid": "<qid>", "determined": true|false, "answer": <value, list or null>, "what_is_known": "<text or empty>", "evidence_pages": ["P12", ...], "rationale": "<one or two sentences>"}`

Work only from the files you are given and this guide. Do not open any other file in the project.
