#!/usr/bin/env python3
"""Turn structured procedure guides into narrated training video.

The engine is company-agnostic. Palette, trade vocabulary, narrator and the
guides themselves come from a project directory (see project.py), selected with
--project or the TD_PROJECT environment variable.

    produce.py --project <dir> list
    produce.py --project <dir> voicetest
    produce.py --project <dir> <guide>
    produce.py --project <dir> <guide> --section s09c
    produce.py --project <dir> <guide> --dry-run

Point TD_API at whichever install is running the GPU services; everything else
has a working default. See config.py.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import context  # noqa: E402
import project as project_mod  # noqa: E402
from config import API, FOUNDRY, OUT_ROOT  # noqa: E402

# Exercises the engine's own machinery — fractions, decimals, part callouts,
# ratios, code citations. A project should define VOICE_TEST_LINES in its
# project.py to exercise its trade's vocabulary instead.
DEFAULT_VOICE_TEST_LINES = [
    "Fasten every 12 to 16 inches on center along the flange.",
    "Each fastener must penetrate a minimum of 3/4 inch, per section R905.10.4.",
    "Any slope over 4:12 requires additional support.",
    "Use #10 x 1.5\" fasteners throughout.",
    "The panel is 15/32-inch thick and the backing is 7/16-inch.",
    "Maintain a 0.030-inch gap and torque to 100-pound force.",
    "Use the 3-4-5 method to keep the first course square.",
    "Overlap horizontal seams by 3.5 inches and vertical seams by 6 inches.",
]


def guides_dir() -> Path:
    return context.current().root / "guides"


def available_guides() -> list[str]:
    d = guides_dir()
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.py") if not p.stem.startswith("_"))


def load_guide(name: str):
    if name not in available_guides():
        sys.exit(f"unknown guide '{name}'; available: "
                 f"{', '.join(available_guides()) or '(none)'}")
    module = importlib.import_module(f"guides.{name}")
    script = getattr(module, "SCRIPT", None)
    if script is None:
        sys.exit(f"guides/{name}.py must define SCRIPT = TrainingScript(...)")
    return script


def voice_test_lines() -> list[str]:
    module_path = context.current().root / "project.py"
    if module_path.is_file():
        spec = importlib.util.spec_from_file_location("td_project_lines",
                                                      module_path)
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            lines = getattr(mod, "VOICE_TEST_LINES", None)
            if lines:
                return list(lines)
    return DEFAULT_VOICE_TEST_LINES


def cmd_voicetest() -> None:
    from narration import check_line, ensure_narrator_ready, spoken, _take, \
        voice_reference

    ensure_narrator_ready()
    proj = context.current()
    out = OUT_ROOT / "voicetest"
    out.mkdir(parents=True, exist_ok=True)
    lines = voice_test_lines()
    print(f"project : {proj.name}")
    print(f"clip    : {voice_reference()}")
    print(f"delivery: {proj.voice_emotion}")
    print(f"foundry : {FOUNDRY}\n")

    failures = 0
    for i, line in enumerate(lines):
        wav = out / f"line_{i:02d}.wav"
        print(f"[{i + 1}/{len(lines)}] {line}")
        print(f"  spoken as: {spoken(line)}")
        _take(spoken(line), wav, verify=False)
        ok, heard = check_line(line, wav)
        print(f"  heard    : {heard}")
        print(f"  {'PASS' if ok else 'FAIL'}\n")
        failures += not ok

    print(f"{len(lines) - failures}/{len(lines)} lines passed the read-check")
    print(f"listen: {out}")
    if failures:
        print("\nFailures are usually respellings, not clone quality — add the "
              "term to the project's TERMS table and re-run.")


def cmd_list() -> None:
    proj = context.current()
    names = available_guides()
    if not names:
        print(f"{proj.name}: no guides in {guides_dir()}")
        return
    for name in names:
        script = load_guide(name)
        print(f"\n{name}: {script.title}  ({len(script.shots)} sections)")
        for shot in script.shots:
            print(f"  {shot.key:<8} {shot.title}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", help="voicetest | list | a guide name")
    ap.add_argument("--project", help="project directory (else TD_PROJECT)")
    ap.add_argument("--section", action="append", dest="sections",
                    help="produce only this section key (repeatable)")
    ap.add_argument("--dry-run", action="store_true",
                    help="resolve the script and print the plan only")
    ap.add_argument("--no-verify", action="store_true",
                    help="skip the whisper read-check (faster, riskier)")
    args = ap.parse_args()

    context.use(project_mod.load(args.project))

    if args.command == "voicetest":
        return cmd_voicetest()
    if args.command == "list":
        return cmd_list()

    script = load_guide(args.command)
    shots = [s for s in script.shots
             if not args.sections or s.key in args.sections]
    if not shots:
        sys.exit(f"no sections matched {args.sections}")

    if args.dry_run:
        print(f"{context.current().name} — {script.title}\n{script.subtitle}")
        print(f"api={API}  foundry={FOUNDRY}  out={OUT_ROOT}\n")
        words = 0
        for shot in shots:
            body = " ".join(shot.narration)
            words += len(body.split())
            print(f"{shot.key}  {shot.section}")
            print(f"  title : {shot.title}")
            print(f"  specs : {len(shot.specs)}  citation: {shot.citation or '-'}")
            print(f"  b-roll: {shot.prompt}")
            print(f"  words : {len(body.split())}\n")
        print(f"{len(shots)} section(s), {words} words "
              f"(~{words / 150:.1f} min at 150 wpm)")
        return

    from assemble import produce
    produce(script, only=args.sections, verify_voice=not args.no_verify)


if __name__ == "__main__":
    main()
