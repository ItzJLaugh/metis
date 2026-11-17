# Metis full build plan

Goal: every useful point from the Laya audit built into Metis, plus a Laya checkpoint fine-tuned for
universal context retrieval on public data. Nothing is tuned to one user's files.

Each phase ends with a benchmark run. A change ships only if it beats the current version on the
**public test split**, and doesn't regress on the author's private test set.

## Where Metis is now

| Step | Today |
|---|---|
| Shortlist | bge-small + `laya.predict_shortlist`: keeps 6 folders, then 6 files (question embedded alone) |
| Folder/file choice | Laya `choice` with a 384-token option budget, top 3 files + closest file checked |
| Passage | Laya `noul` on the 4 best excerpts per file, one batch |
| Abstention | Hand-set cut-offs: "not found" below 0.15; excerpts within 0.20 of the best are also sent (max 5) |
| Process | Every AI-app session starts its own Metis and its own Laya copy |
| Logging | Every lookup goes to `~/.metis/decisions.jsonl` (options, probabilities, choice, correct-if-known) |
| Benchmark | `bench/`, 30 questions (25 + 5 with no answer): folder 23, file 19, part 16 of 25; ~0.9 s GPU, ~9.6 s CPU |

The Nov 2026 accuracy pass also tried calibrated abstention (`fit_abstention_thresholds`),
`predict_long` for the passage step, and option rotation. None beat the version above, so they were removed. Retry
calibration once the decision log has a few hundred labelled lookups.

## Phase 0: Universal benchmark (needed by every other phase)

- [ ] Build a public suite that runs through the real Metis pipeline. Each dataset becomes a folder of
      files (`.txt`, `.pdf`, `.xlsx`) so indexing and reading are tested too:
  - Passages: BEIR subsets (SciFact, FiQA, NFCorpus), Natural Questions sample
  - "Not found": SQuAD 2.0 unanswerable questions
  - Spreadsheets: WikiTableQuestions tables saved as `.xlsx`
- [ ] Split it into **dev** (used for tuning) and **test** (only for reporting), so tuning can't overfit.
- [ ] `bench/run.py --suite public|private`, reporting right file, right passage, not-found accuracy,
      excerpts sent and time.
- [ ] Optional: wire it into `laya-evals` as a pass/fail gate.
- **Done when:** baseline numbers for today's Metis are recorded for both suites.

## Phase 1: One resident process

Problem seen in testing: each Claude session started its own Laya, overflowing the 4 GB GPU and
making searches about 10× slower.

- [ ] One Metis service holds the index, the sentence transformer and Laya. The desktop app starts
      it, or it starts at login.
- [ ] `server.py` becomes a thin client: Claude's stdio connection goes to the service over localhost.
- [ ] Idle unload after N minutes (Laya's `LAYA_IDLE_UNLOAD_SECONDS` pattern), reload on next request.
- [ ] Evaluate `laya-serve` for the Laya part instead of hosting it ourselves.
- **Done when:** two Claude sessions plus the app share one Laya copy, and GPU memory stays under 1.5 GB.

## Phase 2: Audit algorithm upgrades (each measured on dev, adopted only if better)

- [ ] **Shortlist:** compare our Granite shortlist with Laya's `predict_shortlist` (bge-small on the
      question alone). Keep the better one; if Laya's wins, delete our embedding code.
- [ ] **Option rotation:** for the file choice, average probabilities over 2–3 rotated option orders
      instead of one shuffle. Measure accuracy gained against time added.
- [ ] **`predict_long` for the passage step:** compare against today's excerpts + `noul`. Note it needs
      the multilingual checkpoint's long window; adopt only if it wins.
- [ ] **Caching hooks:** an `on_predict_start` cache keyed on question + index version, so repeat
      lookups cost nothing and file changes invalidate them.
- [ ] **Batching:** any per-file Laya work at indexing time uses `predict_batch` with `batch_size` and
      `sort_by_length`.
