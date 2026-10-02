# model.py
import os, re
from pathlib import Path
import ollama
from laya import Router

ROOT = Path(os.environ.get("METIS_ROOT", "/srv"))
THRESHOLD = 0.7
router = Router(preload=True)
llm = ollama.Client()

def pick_folder(text: str) -> str:
    folders = [p.name for p in ROOT.iterdir() if p.is_dir()]
    if folders:
        scores = router.predict(text, {f: {"type": "noul", "instructions": f"Does this file belong in the folder '{f}'?"} for f in folders})["answers"]
        best = max(folders, key=lambda f: scores[f]["noul"])
        if scores[best]["noul"] >= THRESHOLD:
            return best
    name = llm.generate(model="llama3", prompt=f"Reply with only a 1-2 word folder name for:\n\n{text[:2000]}")["response"]
    return re.sub(r"[^a-z0-9_]", "", name.strip().lower().replace(" ", "_")) or "unsorted"

def store_file(filename: str, data: bytes) -> Path:
    path = ROOT / pick_folder(data.decode(errors="ignore")) / Path(filename).name
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(data)
    return path
