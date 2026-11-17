"""Compare Claude answering from an attached folder vs Claude using Metis: time, tokens and cost.

From the connector folder:  python compare.py        (all questions)
                            python compare.py 5      (first 5 questions only)

Needs a Claude API key (ANTHROPIC_API_KEY); API usage is billed. Uses the questions in
../bench/questions.jsonl and the folders shared in the Metis app. Results go to compare_results.json.
"""

import json
import statistics
import sys
import time
from pathlib import Path

import anthropic

from metis_mcp import core

MODEL = "claude-opus-5-5"
PRICE = {"input": 4.00, "cache_write": 5.00, "cache_read": 0.20, "output": 20.00}  # $ per million tokens
SYSTEM = "Answer the user's question from their files. Keep the answer short. If the files don't answer it, say so."

client = anthropic.Anthropic()
files = core.refresh()


def file_text(path):
    return "\n".join(f"[{e['at']}] {e['text']}" for e in files[path]["excerpts"])


# ---------- Folder mode: Claude explores the files itself, like an attached folder ----------

FOLDER_TOOLS = [
    {"name": "list_files", "description": "List every file in the user's shared folders.",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "search_files", "description": "Find files whose text contains a word or phrase (not case-sensitive).",
     "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"],
                      "additionalProperties": False}},
    {"name": "read_file", "description": "Read the full text of one file, given its path from list_files.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"],
                      "additionalProperties": False}},
]


def run_folder_tool(name, args):
    if name == "list_files":
        return "\n".join(files)
    if name == "search_files":
        hits = [path for path in files if args["text"].lower() in file_text(path).lower()]
        return "\n".join(hits) or "No file contains that text."
    if name == "read_file":
        return file_text(args["path"]) if args["path"] in files else "No such file."


# ---------- Metis mode: Claude makes one request and gets back only the passage ----------

METIS_TOOLS = [
    {"name": "request", "description": "Find the passage of the user's files that answers this prompt, and the file's path.",
     "input_schema": {"type": "object", "properties": {"prompt": {"type": "string"}}, "required": ["prompt"],
                      "additionalProperties": False}},
]


def run_metis_tool(name, args):
    return json.dumps(core.retrieve(args["prompt"]))


# ---------- One question, one mode ----------

def ask(question, tools, run_tool):
    """Run Claude until it answers. Returns time, token counts, cost, tool calls, answer, and all tool output."""
    messages = [{"role": "user", "content": question}]
    tokens = {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0}
    tool_calls, seen = 0, ""
    start = time.time()
    while True:
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM,
            tools=tools,
            messages=messages,
            output_config={"effort": "medium"},
            cache_control={"type": "ephemeral"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",  # if a safety check declines, the API retries on another model
        )
        usage = response.usage
        tokens["input"] += usage.input_tokens
        tokens["cache_write"] += usage.cache_creation_input_tokens or 0
        tokens["cache_read"] += usage.cache_read_input_tokens or 0
        tokens["output"] += usage.output_tokens
        if response.stop_reason != "tool_use":
            break
        messages.append({"role": "assistant", "content": response.content})
        results = []
        for block in response.content:
            if block.type == "tool_use":
                tool_calls += 1
                output = run_tool(block.name, block.input) or ""
                seen += output
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": output})
        messages.append({"role": "user", "content": results})
    answer = "".join(block.text for block in response.content if block.type == "text")
    cost = sum(tokens[kind] * PRICE[kind] for kind in tokens) / 1_000_000
    return {"seconds": round(time.time() - start, 1), "tokens": tokens, "cost": round(cost, 4),
            "tool_calls": tool_calls, "answer": answer, "seen": seen}


# ---------- Run every question in both modes ----------

sys.stdout.reconfigure(encoding="utf-8")
QUESTIONS = Path(__file__).resolve().parent.parent / "bench" / "questions.jsonl"
questions = [json.loads(line) for line in QUESTIONS.read_text(encoding="utf-8").splitlines() if line.strip()]
questions = questions[:int(sys.argv[1])] if len(sys.argv) > 1 else questions
core.agent()  # load Laya first so its start-up time isn't counted

results = []
for item in questions:
    row = {"question": item["question"]}
    for mode, tools, run_tool in (("folder", FOLDER_TOOLS, run_folder_tool), ("metis", METIS_TOOLS, run_metis_tool)):
        run = ask(item["question"], tools, run_tool)
        # "Found" = the expected text reached Claude through a tool (questions with no answer aren't graded).
        run["found"] = (item.get("part") or "").lower() in run.pop("seen").lower() if item["file"] else None
        row[mode] = run
        total = sum(run["tokens"].values())
        print(f"{mode:6} {run['seconds']:5.1f}s {total:7,} tokens ${run['cost']:.4f} {run['tool_calls']} calls "
              f"found={run['found']}  {item['question'][:50]}", flush=True)
    results.append(row)

Path(__file__).with_name("compare_results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")

print("\n         median time   median tokens   total cost   found")
for mode in ("folder", "metis"):
    runs = [row[mode] for row in results]
    graded = [run["found"] for run in runs if run["found"] is not None]
    print(f"{mode:6}   {statistics.median(run['seconds'] for run in runs):8.1f}s"
          f"   {statistics.median(sum(run['tokens'].values()) for run in runs):13,.0f}"
          f"   ${sum(run['cost'] for run in runs):9.2f}   {sum(graded)}/{len(graded)}")
print("Answers are in compare_results.json.")
