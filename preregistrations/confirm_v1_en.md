# Confirmation-Set Preregistration (2026-10-21, written before launch)

## Frozen components
- Method S v2.1: `scope_memory.py` (normalization includes near-match table names, default key column, key/column swapping, reducing “Col = value” to a column filter, and removing square brackets), plus the intent prompt in `run_scope_experiments.py` (lists each session's tables, columns, and observed filter conditions). SHA in server file `scope02/confirm_v1_freeze.sha256`.
- Confirmation set `confirm_v1/confirm_questions_v1_nl.jsonl`: 200 questions, only from confirmation families (10 families, never used for development); 84 lack sufficient information.
  - Qwen3.6-35B-A3B-FP8 on machine 2 (shared service 8026, different from the 9B reader) rewrote the question portion into natural language; session-description sentences were kept verbatim. 193 passed literal-retention and operator checks; 7 reverted to template wording.
  - No version-question candidates existed in the confirmation families, so **the confirmation set has no version questions**; conclusions about version questions come only from development data.
  - Gold answers were derived from raw AX before any method ran; executor self-check agreed on 200/200.
- Reader, sampling, output budget, and B0/B1/B2 prompts match development v2 (Qwen3.5-9B, thinking disabled, temperature 0, output 4096).

## Primary decision (the sole criterion for whether to continue)
On 200 confirmation questions, **S must be more accurate than B2 on insufficient questions, while its accuracy on sufficient questions is no more than 3 percentage points below B2** (116 sufficient questions, about 4 questions). If so, conclude that “scope constraints outperform ordinary scope prompts on unseen workflows” and continue to G under the idea plan.

## Also report (not part of the decision)
- Unsupported conclusions (definite answers on insufficient questions) and false refusals.
- Results by question type; rewritten questions and template fallbacks separately.
- Subset using tables not seen in development.
- Split S errors into intent-parsing errors (with S-oracle control) and executor errors.
- Paired McNemar comparisons for S vs. B2 on insufficient-question identification and sufficient-question accuracy.

## Stopping rules
- If S is more than 3 percentage points below B2 on sufficient questions, mainly because intent parsing fails to bind rewritten natural-language questions, record “binding errors cancel the fix” under the idea plan and pause the current design.
- If S ties B2 on identifying insufficient questions, ordinary prompting is already enough; shift the contribution toward evidence selection.
- After the run, do not change S or rerun the confirmation set.
