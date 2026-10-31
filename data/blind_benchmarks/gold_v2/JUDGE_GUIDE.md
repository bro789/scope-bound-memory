# Judging guide

Each line of the input file has: `id`, `question`, `gold` (the authoritative answer from careful annotators who read the pages) and `system_answer` (an answer produced by some system). You do not know which system produced it.

Decide whether `system_answer` states the same fact as `gold`:
- correct: same value / same yes-no / same number / same set of items (order, formatting, capitalisation, extra harmless words, or an extra short explanation do not matter; an identifier may be given with or without its label);
- incorrect: any required item or value is wrong, missing, or extra items are listed that are not in gold; a number that differs; a hedge that does not commit to the gold value.

Judge only by comparing the two answers with the question; do not use outside knowledge.

Output one JSON object per input line, in the same order: `{"id": "<id>", "correct": true|false}`.
