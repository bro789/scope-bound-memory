# S+G Combined Confirmation Preregistration (2026-10-21, written before launch)

## Method (frozen; no development-set tuning)
- `g_experiment.py --with-s`. R, G, E, and G-oracle are identical to the G confirmation round (frozen G v2); S uses frozen S v2.1 (same `scope_memory.py` SHA), with the S v2.1 intent-prompt format.
- List views available to the system:
  - **S@R:** list pages in the BM25 context.
  - **S+G:** those list pages plus pages fetched by G.
- Combination rules:
  - If the executor gives an answer, use it.
  - If the executor finds insufficient coverage (partial coverage / no witness), return “insufficient.”
  - If no observation can be bound or intent parsing fails, fall back to the reader's answer (R+G for S+G; R for S@R).

## Development-set reference (80 questions)
| Arm | Sufficient /50 | Insufficient /30 | Unsupported conclusions | False refusals |
|---|---:|---:|---:|---:|
| R+G | 32 | 20 | 10 | 7 |
| S@R | 20 | 26 | 4 | 21 |
| S+G | 42 | 26 | 4 | 0 |

## Confirmation data
1. **Main confirmation set:** `confirmg_v1/confirm_questions_g1_nl.jsonl`, 200 questions. **This is the second use of these questions** (first used to confirm G). Neither S nor G was tuned on them; combination rules were written into code before any S+G results existed.
2. **Entirely new supplementary set:** `confirmsg_v1/confirm_questions_sg1_nl.jsonl`, 51 questions (24 insufficient). Excludes every list asked in the first two confirmation sets. Rewritten by 35B (47 succeeded, 4 fell back to templates). Used only for a directional check.

## Primary decision
On the main confirmation set, conclude that “programmatic coverage checks and gap-driven fetching are additive” if **S+G is more accurate than R+G on insufficient questions, while sufficient-question accuracy is no more than 3 percentage points below R+G**.

## Also report
- Unsupported conclusions, false refusals, and paired McNemar for S+G vs. R+G.
- S@R vs. S+G: gain to the executor from fetching.
- The same metrics on the 51-question supplementary set.
- Distribution in S+G among executor answers, executor “insufficient” decisions, and reader fallbacks.

## Stopping rule
- If S+G is more than 3 percentage points below R+G on sufficient questions because failed executor binding causes false refusals, record “intent parsing is the combination bottleneck.”
- Do not change the method or rerun after completion.
