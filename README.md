# Metis

Metis connects your AI app (for example Claude Desktop) to the folders on your computer. When the AI needs
something from your files, it asks Metis once. Metis finds the right file and the part of it that answers, and sends
back only that part plus the file's path, so the AI doesn't have to read whole folders.

Everything runs on your own computer. Your files are never copied, uploaded, or changed.

## How it finds things

Two small local models share the work:

- **bge-small** (an embedding model) turns text into numbers so text with similar meaning can be compared. It's fast
  but rough.
- **[Laya](https://github.com/NandhaKishorM/laya)** (a decision model) doesn't write text. Given a question and a
  few options, it returns a probability for each option.

1. **Index (once per file).** When you share a folder, Metis cuts every file into short excerpts of up to 400
   characters (a PDF page, a Word section, an Excel row) and bge-small embeds each one. Files are re-indexed only
   when they change. The index lives in `~/.metis/`, never in your folders.
2. **Look up (every question), narrowing like a funnel:**
   1. bge-small scores every excerpt against the question.
   2. **Folders:** bge-small keeps the 6 closest folders and Laya picks among them.
   3. **Files:** bge-small keeps 6 files from those folders, and Laya picks its top 3.
   4. **Excerpts:** Laya answers "does this excerpt answer the question?" for the best 4 excerpts of each file.
   5. Metis sends the best excerpt(s), or says nothing was found.
3. **Log.** Every lookup is recorded in `~/.metis/decisions.jsonl` (question, options, Laya's probabilities, choice),
   so Metis can be calibrated or Laya fine-tuned later.

Supported files: `.txt`, `.md`, `.csv`, `.json`, `.pdf`, `.docx`, `.xlsx`.

## Install

Needs Python 3.10 or newer. Built and tested on Windows; macOS is untested.

```
git clone https://github.com/ItzJLaugh/metis.git
cd metis
python -m venv .venv
.venv\Scripts\activate            # macOS: source .venv/bin/activate
pip install -r connector/requirements.txt
```

The first run downloads the two models from Hugging Face (about 1.5 GB in total). After that, Metis works offline.

**Optional: NVIDIA GPU (much faster).** Close Claude Desktop and the Metis app first, then run:

```
pip install "torch==2.14.1+cu130" --index-url https://download.pytorch.org/whl/cu130
```

Metis uses the GPU automatically when one is available. Set `LAYA_DEVICE=cpu` to force the CPU.

## Set up Metis with your AI app

1. Start the Metis desktop app: `cd connector` then `python -m metis_mcp.app`
2. Click **+ Add folder…** and pick a folder Metis may read.
3. Click **Connect** next to Claude Desktop.
4. Restart Claude Desktop. Claude now has a `request` tool it uses when a question depends on your files.

## Measure it

`bench/run.py` scores Metis on your own questions: right folder, right file, right part, "nothing found" when
nothing matches, and time per question. Copy `bench/questions.example.jsonl` to `bench/questions.jsonl` and write
questions about your shared folders, one JSON object per line: `question`, `folder` (relative to the shared folder),
`file`, and `part` (text the answer must contain). Use `null` for questions your files can't answer. Results are
added to `bench/results.md`. Both files describe your files, so they're git-ignored.

```
.venv\Scripts\python bench/run.py --step "my test"
```

## Files

- `connector/metis_mcp/core.py`: indexing, search and the decision log ([docs/core.md](docs/core.md))
- `connector/metis_mcp/server.py`: the `request` tool the AI app calls
- `connector/metis_mcp/app.py` + `app.html`: the desktop app
- `connector/compare.py`: cost and time of Claude with vs without Metis (needs an Anthropic API key)
- `bench/`: the benchmark scorer and an example question file

## License

Apache License 2.0, see [LICENSE](LICENSE). Metis downloads Laya (Apache 2.0) and bge-small (MIT) from Hugging Face on first run; their own licenses apply to them.
