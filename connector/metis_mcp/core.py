"""Metis core: indexes the user's folders, then a sentence transformer shortlists files and Laya picks the file and
the passage that answer a question."""

import gc
import hashlib
import json
import os
import random
import re
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np
from docx import Document
from openpyxl import load_workbook
from pypdf import PdfReader

HOME = Path.home() / ".metis"  # Metis's own files; nothing is ever written inside the user's folders
SETTINGS = HOME / "settings.json"  # {"on": true, "folders": [...]}, written by the desktop app
INDEX = HOME / "index.json"  # each file's excerpts
LOG = HOME / "shared.json"  # what Metis sent to the AI app
VECTORS = HOME / "vectors"  # each file's excerpt meanings, one .npy per file
DECISIONS = HOME / "decisions.jsonl"  # every lookup's candidates, Laya's probabilities and the choice (see log_decision)

IDLE_UNLOAD_SECONDS = 600  # models are dropped from GPU/RAM after this long without a search; the next search reloads them (~12 s)
MIN_FREE_VRAM = 2 * 1024**3  # below this much free GPU memory Metis runs on the CPU instead of fighting for the GPU
MAX_FILE_BYTES = 50 * 1024 * 1024  # larger files are not indexed
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "site-packages", ".cache"}  # never indexed
FILE_TYPES = {".txt", ".md", ".csv", ".json", ".pdf", ".docx", ".xlsx"}
LAYA_MODEL = "convaiinnovations/laya"  # the general English Laya; "laya-typed-decisions" tied on accuracy but sent ~50% more excerpts
CHUNK_CHARS = 400  # longest excerpt; keeps question + excerpt inside Laya's 512-token window
DESCRIPTION_CHARS = 120  # how much of each file's best-matching excerpt Laya sees when comparing files
SHORTLIST = 6  # files bge-small hands Laya; fewer options leave each more of Laya's option budget (tested 6 vs 12)
FILES_TO_CHECK = 3  # Laya's top files whose excerpts it then checks one by one (plus the closest file by meaning)
EXCERPTS_PER_FILE = 4  # excerpts Laya checks in each of those files
WORD_WEIGHT = 0.1  # shared words add a little to meaning, so exact codes like "q1" still count
EMBEDDERS = [
    "BAAI/bge-small-en-v1.5",  # 33M parameters; the embedder Laya's README recommends for predict_shortlist
    "ibm-granite/granite-embedding-small-english-r2",  # 48M parameters, the previous default
    "sentence-transformers/all-MiniLM-L6-v2",  # fallback if the installed transformers can't load either
]
FOLDER_STEP = True  # narrow to folders first, then files inside them
FOLDER_K = 6  # folders bge-small hands Laya
FOLDER_MASS = 0.8  # keep Laya's top folders until their probabilities add up to this...
MAX_FOLDERS = 3  # ...or this many folders
HEAD_TOKENS = 384  # Laya's token budget for instructions + options (default 192 cut each of 12 options to ~14 tokens); the state is just the question
NOT_FOUND_BELOW = 0.15  # tuned per model on the author's benchmark: 0.15 for the English one, 0.35 for typed-decisions
CLOSE_ENOUGH = 0.20  # excerpts scoring within this of the best are sent too; every wrong answer had a runner-up this close
MAX_RESULTS = 5  # most excerpts sent to Claude for one question
VERSION = f"{CHUNK_CHARS}-4"  # change this whenever indexing changes; every index rebuilds
STOPWORDS = [
    "the", "and", "for", "are", "was", "were", "with", "that", "this",
    "from", "have", "has", "had", "not", "but", "you", "your", "our",
    "their", "they", "its", "what", "which", "who", "when", "where",
    "why", "how", "can", "could", "would", "should", "will", "about",
    "into", "than", "then", "there", "these", "those", "all", "any",
    "each", "also", "just", "more", "most", "some", "such", "only",
    "own", "same", "very", "does", "did", "doing", "been", "being",
]


