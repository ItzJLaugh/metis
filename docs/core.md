# `core.py` reference

`core.py` does two jobs: **indexing** the user's folders and **searching** them with an embedding model
(bge-small) and a decision model (Laya). `server.py` calls `retrieve(prompt, log=True)` for the AI app. `app.py` calls
`settings`, `save`, `load`, `refresh`, `retrieve` and `shared_today`.

```
Indexing (once per file, again only when it changes)
  file ─→ read_pieces ─→ index_file ─→ ~/.metis/index.json      {excerpts}
                                  └─→ vectors ─→ ~/.metis/vectors/  {one bge-small vector per excerpt}

Searching (each question)
  question ─→ excerpt_scores ─→ pick_folders ─→ rank_files ─→ Laya yes/no on excerpts ─→ best, or "not found"
              (bge-small +       (keep 6, Laya    (keep 6, Laya   (4 per file, one batch)
               shared words)      picks)           picks top 3)
                                                                                  └─→ log_decision ─→ decisions.jsonl
```

## Storage: `~/.metis/`

| Name | File | Holds |
|---|---|---|
| `SETTINGS` | `settings.json` | `{"on": true, "folders": [...]}`, written by the app |
| `INDEX` | `index.json` | every file's excerpts |
| `VECTORS` | `vectors/*.npy` | each file's excerpt vectors, named by a hash of path, `mtime`, `VERSION` and model |
| `LOG` | `shared.json` | what was sent to the AI app, for the app's "Shared today" list |
| `DECISIONS` | `decisions.jsonl` | one line per lookup: question, options, Laya's probabilities, choice, correct-if-known |

An `index.json` entry:
```json
"C:\\...\\School\\lease.pdf": {
  "mtime": 1793071145.8,
  "excerpts": [{"at": "page 1", "text": "Lease agreement. ..."}, {"at": "page 2", "text": "Pets section: ..."}]
}
```

## Settings

| Name | Value | Meaning |
|---|---|---|
| `LAYA_MODEL` | `convaiinnovations/laya` | the English Laya checkpoint |
| `EMBEDDERS` | bge-small, then Granite, then MiniLM | embedding models tried in order |
| `CHUNK_CHARS` | 400 | longest excerpt; question + excerpt fit Laya's 512-token window |
| `SEARCH` | `"funnel"` | `"funnel"`: folders → files → excerpts; `"flat"`: score every excerpt and let Laya check the best `FLAT_K` |
| `FLAT_K` | 16 | excerpts Laya checks in the flat search |
| `FOLDER_STEP` | True | narrow to folders before files |
| `FOLDER_K` | 6 | folders bge-small keeps for Laya's folder choice |
| `FOLDER_MASS` / `MAX_FOLDERS` | 0.8 / 3 | keep Laya's top folders until 80% probability or 3 folders |
| `SHORTLIST` | 6 | files bge-small keeps for Laya's file choice |
| `HEAD_TOKENS` | 384 | Laya's token budget for instructions + options (its default 192 truncates options) |
| `DESCRIPTION_CHARS` | 120 | how much of each file's best excerpt Laya sees when choosing files |
| `FILES_TO_CHECK` | 3 | Laya's top files whose excerpts get checked, plus the closest file by meaning |
| `EXCERPTS_PER_FILE` | 4 | excerpts Laya checks per file |
| `WORD_WEIGHT` | 0.1 | how much shared words add to meaning, so codes like `q1` still count |
| `NOT_FOUND_BELOW` | 0.15 | best yes-probability under this → "not found" |
| `CLOSE_ENOUGH` / `MAX_RESULTS` | 0.20 / 5 | excerpts within 0.20 of the best are sent too, up to 5 |
| `VERSION` | `"400-4"` | change it whenever indexing changes; the index rebuilds |

## Functions

**`load` / `save`**: read and write JSON. `save` writes a temporary file and swaps it in, because the app and the
server both use these files.

**`agent()`**: Laya, loaded once (GPU if available; `LAYA_DEVICE=cpu` forces the CPU). `server.py` starts loading it
in the background when the AI app launches Metis.

**`embedder()` / `embed(texts)`**: the first embedding model in `EMBEDDERS` that loads, and vectors of length 1 so a
dot product is the similarity.

**`read_pieces(path)`**: a file's text as `[location, text]` pairs:

| File | One pair per | Location |
|---|---|---|
| PDF | page | `page 2` |
| Word | heading section | `Budget section` (`start` before the first heading) |
| Excel | row, written `Column: value; ...` (column names = first row with 2+ filled cells) | `"Monthly" sheet, March row` |
| other | whole file | `text` |

**`index_file(path)`**: cuts those pairs into excerpts of at most 400 characters (`page 3, part 2` when split).
Locked or damaged files get no excerpts instead of crashing Metis.

**`refresh()`**: re-indexes new or changed files (by `mtime`), drops files that are gone, embeds what changed and
deletes stale vectors. Returns `{path: entry}`.

**`excerpt_scores(question, files)`**: every excerpt's score = similarity to the question (the question is embedded
alone) + `WORD_WEIGHT` × share of the question's words it contains.

**`choose(query, state, instructions, criteria, k)`**: one Laya choice through `laya.predict_shortlist`: bge-small
keeps the `k` options closest to the question, then Laya returns a probability for each. Options are shuffled per
question so Laya's position bias doesn't always favour the same ones.

**`pick_folders(...)`**: folders described by their file names, `choose` with `FOLDER_K`; keeps the top folders by
`FOLDER_MASS` / `MAX_FOLDERS`, plus the folder of the closest file.

**`rank_files(...)`**: files in those folders, each described as `in <folder>: <start of its best excerpt>`, `choose`
with `SHORTLIST`. Returns paths best first.

**`retrieve(question, log=False, trace=None)`**: the whole search. Stops early if Metis is off or no folders are
shared, then runs the steps above. Laya answers "Does `excerpt` help answer `question`?" for every candidate excerpt
in one `predict_batch`. The best answer under `NOT_FOUND_BELOW` gives `{"found": false, "message"}`; otherwise
`{"found": true, "results": [{context, location, file, confidence}, ...]}`. With `log=True` (the AI app's requests)
it also updates `shared.json` and the decision log. `trace` is filled with Laya's probabilities at each step.

**`log_decision(question, trace, answer, source, correct)`**: appends one line to `decisions.jsonl`. `correct` is
null for AI-app requests; the benchmark fills it in.

**`shared_today()`**: today's `shared.json` entries, newest first.

## Benchmark

`bench/run.py` scores `retrieve` on `bench/questions.jsonl` and appends a row to `bench/results.md`. `docs/build-plan.md` also
records what was tried and dropped (calibrated abstention, `predict_long`, option rotation) and why.
