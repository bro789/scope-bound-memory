# Independent Development Annotation Set: annotation_dev_v1 (2026-10-22)

Uses exactly the same blind-annotation workflow as gold_v1 (Opus generates → Sonnet independently reviews → Opus blindly adjudicates), for development and tuning of general coverage checks. **This is not an evaluation set.**

- 30 sessions: 15 WorkArena development-family sessions (not anchored or used as distractors in any question set) plus 15 WebArena sessions, three from each of five sites. No overlap with gold_v1 or the haystack (`code/select_annotation_pages.py --build`, tag `annot-dev-v1`).
- 180 questions: 91 determinate, 89 indeterminate.
- Two rounds agreed on determinacy for 178/180 (κ=0.978); answers were exactly identical on 159/180. All 21 disagreements were retained after adjudication (18 wording differences, 3 substantive).
- `gold.jsonl` contains the merged development-set gold answers.
