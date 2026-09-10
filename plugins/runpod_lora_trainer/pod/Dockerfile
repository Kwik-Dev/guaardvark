# RunPod serverless worker image for the runpod_lora_trainer plugin.
#
# Base: modern PyTorch + CUDA 12 (matches the Z-Image stack the backend/venv
# uses; older 2.1.0-cuda11.8 cannot run diffusers>=0.38 ZImagePipeline).
# Supports Ada/Ampere 12+ GPUs (RTX 4090, A40, L40S, 3090). Build + deploy
# (one-time, on your RunPod account). Build from the plugins/ directory so both
# the pod files and the local trainer scripts are in context:
#   cd plugins
#   docker build -f runpod_lora_trainer/pod/Dockerfile \
#       -t <your-registry>/guaardvark-lora-trainer:latest .
#   docker push <your-registry>/guaardvark-lora-trainer:latest
#   # In the RunPod console, create a Serverless endpoint from this image.
#
# Then set in .env:
#   GUAARDVARK_LORA_BACKEND=auto        # or runpod
#   GUAARDVARK_RUNPOD_ENDPOINT_ID=<endpoint id>
#   GUAARDVARK_RUNPOD_API_KEY=<api key>
# and enable the runpod_lora_trainer plugin in the Plugins UI (this disables the
# local lora_trainer via its `excludes`).
FROM pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime

WORKDIR /app

# SDK + training stack. `runpod` is required; the rest mirror what the local
# trainers need so the same runner scripts run here unchanged.
COPY runpod_lora_trainer/pod/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY runpod_lora_trainer/pod/handler.py .
COPY runpod_lora_trainer/pod/runner.py .
COPY runpod_lora_trainer/pod/smoke_test.py .
# Bundle the local trainer scripts so the pod runs the exact same training code
# as a local run (runner.py imports run_zimage_trainer / run_trainer).
COPY lora_trainer/scripts/ ./scripts/

# Build-time smoke test: verifies handler/runner imports + wiring. Fails the
# build if anything is wrong, so a pushed image is known-good.
RUN python smoke_test.py

# RunPod workers register a handler via runpod.serverless.start.
CMD ["python", "-u", "/app/handler.py"]