def load(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):  # missing or damaged file
        return default


def save(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp") 
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temporary.replace(path)


def settings():
    return load(SETTINGS, {"on": True, "folders": []})


_laya, _laya_lock = None, threading.Lock()
_infer_lock = threading.RLock() 
_last_used = time.time()
_device, _device_lock, _gpu_lock_handle = None, threading.Lock(), None


def _take_gpu_slot():
    """Only one Metis process per machine may hold the GPU (a second one would load its own copy of the models and
    double the GPU memory). The OS drops this lock automatically if the process dies, so it can't go stale."""
    global _gpu_lock_handle
    HOME.mkdir(parents=True, exist_ok=True)
    handle = open(HOME / "gpu.lock", "a+b")
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False
    _gpu_lock_handle = handle
    return True


def _release_gpu_slot():
    global _gpu_lock_handle, _device
    if _gpu_lock_handle is not None:
        _gpu_lock_handle.close() 
        _gpu_lock_handle = None
    _device = None


def device():
    """"cuda" only if there is an NVIDIA GPU, no other Metis process holds it and it has MIN_FREE_VRAM free;
    otherwise "cpu". LAYA_DEVICE overrides. Decided once, then kept until the models are unloaded."""
    global _device
    with _device_lock:
        if _device is None:
            forced = os.environ.get("LAYA_DEVICE")
            if forced:
                _device = forced
            else:
                import torch
                _device = "cpu"
                if torch.cuda.is_available() and _take_gpu_slot():  # take the slot before touching CUDA
                    if torch.cuda.mem_get_info()[0] >= MIN_FREE_VRAM:
                        _device = "cuda"
                    else:
                        _release_gpu_slot()
                        _device = "cpu"
        return _device


def trim_gpu():
    """Hand memory PyTorch is only caching back to the GPU."""
    import torch
    if torch.cuda.is_available() and _device == "cuda":
        torch.cuda.empty_cache()


def agent():
    """Laya, loaded once (~12 s). The lock makes a search wait for a load that is already running."""
    global _laya, _last_used

    with _laya_lock:
        if _laya is None:
            import laya  # Laya logs to stderr, so it can't break the MCP connection on stdout

            dev = device()
            _laya = laya.load(LAYA_MODEL, device=dev)
            if dev == "cuda":
                # Half-size weights (~0.8 GB) fit small GPUs; empty_cache returns the freed full-size weights
                _laya.model.half()
                trim_gpu()
        _last_used = time.time()
    return _laya


def _unload_idle():
    """Drop both models (and the GPU slot) after IDLE_UNLOAD_SECONDS without a search."""
    global _laya, _embedder, _embedder_name
    if time.time() - _last_used < IDLE_UNLOAD_SECONDS or not _infer_lock.acquire(blocking=False):
        return
    try:
        with _laya_lock, _embedder_lock:
            if _laya is None and _embedder is None:
                return
            _laya, _embedder, _embedder_name = None, None, None
            gc.collect()
            trim_gpu()
            _release_gpu_slot()
    finally:
        _infer_lock.release()


def start_idle_unloader():
    """Call once from each long-lived process (the server and the app)."""
    def loop():
        while True:
            time.sleep(60)
            _unload_idle()
    threading.Thread(target=loop, daemon=True).start()


_embedder, _embedder_name, _embedder_lock = None, None, threading.Lock()


def embedder():
    """The sentence transformer that turns text into meaning vectors, loaded once. Tries EMBEDDERS in order."""
    global _embedder, _embedder_name

    with _embedder_lock:
        if _embedder is None:
            from sentence_transformers import SentenceTransformer  # logs to stderr too

            dev = device()
            errors = []
            for name in EMBEDDERS:
                try:
                    _embedder, _embedder_name = SentenceTransformer(name, device=dev), name
                    break
                except Exception as error:
                    errors.append(f"{name}: {error}")
            else:
                raise RuntimeError("No sentence transformer could load. " + " | ".join(errors))
    return _embedder


def embed(texts):
    """Meaning vectors, one row per text, scaled to length 1 so a dot product is the similarity."""
    model = embedder()
    if not texts:
        return np.zeros((0, model.get_sentence_embedding_dimension()), dtype=np.float32)
    return model.encode(list(texts), normalize_embeddings=True, convert_to_numpy=True,
                        batch_size=32, show_progress_bar=False).astype(np.float32)


# ---------- Indexing ----------

def read_pieces(path):
    """The file's text as [location, text] pairs: one per PDF page, Word section, Excel row, or whole text file."""
    kind = path.suffix.lower()
    
    if kind == ".pdf":
        return [[f"page {number}", page.extract_text() or ""] for number, page in enumerate(PdfReader(path).pages, 1)]
    
    if kind == ".docx":
        pieces = [["start", ""]]
        for paragraph in Document(path).paragraphs:
            if paragraph.style.name.startswith("Heading") and paragraph.text.strip():
                pieces.append([f"{paragraph.text.strip()} section", ""])
            pieces[-1][1] += paragraph.text + "\n"
        return pieces
    
    if kind == ".xlsx":
        pieces = []
        for sheet in load_workbook(path, read_only=True, data_only=True).worksheets:
            sheet.reset_dimensions()
            
            header = []                                                      # column names: the first row with 2 or more filled cells (title rows above it have 1)
            for row in sheet.iter_rows(values_only=True):
                row = ["" if value is None else str(value) for value in row]
                filled = [value for value in row if value]
                if not filled:
                    continue
                if not header and len(filled) >= 2:
                    header = row
                    continue
                cells = []                                                    # each row reads "Month: March; Budget: 5200"
                for column, value in enumerate(row):
                    if value:
                        name = header[column] if column < len(header) else ""
                        cells.append(f"{name}: {value}" if name else value)
                pieces.append([f'"{sheet.title}" sheet, {filled[0][:40]} row', "; ".join(cells)])
        return pieces
    
    return [["text", path.read_text(encoding="utf-8", errors="ignore")]]


def index_file(path):
    """Cut the file into excerpts of at most CHUNK_CHARS, each remembering where in the file it came from."""
    try:
        pieces = read_pieces(path)
    except Exception:                          # locked, password-protected or damaged files are skipped
        pieces = []
        
    excerpts = []
    
    for location, text in pieces:
        text = " ".join(text.split())                                            # squeeze line breaks and runs of spaces (some PDFs are mostly spaces)
        for start in range(0, len(text), CHUNK_CHARS):
            part = text[start:start + CHUNK_CHARS].strip()
            
            if part:
                where = location if len(text) <= CHUNK_CHARS else f"{location}, part {start // CHUNK_CHARS + 1}"
                excerpts.append({"at": where, "text": part})

    return {"mtime": path.stat().st_mtime, "excerpts": excerpts}


def walk(folder):
    """Every file under `folder`, skipping hidden folders and SKIP_DIRS (virtual environments, node_modules, .git...)."""
    for root, dirs, names in os.walk(folder):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for name in names:
            yield Path(root) / name


def refresh():
    """Index new or changed files in the shared folders and drop files that are gone. Returns {path: index entry}."""
    saved = load(INDEX, {})
    old = saved.get("files", {}) if saved.get("version") == VERSION else {}
    files = {}

    for folder in settings()["folders"]:
        for path in walk(folder):
            
            if path.suffix.lower() not in FILE_TYPES or path.name.startswith("~$") or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
                continue  # ~$ files are Office's temporary lock files
            entry = old.get(str(path))
            
            if entry is None or entry["mtime"] != path.stat().st_mtime:
                entry = index_file(path)
                
            files[str(path)] = entry

    save(INDEX, {"version": VERSION, "files": files})

    # Embed new or changed files now, so searches only embed the question. Drop vectors of files that are gone.
    keep = {vector_key(path, entry) for path, entry in files.items()}
    for path, entry in files.items():
        vectors(path, entry)
    for stale in VECTORS.glob("*.npy"):
        if stale.stem not in keep:
            try:
                stale.unlink()
            except OSError:  # the app and the server may both be cleaning up
                pass
    return files


_vectors = {}  # path -> (key, matrix), so each file's vectors are read from disk once per process


def excerpt_text(path, excerpt):
    """What the sentence transformer reads for an excerpt. The file and folder names help it match questions that
    name a topic (e.g. "budget")."""
    path = Path(path)
    return f'{path.stem.replace("_", " ")} ({path.parent.name}), {excerpt["at"]}: {excerpt["text"]}'


def vector_key(path, entry):
    embedder()  # the model's name is part of the key, so switching models re-embeds everything
    return hashlib.sha1(f"{path}|{entry['mtime']}|{VERSION}|{_embedder_name}".encode()).hexdigest()


def vectors(path, entry):
    """One meaning vector per excerpt, kept in ~/.metis/vectors/ until the file changes."""
    key = vector_key(path, entry)
    cached = _vectors.get(path)
    if cached and cached[0] == key:
        return cached[1]

    file = VECTORS / f"{key}.npy"
    try:
        matrix = np.load(file)
    except (OSError, ValueError):
        matrix = None
    if matrix is None or len(matrix) != len(entry["excerpts"]):
        matrix = embed([excerpt_text(path, excerpt) for excerpt in entry["excerpts"]])
        VECTORS.mkdir(parents=True, exist_ok=True)
        temporary = VECTORS / f"{key}.{os.getpid()}.tmp"  # write then swap, like save()
        with open(temporary, "wb") as handle:
            np.save(handle, matrix)
        temporary.replace(file)

    _vectors[path] = (key, matrix)
    return matrix


# ---------- Searching ----------

def words(text):
    """Meaningful words. Keeps short codes and numbers like "q1" and "12"."""
    return {word for word in re.findall(r"[a-z0-9]+", text.lower())
            if word not in STOPWORDS and (len(word) > 2 or not word.isalpha())}


def excerpt_scores(question, files):
    """How well every excerpt of every file matches the question: closeness in meaning (from the sentence
    transformer) plus a little for each word it shares with the question. Returns {path: array, one per excerpt}."""
    question_vector = embed([question])[0]
    asked = words(question)
    scores = {}
    for path, entry in files.items():
        if entry["excerpts"]:
            meaning = vectors(path, entry) @ question_vector
            shared = np.array([len(asked & words(excerpt["text"])) for excerpt in entry["excerpts"]], dtype=np.float32)
            scores[path] = meaning + WORD_WEIGHT * shared / max(len(asked), 1)
    return scores


_cached_embed = None


def choose(query, state, instructions, criteria, k):
    """Laya's choice among `criteria` ({label: description}) for `state`, after bge-small keeps the `k` labels closest
    to `query` (the question alone, or a new file's name and opening). Returns {label: probability} over the kept
    labels, best first."""
    global _cached_embed
    import laya

    if len(criteria) == 1:
        return {next(iter(criteria)): 1.0}
    if _cached_embed is None:
        _cached_embed = laya.cached_embed_fn(embed, maxsize=8192)  # labels repeat between questions

    def query_alone(texts):
        # predict_shortlist embeds "instructions + state" first; Laya's README found the question alone ranks as well
        # or better, so the first text is swapped for the bare query.
        return _cached_embed([query] + list(texts[1:]))

    labels = list(criteria)
    random.Random(query).shuffle(labels)  # Laya leans toward some positions; this stops the same labels always winning
    answer = laya.predict_shortlist(
        agent(), state,
        {"pick": {"type": "choice", "instructions": instructions, "criteria": {label: criteria[label] for label in labels}}},
        embed_fn=query_alone, k=k, head_max_len=HEAD_TOKENS,
    )
    chances = answer["answers"]["pick"]["probabilities"]
    return dict(sorted(chances.items(), key=lambda item: item[1], reverse=True))


def folder_name(folder):
    """A folder's path inside the shared folder it belongs to ("Marketing/Investors"); a shared folder is its own name."""
    for base in settings()["folders"]:
        try:
            relative = Path(folder).relative_to(base).as_posix()
            return Path(base).name if relative == "." else relative
        except ValueError:
            continue
    return Path(folder).name


def pick_folders(question, files, scores, trace):
    """Laya's most likely folders: bge-small keeps FOLDER_K, Laya picks; kept until FOLDER_MASS or MAX_FOLDERS.
    The folder of the closest file by meaning is always kept too."""
    by_folder = {}
    for path in files:
        if path in scores:
            by_folder.setdefault(str(Path(path).parent), []).append(path)
    names = {folder_name(folder): folder for folder in by_folder}
    criteria = {name: ", ".join(sorted(Path(p).stem.replace("_", " ") for p in by_folder[folder]))[:300]
                for name, folder in names.items()}
    chances = choose(question, {"question": question}, "Which folder most likely holds the answer to `question`?",
                     criteria, FOLDER_K)
    trace["folders"] = chances
    kept, mass = [], 0.0
    for name, chance in chances.items():
        if mass >= FOLDER_MASS or len(kept) >= MAX_FOLDERS:
            break
        kept.append(names[name])
        mass += chance
    closest = str(Path(max(scores, key=lambda path: scores[path].max())).parent)
    if closest not in kept:
        kept.append(closest)
    return [path for folder in kept for path in by_folder[folder]]


def rank_files(question, paths, files, scores, trace):
    """Laya's choice over the files, best first: bge-small keeps SHORTLIST, Laya picks. Each file is described by
    its excerpt closest to this question."""
    if len(paths) <= 1:
        return list(paths)
    names = {}  # readable name shown to Laya -> path
    descriptions = {}
    for path in paths:
        name = Path(path).stem.replace("_", " ")  # in testing, Laya chose better by readable names
        name = name if name not in names else path
        names[name] = path
        best = files[path]["excerpts"][int(scores[path].argmax())]
        descriptions[name] = f"in {Path(path).parent.name}: {best['text'][:DESCRIPTION_CHARS]}"
    chances = choose(question, {"question": question}, "Which file most likely holds the answer to `question`?",
                     descriptions, SHORTLIST)
    trace["files"] = {names[name]: chance for name, chance in chances.items()}
    return [names[name] for name in chances]


_decisions_lock = threading.Lock()


def log_decision(question, trace, answer, source="ai app", correct=None):
    """Append one line to ~/.metis/decisions.jsonl: the question, every candidate Laya weighed with its probability,
    what Metis chose, and whether that was right when known (the benchmark knows; an AI app's request doesn't, so
    `correct` is null). These are the labelled examples future calibration and fine-tuning need."""
    top = answer["results"][0] if answer.get("found") else None
    record = {
        "time": datetime.now().isoformat(timespec="seconds"), "source": source, "question": question,
        "folders": trace.get("folders"),  # {folder: Laya probability}, the shortlisted folders
        "files": trace.get("files"),  # {file path: Laya probability}, the shortlisted files
        "excerpts": [{"file": path, "location": at, "p": p, "text": text}  # Laya's P(excerpt answers it)
                     for path, at, p, text in trace.get("excerpts") or []],
        "choice": {"found": bool(top), "file": top and top["file"], "location": top and top["location"],
                   "sent": len(answer.get("results") or [])},
        "correct": correct,
    }
    DECISIONS.parent.mkdir(parents=True, exist_ok=True)
    with _decisions_lock, open(DECISIONS, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def retrieve(question, log=False, trace=None):
    """The passages that answer `question` (1 to MAX_RESULTS), each with where it is and the file's absolute path.
    `trace`, if given, is filled with Laya's probabilities at each step (used by the benchmark). With `log`, the
    reply is recorded for the app's "Shared today" list and the decision log."""
    trace = {} if trace is None else trace
    with _infer_lock:
        try:
            answer = _retrieve(question, trace)
        finally:
            trim_gpu()
    if log:
        if answer.get("found"):
            now = datetime.now()
            shared = load(LOG, [])[-200:]
            for result in answer["results"]:
                shared.append({"date": now.strftime("%Y-%m-%d"), "time": now.strftime("%I:%M %p").lstrip("0"),
                               "file": Path(result["file"]).name, "location": result["location"]})
            save(LOG, shared)
        if trace:  # nothing to log when Metis is off or has no folders
            log_decision(question, trace, answer)
    return answer


def _retrieve(question, trace):
    if not settings()["on"]:
        return {"found": False, "message": "Metis is turned off in the Metis app."}
    files = refresh()
    if not files:
        return {"found": False, "message": "No folders are shared with Metis yet."}

    scores = excerpt_scores(question, files)
    # 1. Folders: bge-small keeps the closest, Laya picks the likely ones.
    candidates_files = pick_folders(question, files, scores, trace) if FOLDER_STEP else list(scores)

    # 2. Files inside them: bge-small keeps SHORTLIST, Laya ranks them; the closest file by meaning is checked too.
    closest = max(candidates_files, key=lambda path: scores[path].max())
    chosen = rank_files(question, candidates_files, files, scores, trace)[:FILES_TO_CHECK]
    if closest not in chosen:
        chosen.append(closest)

    # 3. From each chosen file, take its excerpts closest to the question.
    candidates = []  # (path, excerpt)
    for path in chosen:
        for row in np.argsort(-scores[path])[:EXCERPTS_PER_FILE]:
            candidates.append((path, files[path]["excerpts"][int(row)]))
    if not candidates:
        return {"found": False, "message": "Metis could not read the files that matched."}

    # 4. Laya scores every candidate at once: does it answer the question?
    results = agent().predict_batch(
        [{"question": question, 
          "excerpt": f'{excerpt["at"]}: {excerpt["text"]}'} for _, excerpt in candidates],
        {"answers": 
            {"type": "noul", 
             "instructions": "Does `excerpt` help answer `question`?"}},        ## Laya is used to assist in excerpt selection from a given files (to respond to user prompt)
    )
    scores = [result["answers"]["answers"]["noul"] for result in results]
    trace["excerpts"] = [(path, excerpt["at"], round(score, 4), excerpt["text"])
                         for (path, excerpt), score in zip(candidates, scores)]
    ranked = sorted(range(len(candidates)), key=scores.__getitem__, reverse=True)
    best = scores[ranked[0]]
    if best < NOT_FOUND_BELOW:
        return {"found": False, "message": "Nothing in the shared folders answers this."}

    # 5. Excerpts that score nearly as high as the best may answer the question just as well, so Claude gets
    #    all of them (at most MAX_RESULTS) and picks. A clear winner is sent alone.
    answer = {"found": True, "results": []}
    for number in ranked[:MAX_RESULTS]:
        if best - scores[number] >= CLOSE_ENOUGH or scores[number] < NOT_FOUND_BELOW:
            break
        path, excerpt = candidates[number]
        answer["results"].append({"context": excerpt["text"], "location": excerpt["at"], "file": path,
                                  "confidence": round(scores[number], 2)})
    return answer


def shared_today():
    """Today's entries from the log, newest first, for the app's "Shared with your AI app today" list."""
    today = datetime.now().strftime("%Y-%m-%d")
    return [entry for entry in reversed(load(LOG, [])) if entry["date"] == today]
