"""Cross-encoder reranking for retrieval results.

The fused retriever scores a query and a passage independently (bi-encoder plus
BM25); a cross-encoder reads the pair together and is markedly better at deciding
relevance. It is applied to the candidate pool AFTER filtering and dedup, and its
order is final: MMR runs only when the cross-encoder did not score the pool.

The model competes for VRAM with image and video generation, so loading is
admitted against free VRAM and falls back to CPU rather than failing a query.
Every outcome is reported back to the caller for the retrieval trace: a rerank
that did not happen must never look like one that did.

Residency is reclaimable. `unload()` releases the weights and the orchestrator
calls it -- both from its periodic registry (via `status()`) and directly when a
render needs the card. Without that, a query that happened to run while the card
was quiet would take ~1.1 GB of VRAM for the life of the process and starve every
image batch behind it; that is exactly what F-RAG-10 was.
"""

import gc
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from backend.utils.backend_http import in_mcp_process

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "BAAI/bge-reranker-v2-m3"

# Working set a predict() pass needs on top of the weights. Measured at ~40 MB for
# a 2-pair batch; a full candidate pool at max_length is larger, so the slot is
# priced with room rather than with the idle figure.
_ACTIVATION_HEADROOM_MB = 128

_lock = threading.Lock()
_model = None
_model_device: Optional[str] = None
_load_failed_reason: Optional[str] = None
_model_vram_mb: int = 0     # weights + headroom, 0 when not GPU-resident
_inflight: int = 0          # predict() passes in progress; blocks unload


def is_enabled() -> bool:
    return os.environ.get("GUAARDVARK_RERANK_CROSS_ENCODER", "true").lower() == "true"


def model_name() -> str:
    return os.environ.get("GUAARDVARK_RERANK_MODEL", DEFAULT_MODEL)


# The weights are fetched only by Install (Settings > Knowledge), never by a
# search: a first query on a fresh install used to download 2.2 GB unannounced.
# Install takes the files CrossEncoder reads, not the repo's other formats.
_INSTALL_PATTERNS = ["*.json", "*.safetensors", "*.model", "*.txt"]
INSTALL_SIZE_GB = {"BAAI/bge-reranker-v2-m3": 2.3}

_install_lock = threading.Lock()
_install: Dict[str, Any] = {"state": "idle", "model": None, "progress": None,
                            "downloaded_gb": 0.0, "error": None}


def _local_snapshot(name: str) -> Optional[str]:
    """The model's folder in the Hugging Face cache, or None. Never touches the network."""
    try:
        from huggingface_hub import snapshot_download
        path = snapshot_download(repo_id=name, local_files_only=True,
                                 allow_patterns=_INSTALL_PATTERNS, token=False)
    except Exception:
        return None
    folder = os.fspath(path)
    has_config = os.path.isfile(os.path.join(folder, "config.json"))
    has_weights = any(f.endswith(".safetensors") for f in os.listdir(folder))
    return folder if has_config and has_weights else None


def is_installed() -> bool:
    return _local_snapshot(model_name()) is not None


def not_installed_reason() -> str:
    name = model_name()
    size = INSTALL_SIZE_GB.get(name)
    about = f", about {size} GB" if size else ""
    return (f"the reranker model ({name}{about}) is not installed; install it in "
            f"Settings > Knowledge. Searches run without reranking until then.")


# Below this rerank score a passage is unrelated to the question, per model: the
# scale belongs to the model, so a model not listed here gets no floor.
#
# bge-reranker-v2-m3, measured 2026-09-27 through search_with_llamaindex on two
# indexes (1,145 chunks of product docs and extrusion manuals; two short notes):
# for 12 questions the documents answer, the best passage scored 0.76-0.998 and
# every passage from an answering document scored 0.62 or more; 12 general
# questions they do not answer (capital of Australia, coffee beans, the 1928
# World Series, ...) peaked at 0.0996 on either index. 0.30 is the midpoint of
# that gap on the logit scale.
RELEVANCE_FLOOR = {
    "BAAI/bge-reranker-v2-m3": 0.30,
}


