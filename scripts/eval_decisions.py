#!/usr/bin/env python3
"""Measure declared decisions on their labelled sets with one named model, the way decide() asks.

Every scored item of backend/tests/fixtures/decisions/<decision>.json is put to
the model through nl_control_plane.ask_question, the function decide() uses:
the measured prompt, temperature 0, and the context length the model is loaded
with, so the model is not reloaded. A row measured here therefore describes
what decide() would get from that build.

For each decision it prints correct/n, the lower bound against the floor (the
strict rule decide() qualifies by, decision_evidence.lcb), injection-item
misses, the misses themselves and the median time per question. The last
block, with --rows, is the MEASURED_ACCURACY rows to paste into
backend/services/decision_data.py. A row holds only for the build it names by
digest, so it is recorded from the loaded model's /api/ps entry, never from
the name.

    backend/venv/bin/python scripts/eval_decisions.py --model gemma4:12b --decision needs_live_data
    backend/venv/bin/python scripts/eval_decisions.py --model gemma4:12b --rows
    backend/venv/bin/python scripts/eval_decisions.py --model gemma4:12b --load --record --rows

It refuses to start, exit 2, while GPU work is running (the backend's job gate,
the exclusive GPU lock or the ComfyUI queue) and when the model is not loaded
on the GPU. --load loads it first, when the GPU is idle, and unloads it again
at the end; nothing is ever downloaded. It exits 1 when a measured decision is
below its floor at the lower bound, 0 when every one clears it.

--record writes each reply as received, with what the parser made of it, to
backend/tests/fixtures/decisions/recorded/<decision>.json for
test_decide_recorded.py; replies are cut to RAW_LIMIT characters and anything
that looks like a path, an email address or an IP address is replaced. A JSON
log of the run goes to logs/decision_evals/ unless --out names a file.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_OUT_DIR = ROOT / "logs" / "decision_evals"
RECORDED_DIR = ROOT / "backend" / "tests" / "fixtures" / "decisions" / "recorded"
# Kept out of recorded replies. Labelled items are synthetic, so these should
# never match; the check is for a model that adds something of its own.
SCRUB = (
    (re.compile(r"(?:/home/|/Users/|[A-Za-z]:\\)\S*"), "<path>"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"), "<email>"),
    (re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])"), "<ip>"),
)
RAW_LIMIT = 2000
LOAD_KEEP_ALIVE = "10m"


def gpu_is_busy() -> str | None:
    """What is using the GPU in plain words, or None when nothing is.

    The job gate lives in the running backend's process, so it is asked over
    HTTP; no backend answering means nothing of Guaardvark's is rendering. The
    exclusive GPU lock is a file every process shares; ComfyUI is asked for
    its queue.
    """
    import requests

    from backend.utils.backend_http import BackendError, request_json

    try:
        snap = request_json("GET", "/api/jobs/gate", read_timeout=5).data or {}
    except BackendError as e:
        if e.kind != "unreachable":
            return f"an unknown job (the backend's job gate did not answer: {e})"
        snap = {}
    if snap.get("gpu_busy"):
        holder = snap.get("gpu_holder") or {}
        return f"{holder.get('kind', 'a job')} ({holder.get('native_id', 'unknown id')})"
    from backend.services.gpu_resource_coordinator import get_gpu_coordinator

    if get_gpu_coordinator().lock_held():
        return "the holder of the exclusive GPU lock"
    try:
        from backend.config import COMFYUI_URL

        queue = requests.get(f"{COMFYUI_URL}/queue", timeout=3).json()
        if queue.get("queue_running") or queue.get("queue_pending"):
            return "a ComfyUI render"
    except Exception:  # noqa: BLE001 - ComfyUI not running
        pass
    return None


def _ollama_generate(body: dict, timeout: float) -> None:
    import requests

    from backend.utils.ollama_resource_manager import get_ollama_base_url

    response = requests.post(f"{get_ollama_base_url()}/api/generate", json={**body, "stream": False}, timeout=timeout)
    response.raise_for_status()


def load_model(model: str) -> None:
    """Load ``model`` with the context size the product sizes for it (no download)."""
    from backend.utils.ollama_resource_manager import clear_cache, request_options

    _ollama_generate({"model": model, "prompt": "", "keep_alive": LOAD_KEEP_ALIVE,
                      "options": request_options(model)}, timeout=300)
    clear_cache()


def unload_model(model: str) -> None:
    _ollama_generate({"model": model, "keep_alive": 0}, timeout=60)


def scrub(text: str) -> str:
    text = (text or "")[:RAW_LIMIT]
    for pattern, replacement in SCRUB:
        text = pattern.sub(replacement, text)
    return text


def measure(decision_id: str, model: str, num_ctx: int | None, repeat: int, timeout_s: float) -> dict:
    """Ask every scored item of the decision's set ``repeat`` times; an item
    counts as correct only when every ask was right."""
    from backend.services.decision_data import DECISIONS
    from backend.services.decision_prompt import ordered_state, wording_sha
    from backend.services.nl_control_plane import ask_question
    from backend.tests.fixtures.decision_sets import load_set, scored_items

    entry = DECISIONS[decision_id]
    labelled = load_set(entry["set"])
    correct, misses, injection_misses, unstable, times, replies = 0, [], 0, [], [], []
    items = scored_items(labelled)
    for item in items:
        state = ordered_state(entry, item["state"])
        answers = []
        for _ in range(repeat):
            started = time.perf_counter()
            asked = ask_question(entry, state, model=model, timeout_s=timeout_s, num_ctx=num_ctx)
            times.append(time.perf_counter() - started)
            answers.append(asked)
            replies.append({"id": item["id"], "raw": scrub(asked.raw), "reason": asked.reason,
                            "answer": asked.answer if asked.reason == "ok" else None, "want": item["want"]})
        right = all(a.reason == "ok" and a.answer == item["want"] for a in answers)
        if len({(a.reason, json.dumps(a.answer)) for a in answers}) > 1:
            unstable.append(item["id"])
        if right:
            correct += 1
            continue
        if "injection" in item["tags"]:
            injection_misses += 1
        misses.append({"id": item["id"], "want": item["want"], "tags": item["tags"],
                       "got": [a.answer if a.reason == "ok" else a.reason for a in answers]})
    times.sort()
    return {
        "decision": decision_id, "correct": correct, "n": len(items), "injection_misses": injection_misses,
        "misses": misses, "unstable": unstable, "repeat": repeat,
        "median_s": round(statistics.median(times), 3) if times else None,
        "wording_sha": wording_sha(entry), "set_version": labelled["set_version"], "replies": replies,
    }


def measured_row(result: dict, model: str, digest: str) -> dict:
    return {"backend": "chat", "model": model, "digest": digest, "wording_sha": result["wording_sha"],
            "set_version": result["set_version"], "correct": result["correct"], "n": result["n"],
            "injection_misses": result["injection_misses"], "date": dt.date.today().isoformat()}


def verdict(result: dict) -> tuple[bool, str]:
    from backend.services.decision_data import DECISIONS
    from backend.services.decision_evidence import lcb, needed_to_qualify

    entry = DECISIONS[result["decision"]]
    bound = lcb(result["correct"], result["n"])
    clears = bound >= entry["floor"] and (entry["type"] != "gate" or result["injection_misses"] == 0)
    needed = needed_to_qualify(result["correct"], result["n"], entry["floor"])
    words = (f"95% lower bound {bound:.3f}, floor {entry['floor']}: "
             + ("clears it" if clears else f"below; {needed} more correct items needed"
                if needed is not None else "below"))
    if entry["type"] == "gate" and result["injection_misses"]:
        words += f"; a gate with {result['injection_misses']} injection misses never qualifies"
    return clears, words


def record(result: dict, model: str, digest: str) -> Path:
    """Write the replies for one decision, keeping runs recorded from other builds."""
    path = RECORDED_DIR / f"{result['decision']}.json"
    existing = {}
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
    if existing.get("wording_sha") != result["wording_sha"] or existing.get("set_version") != result["set_version"]:
        existing = {}
    runs = [run for run in existing.get("runs", []) if run.get("digest") != digest]
    runs.append({"model": model, "digest": digest, "recorded": dt.date.today().isoformat(),
                 "replies": result["replies"]})
    payload = {
        "decision": result["decision"], "wording_sha": result["wording_sha"], "set_version": result["set_version"],
        "provenance": "replies of the named builds to the labelled set, as received; synthetic items",
        "runs": sorted(runs, key=lambda run: run["digest"]),
    }
    RECORDED_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--model", required=True, help="the Ollama model to measure, by the name a chat would use")
    parser.add_argument("--decision", action="append", default=[],
                        help="a decision id (repeatable, or comma-separated); default: every one with a labelled set")
    parser.add_argument("--backend", choices=("chat",), default="chat",
                        help="what answers: the chat model through json_chat")
    parser.add_argument("--repeat", type=int, default=1, help="ask each item this many times; all must be right")
    parser.add_argument("--timeout", type=float, default=30.0, help="seconds to wait for each reply")
    parser.add_argument("--load", action="store_true",
                        help="load the model first if it is not loaded (GPU idle only), and unload it at the end")
    parser.add_argument("--record", action="store_true", help="write the replies as test fixtures")
    parser.add_argument("--rows", action="store_true", help="print MEASURED_ACCURACY rows to paste")
    parser.add_argument("--out", type=Path, help="JSON log file (default: logs/decision_evals/<time>.json)")
    args = parser.parse_args(argv)

    from backend.services.decision_data import DECISIONS
    from backend.utils.ollama_resource_manager import _short_digest, resident_entry

    wanted = [d.strip() for value in args.decision for d in value.split(",") if d.strip()]
    decisions = wanted or sorted(k for k, v in DECISIONS.items() if v.get("set"))
    unknown = [d for d in decisions if d not in DECISIONS or not DECISIONS[d].get("set")]
    if unknown:
        print(f"No labelled set for: {', '.join(unknown)}", file=sys.stderr)
        return 2
    if args.repeat < 1:
        print("--repeat must be at least 1", file=sys.stderr)
        return 2

    busy = gpu_is_busy()
    if busy:
        print(f"The GPU is in use by {busy}; not measuring.", file=sys.stderr)
        return 2
    loaded_here = False
    entry = resident_entry(args.model)
    if entry is None and args.load:
        print(f"Loading {args.model} ...", file=sys.stderr)
        load_model(args.model)
        loaded_here = True
        entry = resident_entry(args.model)
    if entry is None:
        print(f"{args.model} is not loaded on the GPU; load it (or pass --load) and run again.", file=sys.stderr)
        if loaded_here:
            unload_model(args.model)
        return 2
    digest = _short_digest(entry.get("digest"))
    num_ctx = entry.get("context_length") if isinstance(entry.get("context_length"), int) else None
    print(f"{args.model}: build {digest}, context {num_ctx}, {len(decisions)} decision(s)", file=sys.stderr)

    results, below = [], []
    try:
        for decision_id in decisions:
            result = measure(decision_id, args.model, num_ctx, args.repeat, args.timeout)
            clears, words = verdict(result)
            result["clears_floor"] = clears
            results.append(result)
            if not clears:
                below.append(decision_id)
            print(f"{decision_id}: {result['correct']}/{result['n']}, {words}; median {result['median_s']} s")
            for miss in result["misses"]:
                print(f"    miss {miss['id']} {miss['tags']}: want {miss['want']!r}, got {miss['got']!r}")
            if result["unstable"]:
                print(f"    answers changed between asks: {', '.join(result['unstable'])}")
            if args.record:
                print(f"    recorded {record(result, args.model, digest).relative_to(ROOT)}")
    finally:
        if loaded_here:
            unload_model(args.model)

    if args.rows:
        print("\n# MEASURED_ACCURACY rows (backend/services/decision_data.py)")
        for result in results:
            print(f'    "{result["decision"]}": [{json.dumps(measured_row(result, args.model, digest))}],')

    out = args.out or DEFAULT_OUT_DIR / f"eval-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "model": args.model, "digest": digest, "num_ctx": num_ctx, "timeout_s": args.timeout,
        "rows": [measured_row(r, args.model, digest) for r in results],
        "results": [{k: v for k, v in r.items() if k != "replies"} for r in results],
    }, indent=1) + "\n", encoding="utf-8")
    print(f"log: {out}", file=sys.stderr)
    return 1 if below else 0


if __name__ == "__main__":
    raise SystemExit(main())
