#!/usr/bin/env python3
"""Measure the Cast identity methods on labelled pairs from this machine's Cast library.

The methods in backend/services/video_consistency_metrics ship withheld: their
score is not shown until identity_method_data.IDENTITY_METHODS carries a measured
row for the model used whose 95% lower confidence bound clears the floor. This is
how that row is produced.

Pairs are built from the Cast library already on this machine. Two images of the
same Subject (reference photos and done samples, pooled) are a positive; two
images of different Subjects of the same kind are a negative. Negatives stay
within a kind so the methods are judged on telling two characters apart, not on
telling a character from a warehouse. Subjects with fewer than two images cannot
make a positive and are skipped.

Run it on the machine whose library it reads — a row measured on someone else's
photographs is not evidence about these. Nothing is written: no images, no
sidecars, no uploads, no rows edited for you. The last block it prints is the
row to paste into IDENTITY_METHODS["<method>"]["measured"].

Refuses to start while a GPU job holds the card (JobOperationGate), because "vlm"
loads a vision model and would fight a render or a training run for VRAM.

    backend/venv/bin/python scripts/eval_identity_methods.py
    backend/venv/bin/python scripts/eval_identity_methods.py --methods embed
    backend/venv/bin/python scripts/eval_identity_methods.py --pairs 80 --kind character
    backend/venv/bin/python scripts/eval_identity_methods.py --json

A method that is not installed or not reachable prints the reason and no row;
that is an answer too, and it is not a failure of this script.
"""
from __future__ import annotations

import argparse
import datetime as dt
import itertools
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def gpu_is_busy() -> str | None:
    """The holder of the GPU in plain words, or None when the card is free."""
    from backend.services.job_operation_gate import get_gate
    snap = get_gate().snapshot()
    if not snap.get("gpu_busy"):
        return None
    holder = snap.get("gpu_holder") or {}
    return f"{holder.get('kind', 'a job')} ({holder.get('native_id', 'unknown id')})"


def subject_images(kind: str | None) -> list[tuple[int, str, list[str]]]:
    """(subject_id, name, image paths that exist) per Subject, as the library has it.

    Reads the same two sources the Cast page shows: Subject.ref_image_paths (the
    training data) and the done SubjectSample rows (the generated sheet), resolved
    through cast_library_api's containment guard so a stored relative path lands
    the same way it does when the API serves it.
    """
    from backend.api.cast_library_api import _resolve_ref_path
    from backend.models import Subject, SubjectSample

    rows: list[tuple[int, str, list[str]]] = []
    query = Subject.query
    if kind:
        query = query.filter(Subject.kind == kind)
    for subject in query.order_by(Subject.id).all():
        paths: list[str] = []
        for stored in list(subject.ref_image_paths or []):
            resolved = _resolve_ref_path(stored)
            if resolved and resolved not in paths:
                paths.append(resolved)
        samples = (
            SubjectSample.query
            .filter_by(subject_id=subject.id, status="done")
            .order_by(SubjectSample.index)
            .all()
        )
        for sample in samples:
            resolved = _resolve_ref_path(sample.image_path) if sample.image_path else None
            if resolved and resolved not in paths:
                paths.append(resolved)
        rows.append((subject.id, subject.name, paths))
    return rows