- Not applicable, recorded so they aren't redone: **typed `decide` answers** (Metis's reply already has
  a fixed shape) and **filing / new-folder abstention** (Metis doesn't file documents).

## Phase 3: Calibrated abstention

- [ ] Record Laya's probabilities and whether each answer was right, on the public **dev** split.
- [ ] Fit temperatures (`fit_temperature_map`) and per-bucket cut-offs (`fit_abstention_thresholds`,
      target error about 10%) for both decisions: file choice and excerpt check.
- [ ] Replace `NOT_FOUND_BELOW` and `CLOSE_ENOUGH` with the fitted values, saved alongside the checkpoint.
- [ ] When Metis abstains, it returns its top 2–3 candidates, or tells the AI app nothing was found.
- **Done when:** on the test split, the error rate of answers Metis gives matches the target.

## Phase 4: Fine-tune a universal Metis Laya

**Data** (converted to Laya JSONL rows: `{state, questions, expected}`):

| Teaches | Source | License |
|---|---|---|
| Does this excerpt answer the question (yes/no) | Natural Questions, SQuAD 2.0, HotpotQA | CC BY-SA |
| Saying no to unanswerable questions | SQuAD 2.0 unanswerable | CC BY-SA 4.0 |
| Which file holds the answer (choice) | The same sets: right passage among 5–12 hard negatives found by the shortlist, shown in Metis's `name: in <folder>: <excerpt>` format | as source |
| Spreadsheet rows | WikiTableQuestions, rows written as `Column: value; …` | CC BY-SA 4.0 |
| (only if non-commercial use is acceptable) | MS MARCO | non-commercial |

- [ ] Converter script: public datasets → JSONL in Metis's exact excerpt formats. 30k–100k rows,
      with a validation split. The Phase 0 test split is never used for training.
- [ ] Train: `laya-train --data train.jsonl --base convaiinnovations/laya --out ./laya-metis`, with
      option-order shuffling for `choice` questions. `finetune` calibrates the result.
- [ ] **Hardware:** full fine-tuning a 421M model needs roughly 8 GB+ of GPU memory. A 4 GB laptop GPU
      (4 GB) can't do it, so use a cloud GPU (the audit's estimate: 4–5 h on 2×T4 for ~30k questions).
- [ ] Refit Phase 3 abstention on the new checkpoint.
- [ ] Adopt only if it beats the base checkpoint on the public test split and doesn't regress on the private test set.
- [ ] Publish the checkpoint under a license compatible with its data, and point `LAYA_MODEL` at it.

## Phase 5: Learning from use (local to each user)

- [ ] A `feedback` MCP tool and thumbs up/down in the app's "Shared today" list.
- [ ] Corrections boost the right file for similar future questions, **on that user's machine only**.
- [ ] Users' data never trains the shared model without explicit consent.

## Separate from the audit, still needed before other people use Metis

- [ ] **Privacy guard:** at indexing time, Laya flags excerpts with passwords, IDs or other sensitive
      data, and Metis holds them back. Today Metis will return such an excerpt if it answers the question.
- [ ] Keep `docs/core.md` current at the end of each phase; it's out of date now.

## Order and size

| Phase | Depends on | Size |
|---|---|---|
| 0 Benchmark | — | M |
| 1 One process | — (can run alongside 0) | M |
| 2 Algorithm upgrades | 0 | M |
| 3 Calibration | 0 (refit after 4) | S |
| 4 Fine-tune | 0, 3 | L (plus cloud GPU time) |
| 5 Learning from use | 1 | M |

## Risks

- **Licenses:** MS MARCO is non-commercial; check every dataset before a commercial release.
- **Benchmark overfitting:** tune only on dev; report only on test.
- **512-token window** of the English checkpoint limits how much Laya reads per decision.
- **Fine-tuning may not transfer:** typed-decisions improved a lot on its own tasks, but that doesn't
  promise the same here. The test split decides.
