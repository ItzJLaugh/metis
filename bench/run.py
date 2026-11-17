"""Score Metis on bench/questions.jsonl: folder, file and part accuracy, abstention, and latency.

From the repo root:
    .venv/Scripts/python bench/run.py --step "0 baseline"            # scores and appends a row to bench/results.md
    .venv/Scripts/python bench/run.py --step "0 baseline" --device cpu
    .venv/Scripts/python bench/run.py --dry                          # scores without writing results.md

Accuracy is scored on Metis's *top* answer for the 25 answerable questions:
  folder = the top file's folder (relative to the shared folder) matches
  file   = the top file's name matches
  part   = the top answer's text contains the expected span (case and spacing ignored)
An abstention on an answerable question counts as wrong on all three.
Abstain rate is shown two ways: correct abstentions on the 5 no-answer questions, and wrong abstentions on the 25.
Latency excludes loading Laya and indexing (both happen once, before the clock starts).
"""

import argparse
import json
import os
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

BENCH = Path(__file__).resolve().parent
ROOT = BENCH.parent
sys.path.insert(0, str(ROOT / "connector"))
sys.stdout.reconfigure(encoding="utf-8")


def norm(text):
    return " ".join(str(text or "").lower().split())


def top_answer(answer):
    """(abstained, path, text, how many results were sent) from either reply shape Metis has used."""
    if "abstained" in answer:  # typed reply: path, span, confidence, abstained, candidates
        if answer["abstained"] or not answer.get("path"):
            return True, None, "", len(answer.get("candidates") or [])
        return False, answer["path"], answer.get("span", ""), 1 + len(answer.get("candidates") or [])
    results = (answer.get("results") or []) if answer.get("found") else []
    if not results:
        return True, None, "", len(answer.get("candidates") or [])
    return False, results[0]["file"], results[0]["context"], len(results)


