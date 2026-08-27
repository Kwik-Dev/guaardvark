# RunPod serverless worker image for the runpod_lora_trainer plugin.
#
# Build + deploy (one-time, on your RunPod account):
#   docker build -t <your-registry>/guaardvark-lora-trainer:latest .
#   docker push <your-registry>/guaardvark-lora-trainer:latest
#   # In the RunPod console, create a Serverless endpoint from this image.
#
# Then set in .env:
#   GUAARDVARK_LORA_BACKEND=auto        # or runpod
#   GUAARDVARK_RUNPOD_ENDPOINT_ID=<endpoint id>
#   GUAARDVARK_RUNPOD_API_KEY=<api key>
# and enable the runpod_lora_trainer plugin in the Plugins UI (this disables the
# local lora_trainer via its `excludes`).
FROM pytorch/pytorch:2.1.0-cuda11.8-cudnn8-runtime

WORKDIR /app

# SDK + training stack. `runpod` is required; the rest mirror what the local
# trainers need so the same runner scripts run here unchanged.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY handler.py .
# The runner scripts from plugins/lora_trainer are copied in so the pod runs the
# exact same training code as a local run.
COPY runner.py .

# RunPod workers register a handler via runpod.serverless.start.
CMD ["python", "-u", "/app/handler.py"]
