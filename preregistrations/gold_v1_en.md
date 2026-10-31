# gold_v1 Formal Evaluation Preregistration (2026-10-24, written before launch)

## Frozen components
- Code: `gold_eval.py` (SHA prefix e2fa6b2c), `page_coverage.py` (db64bbdd), and gate rules. Gate rules were tuned only on `annotation_dev_v1`; rationale for changes is in `progress_log.md`.
- Evaluation set: `annotation_v1/gold.jsonl` (= gold_v1, SHA 471cf5f5), 240 questions: 126 determinate, 114 indeterminate; 120 WorkArena and 120 WebArena. No method had run on these questions before.
- Readers: Qwen3.5-9B (primary) and Qwen3.6-35B-A3B-FP8 (secondary). Offline vLLM, thinking disabled, temperature 0, output limit 4096. Prompts identical to development.
- Scoring: for gold “cannot determine” questions, a program checks whether the system abstains; on determinate questions, abstention counts as wrong, and answered questions are blindly checked for equivalence by a Claude Sonnet subagent using `JUDGE_GUIDE.md` (judges do not see the method). Judges use a different model family from the readers.

## Development-set reference (9B)
| | Determinate /91 | Indeterminate /89 | Unsupported conclusions |
|---|---:|---:|---:|
| Full | 70 | 50 | 39 |
| Full+gate | 68 | 61 | 28 |
| R | 51 | 54 | 35 |
| R+gate | 49 | 63 | 26 |

## Primary decision (9B reader, strongest baseline Full)
The claim “programmatic coverage gating works on independently annotated natural questions” is supported if **Full+gate is more accurate than Full on ‘cannot determine’ questions, while determinate-question accuracy is no more than 3 percentage points below Full**. Also use paired McNemar testing for the difference on “cannot determine” questions.

## Also report (not part of the decision)
- R vs. R+gate, R+G vs. R+G+gate; unsupported conclusions and false refusals.
- Report WorkArena and WebArena separately.
- The same numbers for the 35B reader.
- Number of gate triggers and false blocks on determinate questions.
- Inter-rater agreement: have another Opus subagent blindly judge 40 random answers and report agreement with Sonnet.

## Stopping rule
- Do not change gate rules or rerun after completion.
- If the primary decision fails, report that honestly and limit the paper's gate claim to controlled experiments (confirmation set v1).
