# Independent Annotation Evaluation Set gold_v1 (2026-10-22)

Purpose: avoid circularity where the method authors' question generator creates both questions and gold answers. This question set did not pass through our question generator, parser, or coverage definition.

## Data
- 40 trajectories, unused by any benchmark haystack or existing question set:
  - 20 WorkArena sessions from confirmation families, never used as anchor or distractor sessions.
  - 20 WebArena sessions, four each from five sites (ports 9001, 9080, 9082, 9083, 9443), selected by stratified random sampling.
- Sampling used SHA ordering only; it did not inspect whether pages contained lists. See `code/select_annotation_pages.py`.
- Rendered all distinct pages from each trajectory into text with `ax_compact` (`pages/`), exactly as supplied to methods (16,000-character limit per page).

## Process
1. **Blind question generation:** eight Opus subagents, each with a fresh context, handled five sessions. They received only `ANNOTATION_GUIDE.md` and session pages; instructions did not mention our methods, pagination, or coverage. Each session received six questions across at least four types, about half determinate and half indeterminate; reasons for indeterminacy were unrestricted.
2. **Independent blind review:** four Sonnet subagents saw the questions but not the authors' answers; using `VERIFY_GUIDE.md`, they answered independently and judged whether each question was answerable.
3. **Agreement:**
   - Two rounds agreed on determinacy for 229/240 questions (95.4%); Cohen's κ=0.908.
   - Answers were exactly identical on 200/240.
   - See `agreement_r1_r2.json`.
4. **Blind adjudication:** 40 disagreements went to two Opus subagents. The two candidate answers were randomly labeled A/B; adjudicators did not know which round they came from and checked the pages themselves. Retained all 40 and removed none; 29 differed only in wording, 11 had substantive disagreements.
5. **Freeze:** `gold_v1.jsonl`, 240 questions.

## Counts
| | Determinate | Indeterminate |
|---|---:|---:|
| WorkArena | 61 | 59 |
| WebArena | 65 | 55 |

Question types: 64 single fact, 43 count, 34 existence, 34 list, 33 change, and 32 comparison/extremum.

## Known issues
- Question-writing subagents shared a temporary directory. Batch 7 mistakenly ran batch 8's file-writing script, overwriting five batch-8 files. A later check found all five structurally complete and the number of determinate questions matched the batch-8 self-report, but the files were not compared byte by byte with their pre-overwrite versions.
- All labels were produced by LLMs and need a human audit: see `manual_audit_sheet.md`, with 30 stratified random questions and 11 substantive disagreements.
- Pages were truncated at 16,000 characters. Some sessions had truncated list pages; question authors said their questions did not rely on content beyond the truncation.
- The least certain substantive disagreement was 22f883f0-q1: a number appeared on the form, but no save confirmation was visible.

## Next steps
- Use this set only for evaluation, not tuning.
- First extend S/G/S+G to WebArena pages (requires a new list parser), then run R, R+G, S+G, and stronger baselines.
