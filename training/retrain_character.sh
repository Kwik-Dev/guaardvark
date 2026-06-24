#!/usr/bin/env bash
# One-command character-LoRA retrain launcher (FLUX-dev / SimpleTuner, 16 GB).
#
# Orchestrates the proven pieces into a single gated pipeline:
#   pretrain_gate  ->  [auto-caption]  ->  SimpleTuner train  ->  watchdog
#                  ->  verify_training_outputs  ->  copy best checkpoint  ->  register Subject
#
# The gate runs FIRST and aborts before any GPU work if the dataset is bad (the horse-head
# guard). Designed for an overnight run. NOTHING here trains until the gate passes.
#
# Usage:
#   bash training/retrain_character.sh                 # full run (GPU, ~3-4h)
#   bash training/retrain_character.sh --check         # gate (+caption) only, NO GPU/training
#   bash training/retrain_character.sh --caption       # (re)caption before gating, then train
#   bash training/retrain_character.sh --force         # train even if the gate FAILS (not advised)
#   bash training/retrain_character.sh --no-register   # skip the DB Subject registration step
#   bash training/retrain_character.sh --checkpoint N  # deploy checkpoint-N (default: highest)
#
# Env/flags (defaults target sage_harlow):
#   --trigger WORD   --dataset DIR   --name NAME(output subdir)
set -uo pipefail

REPO=/home/llamax1/LLAMAX8
TRIGGER=sage_harlow
NAME=sage_harlow
DATASET="$REPO/training/sage_harlow/dataset"
PROFILE="$REPO/training/sage_harlow/character_profile.md"
ST="$REPO/plugins/lora_trainer/SimpleTuner"
MDB="$ST/config/multidatabackend.json"
DEPLOY="$REPO/data/training/loras/${NAME}.safetensors"
PY="$REPO/backend/venv/bin/python"

DO_CHECK=0; DO_CAPTION=0; FORCE=0; DO_REGISTER=1; CHECKPOINT=""

while [ $# -gt 0 ]; do
  case "$1" in
    --check) DO_CHECK=1 ;;
    --caption) DO_CAPTION=1 ;;
    --force) FORCE=1 ;;
    --no-register) DO_REGISTER=0 ;;
    --checkpoint) CHECKPOINT="${2:-}"; shift ;;
    --trigger) TRIGGER="${2:-}"; shift ;;
    --dataset) DATASET="${2:-}"; shift ;;
    --name) NAME="${2:-}"; DEPLOY="$REPO/data/training/loras/${NAME}.safetensors"; shift ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1"; exit 2 ;;
  esac
  shift
done

say() { printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }
die() { printf '\n\033[1;31mABORT: %s\033[0m\n' "$*"; exit 1; }
[ -x "$PY" ] || PY=python3

# 1) optional (re)caption -----------------------------------------------------
if [ "$DO_CAPTION" = 1 ]; then
  say "Auto-captioning $DATASET (trigger=$TRIGGER)"
  "$PY" "$REPO/scripts/caption_dataset.py" "$DATASET" --trigger "$TRIGGER" \
      ${PROFILE:+--profile "$PROFILE"} || die "captioning failed"
fi

# 2) pre-train gate (the horse-head guard) -----------------------------------
say "Pre-train quality gate"
"$PY" "$REPO/scripts/pretrain_gate.py" "$DATASET" --trigger "$TRIGGER" --config "$MDB"
GATE=$?
if [ "$GATE" -ne 0 ]; then
  if [ "$FORCE" = 1 ]; then
    echo "Gate FAILED but --force given; continuing against advice."
  else
    die "dataset gate failed — fix the dataset (see DATASET_SPEC.md) or pass --force."
  fi
fi

if [ "$DO_CHECK" = 1 ]; then
  say "--check: gate complete, stopping before any GPU/training."
  exit $GATE
fi

# 3) train (GPU, heavy) -------------------------------------------------------
say "Launching SimpleTuner (GPU ~3-4h). Desktop should be stopped: sudo systemctl stop gdm"
RUN="$REPO/training/sage_harlow/run_st.sh"
[ -f "$RUN" ] || die "run_st.sh not found at $RUN"
bash "$RUN" 2>&1 | tee "$REPO/training/sage_harlow/st_live.log"
grep -aq "ST_RUN_DONE" "$REPO/training/sage_harlow/st_live.log" || die "training did not finish cleanly"

# 4) pick + deploy checkpoint -------------------------------------------------
OUT="$REPO/training/sage_harlow/output/${NAME}_flux_v1"
[ -d "$OUT" ] || OUT="$REPO/training/sage_harlow/output"
if [ -z "$CHECKPOINT" ]; then
  CKPT_DIR=$(ls -d "$OUT"/checkpoint-* 2>/dev/null | sort -t- -k2 -n | tail -1)
else
  CKPT_DIR="$OUT/checkpoint-$CHECKPOINT"
fi
[ -n "$CKPT_DIR" ] && [ -d "$CKPT_DIR" ] || die "no checkpoint found under $OUT"
SRC="$CKPT_DIR/pytorch_lora_weights.safetensors"
[ -f "$SRC" ] || die "checkpoint weights missing: $SRC"
say "Deploying $(basename "$CKPT_DIR") -> $DEPLOY"
# Keep the old LoRA for A/B before overwriting.
[ -f "$DEPLOY" ] && cp -f "$DEPLOY" "${DEPLOY%.safetensors}.prev.safetensors" && \
  echo "previous LoRA backed up to ${DEPLOY%.safetensors}.prev.safetensors (for A/B)"
mkdir -p "$(dirname "$DEPLOY")"
cp -f "$SRC" "$DEPLOY"

# 5) post-train verify --------------------------------------------------------
say "Post-train verification"
"$PY" "$REPO/scripts/verify_training_outputs.py" || echo "[warn] verify reported issues — review above"

# 6) register Subject (DB) ----------------------------------------------------
if [ "$DO_REGISTER" = 1 ]; then
  say "Registering Subject (trigger + lora_path + bible)"
  REG="$REPO/training/sage_harlow/clip_steampunk/register_subject.py"
  if [ -f "$REG" ]; then
    "$PY" "$REG" || echo "[warn] Subject registration failed — run it manually"
  else
    echo "[warn] register_subject.py not found; register the LoRA in the Cast Library manually."
  fi
fi

cat <<EOF

== DONE ==
Deployed LoRA : $DEPLOY
Previous (A/B): ${DEPLOY%.safetensors}.prev.safetensors
Validation    : training/sage_harlow/output/validation_images/  (eyeball best checkpoint)

NEXT: render a 20-clip A/B sample (old .prev vs new) and confirm full-body + horse-riding shots
keep the face and freckles. Target: <2% ridiculous (was ~8%). Restore desktop: sudo systemctl start gdm
EOF