def build_pairs(subjects, limit: int, rng: random.Random) -> list[dict]:
    """Labelled pairs, half positive and half negative, balanced as far as the
    library allows. Each pair is {ref, candidate, same, subjects}."""
    usable = [(sid, name, paths) for sid, name, paths in subjects if len(paths) >= 2]
    positives: list[dict] = []
    for sid, name, paths in usable:
        for left, right in itertools.combinations(paths, 2):
            positives.append({"ref": left, "candidate": right, "same": True,
                              "subjects": (sid, sid), "names": (name, name)})
    negatives: list[dict] = []
    for (a_id, a_name, a_paths), (b_id, b_name, b_paths) in itertools.combinations(usable, 2):
        for left in a_paths:
            for right in b_paths:
                negatives.append({"ref": left, "candidate": right, "same": False,
                                  "subjects": (a_id, b_id), "names": (a_name, b_name)})
    rng.shuffle(positives)
    rng.shuffle(negatives)
    half = max(1, limit // 2)
    pairs = positives[:half] + negatives[:half]
    rng.shuffle(pairs)
    return pairs


def run_method(method: str, pairs: list[dict]) -> dict:
    """Score every pair with one method and count the calls it got right.

    A pair the method could not answer (encoder missing, vision error, reply that
    is not yes/no) is not counted as wrong — it stops the run, because an accuracy
    over the pairs that happened to work is not the accuracy of the method.
    """
    from backend.services.identity_method_data import IDENTITY_METHODS
    from backend.services.video_consistency_metrics import score_identity_preservation

    threshold = float((IDENTITY_METHODS.get(method) or {}).get("threshold", 0.5))
    correct = 0
    model = None
    scores: list[dict] = []
    for pair in pairs:
        result = score_identity_preservation([pair["ref"]], pair["candidate"], method=method)
        model = result.get("model") or model
        raw = (result.get("details") or {}).get("raw_score")
        if raw is None:
            return {"method": method, "ok": False, "model": model,
                    "reason": result.get("reason") or "no score", "n": len(scores)}
        called_same = float(raw) >= threshold
        correct += int(called_same == pair["same"])
        scores.append({"same": pair["same"], "raw_score": float(raw),
                       "called_same": called_same, "subjects": list(pair["subjects"])})
    return {"method": method, "ok": True, "model": model, "threshold": threshold,
            "correct": correct, "n": len(scores), "scores": scores}


def measured_row(result: dict) -> dict:
    """The row to paste into IDENTITY_METHODS, with ``digest`` left for the person
    to fill from the build they measured (Ollama manifest digest, or the weights'
    sha256 prefix)."""
    return {
        "model": result.get("model") or "<model>",
        "digest": "<digest prefix>",
        "correct": result["correct"],
        "n": result["n"],
        "date": dt.date.today().isoformat(),
    }


def report(results: list[dict], as_json: bool) -> None:
    from backend.services.identity_method_data import (
        DEFAULT_IDENTITY_FLOOR,
        IDENTITY_METHODS,
        accuracy_lower_bound,
    )

    if as_json:
        print(json.dumps({"results": results, "rows": {
            r["method"]: measured_row(r) for r in results if r.get("ok")
        }}, indent=2))
        return

    for result in results:
        method = result["method"]
        if not result.get("ok"):
            print(f"{method}: not measured — {result['reason']} "
                  f"(after {result['n']} pairs)")
            continue
        floor = (IDENTITY_METHODS.get(method) or {}).get("floor", DEFAULT_IDENTITY_FLOOR)
        bound = accuracy_lower_bound(result["correct"], result["n"])
        verdict = "clears" if bound >= floor else "below"
        print(f"{method}: {result['correct']}/{result['n']} "
              f"= {result['correct'] / max(1, result['n']):.3f} at threshold "
              f"{result['threshold']:.2f}, 95% lower bound {bound:.3f} "
              f"{verdict} the {floor} floor  (model {result.get('model') or 'unknown'})")

    rows = {r["method"]: measured_row(r) for r in results if r.get("ok")}
    if not rows:
        return
    print("\nPaste into backend/services/identity_method_data.IDENTITY_METHODS, "
          "filling in each digest:")
    for method, row in rows.items():
        print(f'    "{method}": ... "measured": [{json.dumps(row)}],')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--methods", default="embed,vlm",
                        help="comma-separated subset of the qualified methods")
    parser.add_argument("--pairs", type=int, default=60,
                        help="labelled pairs to use, split evenly positive/negative")
    parser.add_argument("--kind", choices=("character", "environment", "prop"),
                        help="restrict to one Subject kind (default: all)")
    parser.add_argument("--seed", type=int, default=0, help="pair sampling seed")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    busy = gpu_is_busy()
    if busy:
        print(f"The GPU is held by {busy} — not starting. "
              "This loads a vision model and would fight it for VRAM.", file=sys.stderr)
        return 2

    from backend.app import create_app
    app = create_app()
    with app.app_context():
        subjects = subject_images(args.kind)
        pairs = build_pairs(subjects, args.pairs, random.Random(args.seed))
        if len(pairs) < 2:
            print("Not enough Cast images to make labelled pairs: this needs at least "
                  "two subjects with two images each.", file=sys.stderr)
            return 1
        positives = sum(1 for p in pairs if p["same"])
        print(f"{len(pairs)} pairs from {len(subjects)} subjects "
              f"({positives} same, {len(pairs) - positives} different)")
        results = [run_method(m.strip(), pairs) for m in args.methods.split(",") if m.strip()]
        report(results, args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
