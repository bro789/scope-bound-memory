# gold_v3 Confirmation Preregistration (2026-10-25, written before any model ran on gold_v3)

## Background
Gate v2 still missed the primary decision on gold_v2 (9B determinate-question accuracy fell 4.7 points; the margin was 3). Error analysis attributed false blocks to keyword-based identification of the set a question refers to and page-level coverage checks. Gate v3 replaces both with structured procedures. **gold_v1 and gold_v2 are now development sets**; confirmation uses the new blind-annotated gold_v3.

## Gate v3
1. **Parse question scope** (`parse_scope.py`): a fixed Qwen3.5-9B reads only the question text (no page or answer) and outputs operation type, requested set, scope (visible items only / entire set), answer source (stated directly on page / single record / requires enumeration), and whether the first sorted row is used. Few-shot examples come only from development annotation sets.
2. **Check container-level coverage** (`container_coverage.py`): split the raw accessibility tree into tables/lists and other containers; record each container's entries, column headers, nearby scope statement (e.g. “Showing rows 1 to 20 of 2,826”), and pagination controls; classify it complete / incomplete / unknown. Navigation, banners, menus, and toolbars are not containers.
3. **Decision rule** (`gate_v3b` in `gold_eval.py`): withdraw the reader answer and return “cannot determine” only if every condition below is satisfied:
   - Operation is non-monotonic (count, enumeration, extremum, before/after change, or an existence question answered “none”).
   - Parse result says “entire set,” requires item-by-item enumeration, and does not select the first row after sorting.
   - Not a pairwise comparison between two specified objects (“X or Y”).
   - The answer number is not a total stated on the page (statistics on a count page or total in a container scope statement; before/after questions may use a difference between these numbers).
   - Answer does not match a column header.
   - The answer's container (selected first by matching answer items, then by overlap between the requested set and container title/headers; ties favor coverage evidence and more entries; dropdowns are not record lists) is incomplete. If container coverage is unknown, fall back to page-level `read_v2`, except when the answer is in a secondary list on the page.

## Performance on development data (offline rescoring of saved answers; no reader-model calls)
| Data | v2 correct blocks / false blocks | v3 correct blocks / false blocks |
|---|---:|---:|
| Development set, 9B Full | 11 / 1 | 6 / 0 |
| gold_v1, 9B Full | 14 / 1 | 15 / 0 |
| gold_v1, 35B Full | 18 / 3 | 21 / 0 |
| gold_v2, 9B Full | 11 / 6 | 8 / 0 |
| gold_v2, 35B Full | 13 / 5 | 11 / 0 |

On gold_v2 with 9B: Full scored 93/127 determinate and 63/113 indeterminate; Full+gate3 scored 93/127 and 71/113. These rules were all tuned on development data, so weaker performance on gold_v3 was expected.

## Freeze
`gold_v3_freeze.sha256` covers `gold_eval.py`, `parse_scope.py`, `container_coverage.py`, `page_coverage.py`, `run_scope_experiments.py`, and `annotation_v3/gold.jsonl`. Reader, prompt, rendering, and scoring pipeline match gold_v2.

## Data
Gold_v3: 40 entirely new sessions (no overlap with the development annotation set, gold_v1, or gold_v2), 240 questions: 123 determinate and 117 indeterminate. Same blind-annotation process (Opus generates → Sonnet independently reviews → Opus blindly adjudicates); two-round agreement on determinacy κ=0.983; all 37 disagreements were adjudicated and retained.

## Primary decision (9B reader, Full vs. Full+gate3)
**Accuracy on indeterminate questions must exceed Full, while determinate-question accuracy must not be more than 3 percentage points below Full.** Report 35B too, but it is not part of the decision. Also report gate-trigger counts, false blocks, unsupported conclusions, gated R/R+G results, environment breakdown, v1/v2 gate comparisons, and inter-rater agreement (Opus judges 40 sampled items).

## Stopping rule
Do not change the gate or rerun after completion. Report the result honestly whether or not it passes.