def folder_of(path, shared):
    parent = Path(path).parent
    for base in shared:
        try:
            rel = parent.relative_to(base).as_posix()
            return "." if rel in ("", ".") else rel
        except ValueError:
            continue
    return parent.as_posix()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", default="dry run", help="row label in results.md")
    parser.add_argument("--device", help="cpu or cuda (default: Metis's own choice)")
    parser.add_argument("--questions", default=str(BENCH / "questions.jsonl"),
                        help="your questions (default bench/questions.jsonl; see bench/questions.example.jsonl)")
    parser.add_argument("--dry", action="store_true", help="don't write results.md")
    parser.add_argument("--note", default="", help="free text for the notes column")
    parser.add_argument("--no-decision-log", action="store_true", help="don't add this run to ~/.metis/decisions.jsonl")
    parser.add_argument("--set", action="append", default=[], metavar="NAME=VALUE",
                        help="override a core.py setting for this run, e.g. --set SHORTLIST=6 (value is Python)")
    args = parser.parse_args()
    if args.device:
        os.environ["LAYA_DEVICE"] = args.device

    if not Path(args.questions).exists():
        raise SystemExit(f"No questions at {args.questions}. Copy bench/questions.example.jsonl to bench/questions.jsonl "
                         "and write questions about your own files (one JSON object per line).")
    from metis_mcp import core
    for setting in args.set:
        name, value = setting.split("=", 1)
        if not hasattr(core, name):
            raise SystemExit(f"core.py has no setting {name}")
        setattr(core, name, eval(value))

    questions = [json.loads(line) for line in Path(args.questions).read_text(encoding="utf-8").splitlines() if line.strip()]
    shared = core.settings()["folders"]
    core.agent()
    core.refresh()
    core.retrieve("warm-up question")  # first call pays one-off CUDA/tokenizer costs

    rows, times = [], []
    for q in questions:
        trace = {}
        start = time.perf_counter()
        answer = core.retrieve(q["question"], trace=trace)
        times.append(time.perf_counter() - start)
        abstained, path, text, sent = top_answer(answer)
        row = {"id": q["id"], "question": q["question"], "seconds": round(times[-1], 3), "abstained": abstained,
               "sent": sent, "got_file": Path(path).name if path else None,
               "got_folder": folder_of(path, shared) if path else None, "answerable": q["file"] is not None}
        if q["file"] is None:
            row.update(folder_ok=None, file_ok=None, part_ok=None)
        else:
            row["folder_ok"] = bool(path) and row["got_folder"] == q["folder"]
            row["file_ok"] = bool(path) and row["got_file"] == q["file"]
            row["part_ok"] = row["file_ok"] and norm(q["part"]) in norm(text)
            # When Metis abstains with candidates: is the right file (and span) among them?
            offered = answer.get("candidates") or []
            row["candidate_file"] = any(Path(c.get("file") or c.get("path") or "").name == q["file"] for c in offered)
            row["candidate_part"] = any(Path(c.get("file") or c.get("path") or "").name == q["file"] and
                                        norm(q["part"]) in norm(c.get("context") or c.get("span")) for c in offered)
        row["raw"] = answer
        row["trace"] = trace
        row["expected"] = {"folder": q["folder"], "file": q["file"], "part": q["part"]}
        if not args.no_decision_log:  # labelled examples for the decision log: here we know what was right
            core.log_decision(q["question"], trace, answer, source="benchmark", correct={
                **row["expected"], "answered_right": row["part_ok"] if q["file"] else abstained})
        rows.append(row)
        mark = ("ABSTAIN-OK" if abstained else "ANSWERED!") if q["file"] is None else \
               "OK" if row["part_ok"] else "FILE" if row["file_ok"] else "FOLDER" if row["folder_ok"] else \
               "ABSTAIN!" if abstained else "BAD"
        print(f"{mark:10} {times[-1]:5.2f}s  #{q['id']:<2} {q['question'][:60]:60} -> {row['got_file']}")

    answerable = [r for r in rows if r["answerable"]]
    unanswerable = [r for r in rows if not r["answerable"]]
    n = len(answerable)
    folder = sum(r["folder_ok"] for r in answerable)
    file_ = sum(r["file_ok"] for r in answerable)
    part = sum(r["part_ok"] for r in answerable)
    good_abstain = sum(r["abstained"] for r in unanswerable)
    bad_abstain = sum(r["abstained"] for r in answerable)
    abstain_rate = sum(r["abstained"] for r in rows) / len(rows)
    folder_step = [r for r in answerable if r["trace"].get("folders")]
    if folder_step:  # how often Laya's own first folder was right (before the file step can rescue it)
        roots = {Path(base).name for base in shared}
        top1 = sum((lambda name: "." if name in roots else name)(next(iter(r["trace"]["folders"]))) == r["expected"]["folder"]
                   for r in folder_step)
        print(f"Laya folder step, first pick right: {top1}/{len(folder_step)}")
        args.note = (args.note + f"; folder step top-1 {top1}/{len(folder_step)}").lstrip("; ")
    device = core.agent().device
    summary = (f"folder {folder}/{n}  file {file_}/{n}  part {part}/{n}  "
               f"abstain {abstain_rate:.0%} (no-answer {good_abstain}/{len(unanswerable)}, answerable {bad_abstain}/{n})  "
               f"latency avg {statistics.mean(times):.2f}s median {statistics.median(times):.2f}s max {max(times):.2f}s on {device}")
    print("\n" + summary)

    out = BENCH / "out"
    out.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (out / f"{stamp}.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False, default=str) for r in rows),
                                        encoding="utf-8")
    if args.dry:
        return
    results = BENCH / "results.md"
    if not results.exists():
        results.write_text("# Metis benchmark results\n\n"
                           "30 questions in `bench/questions.jsonl` (25 answerable, 5 with no answer). Accuracy is on the "
                           "top answer for the 25 answerable ones. Abstain = share of all 30 questions where Metis said "
                           "nothing found (no-answer: right abstentions out of 5; answerable: wrong abstentions out of 25).\n\n"
                           "| Step | Folder | File | Part | Abstain (no-answer / answerable) | Avg latency | Median | Device | Sent/q | Notes |\n"
                           "|---|---|---|---|---|---|---|---|---|---|\n", encoding="utf-8")
    sent_avg = statistics.mean(r["sent"] for r in rows)
    offered = [r for r in answerable if r["abstained"] and r["sent"]]
    if offered:
        hit_file = sum(r["candidate_file"] for r in offered)
        hit_part = sum(r["candidate_part"] for r in offered)
        useful = part + hit_part
        print(f"abstained with candidates: {len(offered)}/{n}; right file among them {hit_file}, right part {hit_part}; "
              f"right part in the reply (answer or candidates) {useful}/{n}")
        args.note = (f"{len(offered)} answerable got candidates (right file {hit_file}, right part {hit_part}); "
                     f"right part anywhere in reply {useful}/{n}; " + args.note).rstrip("; ")
    with results.open("a", encoding="utf-8") as handle:
        handle.write(f"| {args.step} | {folder}/{n} ({folder / n:.0%}) | {file_}/{n} ({file_ / n:.0%}) | "
                     f"{part}/{n} ({part / n:.0%}) | {abstain_rate:.0%} ({good_abstain}/{len(unanswerable)} · {bad_abstain}/{n}) | "
                     f"{statistics.mean(times):.2f}s | {statistics.median(times):.2f}s | {device} | {sent_avg:.1f} | {args.note} |\n")


if __name__ == "__main__":
    main()