def relevance_floor() -> Optional[float]:
    """The score a passage needs to count as related, or None when unknown.

    GUAARDVARK_RAG_MIN_RERANK_SCORE overrides the measured value; 0 turns the
    floor off.
    """
    raw = os.environ.get("GUAARDVARK_RAG_MIN_RERANK_SCORE", "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            logger.warning("GUAARDVARK_RAG_MIN_RERANK_SCORE=%r is not a number; ignoring", raw)
    return RELEVANCE_FLOOR.get(model_name())


def drop_unrelated(results: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    """Keep the retrieved passages this reranker rates related to the question.

    Applies only when the reranker scored this retrieval and its model has a
    floor. A hit it did not score, such as a dependency-expansion chunk, stays
    only beside a surviving scored hit from the same source. Returns (kept,
    number dropped).
    """
    floor = relevance_floor()
    scored = [r for r in results if r.get("rerank_score") is not None]
    if not floor or not scored:
        return results, 0

    def _source(r):
        return (r.get("metadata") or {}).get("source_filename")

    related_sources = {_source(r) for r in scored if r["rerank_score"] >= floor}
    kept = [
        r for r in results
        if (r["rerank_score"] >= floor if r.get("rerank_score") is not None
            else _source(r) in related_sources)
    ]
    return kept, len(results) - len(kept)


def _pick_device() -> str:
    """GPU only when there is comfortable headroom; otherwise CPU.

    The floor is deliberately above the model's own footprint: admitting it into
    the last free gigabyte would just move an OOM onto whichever generation job
    starts next. This choice is made per load, not once per process -- an unload
    releases it, and the next query re-decides against the card as it is then.
    """
    # Memory held by the MCP process is invisible to the backend's GPU admission.
    if in_mcp_process():
        return "cpu"
    floor_mb = int(os.environ.get("GUAARDVARK_RERANK_MIN_VRAM_MB", "3000"))
    try:
        from backend.services.gpu_resource_coordinator import has_gpu, get_available_vram
        if not has_gpu():
            return "cpu"
        info = get_available_vram()
        if info.get("success") and info.get("available_mb", 0) >= floor_mb:
            return "cuda"
        logger.info(
            "Reranker: %s MB free < %s MB floor — loading on CPU",
            info.get("available_mb"), floor_mb,
        )
        return "cpu"
    except Exception as e:
        logger.debug("Reranker device probe failed (%s); using CPU", e)
        return "cpu"


def _measure_vram_mb(model) -> int:
    """Weights actually on the GPU, plus an activation allowance. 0 if not resident."""
    try:
        import torch
        params = getattr(model, "model", None)
        if params is None:
            return 0
        total = sum(
            p.numel() * p.element_size()
            for p in params.parameters()
            if p.device.type == "cuda"
        )
        if total <= 0:
            return 0
        return int(total // (1024 * 1024)) + _ACTIVATION_HEADROOM_MB
    except Exception:
        return 0


def _get_model():
    """Load the cross-encoder once. Returns None if unavailable (never raises).

    Loads only from the local cache. A model that is not installed is not a
    failure to remember: the next query checks again, so an Install takes
    effect without a restart.
    """
    global _model, _model_device, _load_failed_reason, _model_vram_mb
    if _model is not None or _load_failed_reason is not None:
        return _model
    if not is_installed():
        return None
    with _lock:
        if _model is not None or _load_failed_reason is not None:
            return _model
        try:
            from sentence_transformers import CrossEncoder
            device = _pick_device()
            name = model_name()
            kwargs: Dict[str, Any] = {"device": device, "max_length": 512}
            if device == "cuda":
                # fp16 halves the card cost (2424 MB -> 1334 MB measured on
                # bge-reranker-v2-m3) and reranking is a ranking decision, not an
                # arithmetic one. CPU stays fp32: half precision there is slower,
                # not faster, and costs no VRAM to avoid.
                import torch
                kwargs["model_kwargs"] = {"torch_dtype": torch.float16}
            logger.info("Reranker: loading %s on %s", name, device)
            _model = CrossEncoder(name, local_files_only=True, **kwargs)
            _model_device = device
            _model_vram_mb = _measure_vram_mb(_model) if device == "cuda" else 0
            logger.info(
                "Reranker: ready (%s on %s%s)", name, device,
                f", ~{_model_vram_mb}MB VRAM" if _model_vram_mb else "",
            )
        except Exception as e:
            _load_failed_reason = f"{e.__class__.__name__}: {str(e)[:160]}"
            logger.warning("Reranker unavailable — %s", _load_failed_reason)
            _model = None
    return _model


def status() -> Dict[str, Any]:
    """What the reranker currently holds. Cheap; safe to poll."""
    return {
        "loaded": _model is not None,
        "device": _model_device,
        "vram_mb": _model_vram_mb,
        "in_use": _inflight,
        "model": model_name(),
    }


def install_status() -> Dict[str, Any]:
    """For Settings > Knowledge: on or off, installed or not, and any install in progress."""
    name = model_name()
    with _install_lock:
        job = dict(_install)
    return {
        "enabled": is_enabled(),
        "model": name,
        "installed": is_installed(),
        "size_gb": INSTALL_SIZE_GB.get(name),
        "load_error": _load_failed_reason,
        "install": job,
    }


def start_install() -> Dict[str, Any]:
    """Download the reranker's weights in the background. The only path that fetches them."""
    name = model_name()
    if is_installed():
        return install_status()
    with _install_lock:
        if _install["state"] == "running":
            return install_status()
        _install.update({"state": "running", "model": name, "progress": 0 if INSTALL_SIZE_GB.get(name) else None,
                         "downloaded_gb": 0.0, "error": None, "started_at": time.time()})
    threading.Thread(target=_run_install, args=(name,), daemon=True,
                     name="reranker-install").start()
    return install_status()


def _run_install(name: str) -> None:
    global _load_failed_reason
    from huggingface_hub import snapshot_download
    from backend.services.local_weights import hf_repo_cache_dir

    cache = hf_repo_cache_dir(name)
    stop = threading.Event()
    total = INSTALL_SIZE_GB.get(name)

    def _bytes() -> int:
        n = 0
        for root, _dirs, files in os.walk(cache):
            for f in files:
                try:
                    n += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
        return n

    baseline = _bytes()

    def _watch() -> None:
        while not stop.wait(1.0):
            done = max(0, _bytes() - baseline) / 1024 ** 3
            with _install_lock:
                if _install["state"] != "running":
                    return
                _install["downloaded_gb"] = round(done, 2)
                if total:
                    _install["progress"] = min(99, int(100 * done / total))

    threading.Thread(target=_watch, daemon=True, name="reranker-install-progress").start()
    try:
        logger.info("Reranker: installing %s", name)
        snapshot_download(repo_id=name, allow_patterns=_INSTALL_PATTERNS, token=False)
        if not _local_snapshot(name):
            raise RuntimeError("the download finished without config.json and a .safetensors file")
        with _lock:
            _load_failed_reason = None
        with _install_lock:
            _install.update({"state": "done", "progress": 100, "finished_at": time.time(),
                             "downloaded_gb": round(max(0, _bytes() - baseline) / 1024 ** 3, 2)})
        logger.info("Reranker: %s installed", name)
    except Exception as e:
        logger.error("Reranker install failed: %s", e)
        with _install_lock:
            _install.update({"state": "failed", "error": f"{e.__class__.__name__}: {str(e)[:200]}",
                             "finished_at": time.time()})
    finally:
        stop.set()


def unload() -> Dict[str, Any]:
    """Release the model. Returns what happened; never raises.

    ``unloaded`` is True whenever nothing is resident afterwards -- including the
    case where nothing was loaded to begin with -- so a caller dropping a stale
    registry slot can trust it. It is False only when a predict() pass is in
    flight: dropping the module reference then would not free the memory anyway
    (the running call holds its own reference) and would misreport what was freed.
    """
    global _model, _model_device, _load_failed_reason, _model_vram_mb
    with _lock:
        if _model is None:
            return {"unloaded": True, "freed_mb": 0, "reason": "not loaded"}
        if _inflight > 0:
            return {"unloaded": False, "freed_mb": 0, "reason": f"in use ({_inflight} in flight)"}

        freed = _model_vram_mb
        was_device = _model_device
        _model = None
        _model_device = None
        _model_vram_mb = 0
        # A failed load is remembered so we don't retry it every query; an unload
        # is not a failure, so the next query is free to load again.
        _load_failed_reason = None

    gc.collect()
    if was_device == "cuda":
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.synchronize()
        except Exception as e:
            logger.debug("Reranker unload: empty_cache failed (%s)", e)

    logger.info("Reranker: unloaded from %s (~%sMB freed)", was_device, freed)
    return {"unloaded": True, "freed_mb": freed, "reason": None}


def rerank(query: str, results: List[Dict[str, Any]],
           top_n: Optional[int] = None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Reorder `results` by cross-encoder relevance.

    Returns (results, info). `results` is returned untouched whenever reranking
    did not run, and `info` always says why.
    """
    global _inflight

    info: Dict[str, Any] = {"applied": False, "reason": None, "model": None, "device": None}

    if not is_enabled():
        info["reason"] = "disabled by GUAARDVARK_RERANK_CROSS_ENCODER"
        return results, info
    if len(results) < 2:
        info["reason"] = "fewer than 2 candidates"
        return results, info

    model = _get_model()
    if model is None:
        info["reason"] = _load_failed_reason or (
            "model unavailable" if is_installed() else not_installed_reason())
        return results, info

    # Pin across predict() so an eviction racing this call is refused rather than
    # reporting VRAM it did not free.
    with _lock:
        _inflight += 1
    try:
        pairs = [(query, (r.get("text") or "")[:4000]) for r in results]
        scores = model.predict(pairs, show_progress_bar=False)
        for r, s in zip(results, scores):
            r["rerank_score"] = float(s)
        ordered = sorted(results, key=lambda r: r.get("rerank_score", 0.0), reverse=True)
        if top_n:
            ordered = ordered[:top_n]
        info.update({
            "applied": True,
            "model": model_name(),
            "device": _model_device,
            "scored": len(pairs),
        })
        return ordered, info
    except Exception as e:
        info["reason"] = f"rerank failed: {e.__class__.__name__}: {str(e)[:120]}"
        logger.warning("Reranker: %s", info["reason"])
        return results, info
    finally:
        with _lock:
            _inflight = max(0, _inflight - 1)
