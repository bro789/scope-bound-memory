# G Confirmation-Set Preregistration (2026-10-21, written before launch)

## Frozen components
- Method: `g_experiment.py` (v2: fetched pages include coverage notes computed by the parser), `ax_compact.py`, and the BM25 and reader settings in `run_scope_experiments.py`. Reader: Qwen3.5-9B, thinking disabled, temperature 0, output 4096. Every state is compressed with `ax_compact` and truncated at 16,000 characters; R uses BM25 top-5. SHA in server file `scope02/confirmg_v1_freeze.sha256`.
- The only change tuned on development data (40 v2 questions + 40 newly generated questions) was adding coverage notes to fetched pages; it applied equally to G, E, and G-oracle.
- Confirmation set `confirmg_v1/confirm_questions_g1_nl.jsonl`: 200 questions (94 insufficient), all from confirmation families. No list is repeated from confirmation set v1 (same session, table, and filter count as the same list), but 117 questions come from sessions that appeared in confirmation set v1; report that overlap honestly. Qwen3.6-35B rewrote the questions (191 succeeded, 9 fell back to templates). Gold answers were derived from raw AX before any method ran; executor self-check agreed on 200/200.

## Development-set reference (v2)
| Arm | Sufficient /50 | Insufficient /30 | Successful fetches /49 |
|---|---:|---:|---:|
| R | 15 | 21 | — |
| R+G | 32 | 20 | 38 |
| R+E | 16 | 21 | 5 |
| R+G-oracle | 34 | 19 | — |

## Primary decision (drafted before development results; margin unchanged)
On 200 confirmation questions, conclude that “gap-driven fetching is more effective than ordinary fetching at the same budget” if **R+G is more accurate than R+E on sufficient questions and is no more than 3 percentage points worse on insufficient-question identification**.

## Mechanism decision
On questions that trigger fetching, G must retrieve necessary evidence more often than E (paired McNemar, p<0.05).

## Also report
- Unsupported conclusions, false refusals, results by question type, and rewritten questions vs. template fallbacks.
- R+G-oracle upper bound; fetch trigger rate (fraction R labels insufficient); token cost of fetching.
- Results separately for the 117 questions whose session appeared in confirmation set v1 and the remaining 83.

## Stopping rules
- R+G ties R+E: “fetching a little more” is enough, so G is not a contribution.
- Low fetch-hit rate (G no better than E): gap identification is the bottleneck.
- After the run, do not change G or rerun.
