#!/bin/bash
# scripts/verify_gpu_stack.sh
# Advisory verification: does each provisioned venv run a GPU kernel, and is
# Ollama serving on the GPU? NEVER blocks boot — exits 0, records degraded
# state to data/gpu_stack_status.json for the health layer.
set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATUS_FILE="$REPO_ROOT/data/gpu_stack_status.json"
DEGRADED=()

check_venv() {
    local label="$1" py="$2"
    [ -x "$py" ] || { return 0; }   # venv absent = not provisioned, not degraded
    if ! "$py" -c "import torch; torch.zeros(1).cuda()" >/dev/null 2>&1; then
        echo "  ⚠ $label: torch cannot run a GPU kernel"
        DEGRADED+=("$label")
    else
        echo "  ✔ $label: GPU kernel OK"
    fi
}

echo "GPU stack verification (advisory):"
check_venv "backend"             "$REPO_ROOT/backend/venv/bin/python"
check_venv "audio_foundry"       "$REPO_ROOT/plugins/audio_foundry/venv/bin/python"
check_venv "audio_foundry-music" "$REPO_ROOT/plugins/audio_foundry/venv-music/bin/python"
check_venv "video_editor"        "$REPO_ROOT/plugins/video_editor/venv/bin/python"

# Ollama: is a model loaded fully on GPU? `ollama ps` prints a PROCESSOR column.
if command -v ollama >/dev/null 2>&1 && ollama ps >/dev/null 2>&1; then
    # Match the PROCESSOR column ("NN% CPU"), not a model NAME that contains
    # "cpu" (e.g. a model called cpu-bench would otherwise false-positive).
    if ollama ps 2>/dev/null | grep -qiE '[0-9]+%[[:space:]]*cpu'; then
        echo "  ⚠ ollama: a model is (partly) on CPU — check NUM_PARALLEL vs VRAM"
        DEGRADED+=("ollama-cpu-offload")
    else
        echo "  ✔ ollama: reachable (no CPU-offload flagged)"
    fi
fi

mkdir -p "$REPO_ROOT/data"
if [ ${#DEGRADED[@]} -eq 0 ]; then
    printf '{"degraded": false, "components": []}\n' > "$STATUS_FILE"
    echo "GPU stack: healthy."
else
    printf '{"degraded": true, "components": [%s]}\n' \
        "$(printf '"%s",' "${DEGRADED[@]}" | sed 's/,$//')" > "$STATUS_FILE"
    echo "GPU stack: DEGRADED (${DEGRADED[*]}). System still boots; see $STATUS_FILE."
fi
exit 0
