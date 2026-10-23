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
photographs is not evidence about these. It reads the library through a bare
database session and does not build the Guaardvark app, so it syncs no schema,
seeds nothing and starts no services on the box it measures. Nothing is written:
no images, no sidecars, no uploads, no rows edited for you. The last block it
prints is the row to paste into IDENTITY_METHODS["<method>"]["measured"].

For each method it prints the score distribution of the same and the different
pairs (min, median, max) and the cut that separates them best, which is where a
pass mark comes from; ``--threshold`` then scores a candidate pass mark on pairs
drawn with another ``--seed`` before it is pasted in. ``hist`` (the colour match
clips were compared with before the encoder) can be scored on the same pairs as
a baseline. It has no pass mark of its own and is reported at its best cut, a
flattering number for the baseline, which is the fair way round.

"vlm" loads a vision model, so it refuses to start while the running backend
reports a GPU job; "embed" and "hist" run on the CPU and do not ask.

    backend/venv/bin/python scripts/eval_identity_methods.py
    backend/venv/bin/python scripts/eval_identity_methods.py --methods embed,hist
    backend/venv/bin/python scripts/eval_identity_methods.py --methods embed --threshold 0.5 --seed 1
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
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Scored on the pairs for comparison only: never a row, never a shown score.
BASELINES = {"hist": "colour match (RGB histogram cosine)"}


def gpu_is_busy() -> str | None:
    """The holder of the GPU in plain words, or None when the card is free.

    Asks the running backend: the job gate lives in its process, so a gate
    built in this script would always read free. No backend answering means
    nothing of Guaardvark's is rendering.
    """
    from backend.utils.backend_http import BackendError, request_json
    try:
        snap = request_json("GET", "/api/jobs/gate", read_timeout=5).data or {}
    except BackendError as e:
        if e.kind == "unreachable":
            return None
        return f"an unknown job (the backend's job gate did not answer: {e})"
    if not snap.get("gpu_busy"):
        return None
    holder = snap.get("gpu_holder") or {}
    return f"{holder.get('kind', 'a job')} ({holder.get('native_id', 'unknown id')})"


def library_app():
    """A Flask app with only the database and storage root the library reads."""
    from flask import Flask
    from backend import config
    from backend.models import db

    app = Flask("eval_identity_methods")
    app.config.update({
        "SQLALCHEMY_DATABASE_URI": config.DATABASE_URL,
        "SQLALCHEMY_TRACK_MODIFICATIONS": False,
        "STORAGE_DIR": config.STORAGE_DIR,
    })
    db.init_app(app)
    return app


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


def _raw_score(method: str, result: dict):
    """The method's number for one pair, before any evidence gate."""
    details = result.get("details") or {}
    if method in BASELINES:
        return None if details.get("error") or "mean_cosine" not in details else result.get("score")
    return details.get("raw_score")


def distribution(values: list[float]) -> dict | None:
    if not values:
        return None
    return {"min": round(min(values), 4), "median": round(statistics.median(values), 4),
            "max": round(max(values), 4), "n": len(values)}


def best_cut(scores: list[dict]) -> dict:
    """The pass mark that gets the most pairs right on these scores.

    Candidates are the midpoints between neighbouring scores. When the two
    classes do not overlap, the midpoint of the gap between the lowest same pair
    and the highest different pair is returned, with the gap's width.
    """
    same = sorted(s["raw_score"] for s in scores if s["same"])
    diff = sorted(s["raw_score"] for s in scores if not s["same"])
    if same and diff and min(same) > max(diff):
        return {"threshold": round((min(same) + max(diff)) / 2, 4),
                "correct": len(scores), "n": len(scores),
                "gap": round(min(same) - max(diff), 4)}
    values = sorted({s["raw_score"] for s in scores})
    candidates = [(a + b) / 2 for a, b in zip(values, values[1:])] or values
    best = (0, 0.0)
    for cut in candidates:
        right = sum(int((s["raw_score"] >= cut) == s["same"]) for s in scores)
        if right > best[0]:
            best = (right, cut)
    return {"threshold": round(best[1], 4), "correct": best[0], "n": len(scores), "gap": None}


def run_method(method: str, pairs: list[dict], threshold: float | None = None) -> dict:
    """Score every pair with one method and count the calls it got right.

    A pair the method could not answer (encoder missing, vision error, reply that
    is not yes/no) is not counted as wrong — it stops the run, because an accuracy
    over the pairs that happened to work is not the accuracy of the method.
    ``threshold`` replaces the method's declared pass mark for this run.
    """
    from backend.services.identity_method_data import IDENTITY_METHODS
    from backend.services.video_consistency_metrics import score_identity_preservation

    declared = (IDENTITY_METHODS.get(method) or {}).get("threshold")
    if threshold is None:
        threshold = declared
    model = None
    scores: list[dict] = []
    for pair in pairs:
        result = score_identity_preservation([pair["ref"]], pair["candidate"], method=method)
        model = result.get("model") or model
        raw = _raw_score(method, result)
        if raw is None:
            return {"method": method, "ok": False, "model": model,
                    "reason": result.get("reason") or (result.get("details") or {}).get("error")
                    or "no score", "n": len(scores)}
        scores.append({"same": pair["same"], "raw_score": float(raw),
                       "subjects": list(pair["subjects"]),
                       "ref": pair["ref"], "candidate": pair["candidate"]})
    cut = best_cut(scores)
    if threshold is None:  # a baseline: judged at its own best cut
        threshold = cut["threshold"]
    for s in scores:
        s["called_same"] = s["raw_score"] >= float(threshold)
    correct = sum(int(s["called_same"] == s["same"]) for s in scores)
    return {"method": method, "ok": True, "model": model, "threshold": float(threshold),
            "baseline": method in BASELINES, "correct": correct, "n": len(scores),
            "same": distribution([s["raw_score"] for s in scores if s["same"]]),
            "different": distribution([s["raw_score"] for s in scores if not s["same"]]),
            "best_cut": cut, "scores": scores}


