"""Build-time smoke test for the RunPod LoRA trainer pod image.

Runs during `docker build` (see Dockerfile `RUN python smoke_test.py`). It
verifies the image wiring WITHOUT training: handler imports, the bundled runner
scripts, and the key functions exist and are callable. If any check fails, the
build fails — so a pushed image is known-good before it ever reaches RunPod.

This deliberately does NOT load a model or train (no GPU / no multi-GB download
at build time). Full training is verified later by submitting a real job from
the Guaardvark web UI.
"""
from __future__ import annotations

import sys
from pathlib import Path

FAILURES: list[str] = []


def check(name: str, fn) -> None:
    try:
        fn()
        print(f"  ✓ {name}")
    except Exception as e:  # noqa: BLE001
        FAILURES.append(f"{name}: {e}")
        print(f"  ✗ {name}: {e}")


def main() -> int:
    print("RunPod LoRA trainer pod — smoke test")

    # 1. Handler imports (runpod + our functions). Guarded by
    #    `if __name__ == "__main__"` so importing does not start the worker.
    def _handler_imports():
        import handler  # noqa: F401
        for fn in ("_resolve_manifest", "_download_input", "_bucket_name", "_bucket_prefix", "handler"):
            assert hasattr(handler, fn), f"handler missing {fn}"
    check("handler imports + functions", _handler_imports)

    # 2. Bundled runner scripts are present and expose the trainer API.
    def _runner_imports():
        scripts = Path(__file__).resolve().parent / "scripts"
        assert scripts.is_dir(), f"scripts dir missing: {scripts}"
        if str(scripts) not in sys.path:
            sys.path.insert(0, str(scripts))
        import run_zimage_trainer  # noqa: F401
        import run_trainer  # noqa: F401
        for mod in (run_zimage_trainer, run_trainer):
            for fn in ("_do_load", "_do_train"):
                assert hasattr(mod, fn), f"{mod.__name__} missing {fn}"
    check("runner scripts bundled + _do_load/_do_train", _runner_imports)

    # 3. run_training() entrypoint exists and accepts the expected kwargs.
    def _run_training_signature():
        import runner
        assert hasattr(runner, "run_training"), "runner missing run_training"
        import inspect
        params = inspect.signature(runner.run_training).parameters
        for kw in ("subject_id", "subject_name", "ref_image_paths", "output_path",
                   "backend", "base_model_id", "resolution", "rank", "alpha",
                   "learning_rate", "steps", "image_prompts"):
            assert kw in params, f"run_training missing kwarg {kw}"
    check("run_training() signature", _run_training_signature)

    # 4. Manifest resolution: local path kept, s3/http URL routed to download.
    def _manifest_resolution():
        import handler
        tmp = Path("/tmp")
        local = tmp / "ref.png"
        local.write_bytes(b"x")
        paths = handler._resolve_manifest({"images": [{"image_path": str(local)}]})
        assert paths == [str(local)], f"local path not kept: {paths}"
    check("_resolve_manifest local path", _manifest_resolution)

    if FAILURES:
        print(f"\nSMOKE TEST FAILED ({len(FAILURES)}):")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("\nSMOKE TEST PASSED — image wiring is correct.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
