# gold_v2 Confirmation Preregistration (2026-10-24, written before annotation)

## Background
On gold_v1, gate v1 missed the preregistered primary decision by one question, mainly because of false blocks. After manually reviewing every trigger on the development annotation set and gold_v1, the gate was revised to v2. **gold_v1 is now a development set**; confirmation must use a completely new blind-annotated set, gold_v2.

## Gate v2 changes from v1 (general rules, not tailored to individual questions)
1. Tighten pagination evidence: the page must be a list (≥3 entries) and have a next-page control, at least two small page-number links, or a “View All” link. Phone-number links, record-navigation “Next” in forms, and image carousels no longer count as pagination.
2. Join multi-line scope statements (e.g., “1 - 12 of 31190”) before parsing them.
3. Do not block questions asking “what did you see?” (did you see / appeared / was shown / according to / already displayed / you started on).
4. For counts, changes, and extrema, do not block if the answer number is stated on the page (“of N,” “N total,” tab counts, or “N results/items/…” with N≥10) or is a difference between such numbers.
5. Do not block list answers whose items appear only in column names, buttons, labels, or other controls—not in data rows.
6. Do not block answers of “none/0” when the page explicitly says there are none (“No records,” “Be the first to review,” “There are no …”).

## Performance on development data (offline rescoring of saved reader answers; no model calls)
| Data | v1 correct blocks / false blocks | v2 correct blocks / false blocks |
|---|---:|---:|
| Development set, 9B Full | 11 / 2 | 11 / 1 |
| gold_v1, 9B Full | 14 / 5 | 14 / 1 |
| gold_v1, 35B Full | 17 / 13 | 18 / 3 |
| gold_v1, 35B R | 17 / 8 | 19 / 3 |

Known residual: questions where a list is sorted and its first row gives an extremum (“most recent,” “listed first”). Sort indicators on pages are unreliable; this case is not handled.

## Freeze
- `gate_v2` in `gold_eval.py` and `read_v2` / `stated_counts` / `states_none` in `page_coverage.py`. SHA in `gold_v2_freeze.sha256`.
- Reader, prompt, and scoring pipeline are the same as gold_v1.

## Data
Gold_v2: 40 sessions (20 WorkArena; 4 from each of 5 WebArena sites), with no overlap with the development annotation set, gold_v1, or the LongMemEval haystack. Same blind-annotation process as gold_v1 (Opus generates → Sonnet independently reviews → Opus blindly adjudicates).

## Primary decision (9B reader, Full vs. Full+gate_v2)
**Accuracy on indeterminate questions must exceed Full, while determinate-question accuracy must not be more than 3 percentage points below Full.** Report 35B too, but it is not part of the decision. Also report gate-trigger counts, false blocks, unsupported conclusions, results by environment, and inter-rater agreement (Opus judges 40 sampled items).

## Stopping rule
Do not change the gate or rerun after completion. Report the result honestly whether or not it passes.