def _digest(method: str, model: str | None) -> str:
    """The build fingerprint a row carries: the verified weights' sha256 prefix
    for ``embed``; for ``vlm`` the Ollama manifest digest is left to fill in."""
    if method == "embed" and model:
        from backend.services import identity_encoder
        from backend.services.identity_method_data import IMAGE_ENCODERS
        if identity_encoder.installed_path(model) is not None:
            return IMAGE_ENCODERS[model]["weights_sha256"][:12]
    return "<digest prefix>"


def measured_row(result: dict) -> dict:
    """The row to paste into IDENTITY_METHODS: what was measured, with what, at
    which pass mark, and how often it was right."""
    method = result.get("method") or ""
    model = result.get("model")
    row = {
        "model": model or "<model>",
        "digest": _digest(method, model),
        "threshold": result.get("threshold"),
        "correct": result["correct"],
        "n": result["n"],
        "date": dt.date.today().isoformat(),
    }
    if method == "embed" and model:
        from backend.services.identity_method_data import IMAGE_ENCODERS
        revision = (IMAGE_ENCODERS.get(model) or {}).get("revision")
        if revision:
            row["revision"] = revision
    return row


def report(results: list[dict], as_json: bool) -> None:
    from backend.services.identity_method_data import (
        DEFAULT_IDENTITY_FLOOR,
        IDENTITY_METHODS,
        accuracy_lower_bound,
    )

    rows = {r["method"]: measured_row(r) for r in results if r.get("ok") and not r.get("baseline")}
    if as_json:
        print(json.dumps({"results": results, "rows": rows}, indent=2))
        return

    for result in results:
        method = result["method"]
        if not result.get("ok"):
            print(f"{method}: not measured — {result['reason']} "
                  f"(after {result['n']} pairs)")
            continue
        same, diff, cut = result["same"], result["different"], result["best_cut"]
        print(f"{method}: same {same}  different {diff}")
        gap = f", gap {cut['gap']:.3f}" if cut.get("gap") is not None else ""
        print(f"    best cut {cut['threshold']:.3f}: {cut['correct']}/{cut['n']}{gap}")
        accuracy = result["correct"] / max(1, result["n"])
        if result.get("baseline"):
            print(f"    {BASELINES[method]}, a baseline: {result['correct']}/{result['n']} "
                  f"= {accuracy:.3f} at its best cut")
            continue
        floor = (IDENTITY_METHODS.get(method) or {}).get("floor", DEFAULT_IDENTITY_FLOOR)
        bound = accuracy_lower_bound(result["correct"], result["n"])
        verdict = "clears" if bound >= floor else "below"
        print(f"    {result['correct']}/{result['n']} = {accuracy:.3f} at threshold "
              f"{result['threshold']:.3f}, 95% lower bound {bound:.3f} "
              f"{verdict} the {floor} floor  (model {result.get('model') or 'unknown'})")

    if not rows:
        return
    print("\nPaste into backend/services/identity_method_data.IDENTITY_METHODS"
          " (a vlm digest is the Ollama manifest digest prefix):")
    for method, row in rows.items():
        print(f'    "{method}": ... "measured": [{json.dumps(row)}],')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--methods", default="embed,vlm",
                        help="comma-separated subset of the qualified methods, plus "
                             "the baseline 'hist'")
    parser.add_argument("--pairs", type=int, default=60,
                        help="labelled pairs to use, split evenly positive/negative")
    parser.add_argument("--kind", choices=("character", "environment", "prop"),
                        help="restrict to one Subject kind (default: all)")
    parser.add_argument("--seed", type=int, default=0, help="pair sampling seed")
    parser.add_argument("--threshold", type=float,
                        help="score at this pass mark instead of the declared one")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    methods = [m.strip() for m in args.methods.split(",") if m.strip()]
    if "vlm" in methods:
        busy = gpu_is_busy()
        if busy:
            print(f"The GPU is held by {busy} — not starting. "
                  "This loads a vision model and would fight it for VRAM.", file=sys.stderr)
            return 2

    app = library_app()
    with app.app_context():
        subjects = subject_images(args.kind)
        pairs = build_pairs(subjects, args.pairs, random.Random(args.seed))
        if len(pairs) < 2:
            print("Not enough Cast images to make labelled pairs: this needs at least "
                  "two subjects with two images each.", file=sys.stderr)
            return 1
        positives = sum(1 for p in pairs if p["same"])
        usable = [s for s in subjects if len(s[2]) >= 2]
        # On stderr under --json, so stdout stays one JSON document.
        print(f"{len(pairs)} pairs from {len(usable)} of {len(subjects)} subjects "
              f"({positives} same, {len(pairs) - positives} different; images per subject: "
              f"{', '.join(str(len(s[2])) for s in usable)})",
              file=sys.stderr if args.json else sys.stdout)
        results = [
            run_method(m, pairs, None if m in BASELINES else args.threshold)
            for m in methods
        ]
        report(results, args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
