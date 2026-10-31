# Supplementary Material: Agent Memories Overreach Their Observations

This repository contains the code, data, pre-registrations, raw model outputs and scores behind every number in the paper *Agent Memories Overreach Their Observations: Scope-Bound Answering of Set Questions over Partial Web Views*.

## Layout

| Path | Contents |
|---|---|
| `code/` | Final code (see "Code versions" below). |
| `code_snapshot_sg_confirm/` | Code as it was for the S+G confirmation run (matches `freeze_hashes/confirmsg_v1_freeze.sha256`). |
| `data/list_views_dev_pool.jsonl.gz` | All list-view observations parsed from the 841 held-out WorkArena sessions, each with filter, visible range and version. This is the memory used by S and G. |
| `data/question_sets/` | Templated question sets: development (`dev_v1`, `dev_v2`, `devg_v1`) and confirmation (`confirm_v1`, `confirmg_v1` = G set, `confirmsg_v1`). `*_nl.jsonl` are the paraphrased versions; `family_split.json` is the task-family split. |
| `data/blind_benchmarks/` | The four blind-annotated benchmarks (`gold_dev`, `gold_v1`, `gold_v2`, `gold_v3`). Each has the rendered session pages (`pages/`), annotation, verification and judge guides, both annotation rounds (`round1/`, `round2/`), agreement (`agreement_r1_r2.json`), adjudication (`adjudication/`), the final labels (`gold.jsonl`) and judge inputs/outputs (`judge_*`). `label_audit/` holds the label audits. |
| `runs/` | One directory per run, with raw outputs (`raw_*.json`), parsed predictions (`*_results.json`, `preds.json`), judgments (`judge*.json`) and summaries (`scores.json`, `*_summary.json`, `analysis.json`). Also the scope-parser outputs (`cc6-33/34/35_parse_*.json`) and the container analysis (`containers_all.json`) used by gate v3. |
| `preregistrations/` | Written pre-registrations for every confirmation run, translated from the Chinese originals prepared before each run. |
| `freeze_hashes/` | SHA-256 hashes of the code and question sets frozen before each confirmation run. |

## Runs used in the paper

| Paper | Run directory |
|---|---|
| RQ1, Tables 2-3, Figure 3 | `runs/cc6-13_confirm/`; re-parsed scores in `runs/cc6-17_evsel_confirm/reparse_rescore.json` |
| RQ2 evidence-selection control | `runs/cc6-17_evsel_confirm/` (development: `cc6-17_evsel_dev/`) |
| RQ3 gap-driven refetch, Table 4, Figure 4 | `runs/cc6-22_g_confirm/` (development: `cc6-18_g_dev/`, `cc6-20_g_dev_v2/`) |
| RQ4 S+G, Table 4 | `runs/cc6-25_sg_confirm/`; supplementary set `runs/cc6-25_sg_fresh/` (development: `cc6-23_sg_dev/`) |
| RQ5 35B reader, Table 4 | `runs/cc6-28_35b_confirm_v1/`, `runs/cc6-28_35b_confirm_g1/` |
| RQ6 gold_v1, Table 5 | `runs/cc6-30_gold_v1_9b/`, `runs/cc6-30_gold_v1_35b/` (development: `cc6-29_gold_dev_9b/`) |
| RQ6 gold_v2, Table 5 | `runs/cc6-32_gold_v2_9b/`, `runs/cc6-32_gold_v2_35b/` |
| RQ6 gold_v3, Tables 5-6, Figure 5 | `runs/cc6-35_gold_v3_9b/`, `runs/cc6-35_gold_v3_35b/` (`preds_nogate3.json` = before gate v3) |
| Early development | `runs/cc6-3_full/`, `runs/cc6-7_v2/`, `runs/cc6-13_dev_v21/` |

All reported numbers use the scores re-parsed with the tolerant JSON parser (`code/reparse_rescore.py`).

## Code

| File | Role |
|---|---|
| `scope_memory.py` | S: scope binding and the coverage executor. Identical (hash `a25ee6f6`) in every confirmation run. |
| `run_scope_experiments.py` | Reader calls (vLLM offline), arms B0/B1/B2/S, tolerant JSON parsing, scoring (RQ1, RQ2, RQ5). |
| `g_experiment.py` | Arms R, R+E, R+G, R+G-oracle and S+G (RQ3, RQ4). |
| `gold_eval.py` | Blind-benchmark evaluation: R, Full, R+G and gates v1 (`gate`), v2 (`gate_v2`) and v3 (`gate_v3b`, `--gate3`); judge export/import and scoring (RQ6). |
| `parse_scope.py`, `container_coverage.py` | Gate v3: question-scope parser and container-level coverage analysis. |
| `page_coverage.py` | Page-level coverage used by gates v1 and v2. |
| `parse_list_views.py`, `ax_compact.py` | Parsing list views from trajectories; page rendering. |
| `make_dev_questions.py`, `paraphrase_questions.py` | Templated question generation and paraphrasing. |
| `select_annotation_pages.py`, `annotation_tools.py` | Blind-benchmark session selection, annotation input, round comparison and adjudication merge. |
| `analyze_confirm.py`, `analyze_g.py`, `reparse_rescore.py`, `rescore_dev.py` | Analysis, paired exact McNemar tests and re-scoring. |
| `dev_analysis/` | Offline diagnostics used while developing gate v3 on development data. |

### Code versions

`code/` is the final version and matches `freeze_hashes/gold_v3_freeze.sha256`. After the confirmation-v1 and G runs, three changes were made to `run_scope_experiments.py`, `g_experiment.py` and `make_dev_questions.py`: environment-variable overrides for paths and models, the tolerant JSON parser, and a template fallback for failed paraphrases. The exact versions used for those two runs are recorded only by their hashes in `freeze_hashes/`. The version used for the S+G confirmation is in `code_snapshot_sg_confirm/`. The pre-registration file names were translated: `preregistrations/gold_v2_en.md` is the file hashed as `gold_v2_preregistration.md`.

## Reproducing

Requirements: Python 3.10+; `vllm` only for runs that call a model (analysis and scoring scripts use the standard library). Readers: Qwen3.5-9B and Qwen3.6-35B-A3B-FP8, run in vLLM at temperature 0 with thinking mode disabled. Judges: Claude Sonnet 5.5 (all items) and Claude Opus 5.5 (40 re-judged items per benchmark).

1. Download the web-agent trajectories released with LongMemEval-V2 (`trajectories.jsonl`) and the models.
2. Set `SCOPE02_ROOT` to a directory containing `trajectories.jsonl`, `models/`, and `scope02/` with the contents of this repository's `data/` (decompress `list_views_dev_pool.jsonl.gz`). Optional: `SCOPE02_MODEL`, `SCOPE02_TP`, `SCOPE02_MAXLEN`, `SCOPE02_GPU_UTIL`.
3. Run the scripts above; each run directory in `runs/` can be regenerated by the corresponding script, and every table and figure can be recomputed from the saved outputs in `runs/` without running any model.

## Data provenance and license

The sessions come from the WorkArena (ServiceNow) trajectories released with LongMemEval-V2; the rendered pages contain the demo data of those instances. Data derived from these trajectories follows the licenses of LongMemEval-V2 and WorkArena. Code in this repository is released under the MIT License.

