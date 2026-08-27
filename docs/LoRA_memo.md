Z-Imageで人物の一貫性を保つLoRAをRunPodで作る手順｜60枚・約40分・0.73ドルの実測
https://note.com/locany/n/n6c8c4d0ed696

LTX-2.3とZ-Image TurboによるI2Vワークフロ
https://physical-i.jp/ai/ltx-2-3-i2v-business-verification/


MacBook Airだけで、自作キャラのLoRAを0円で学習させた（Draw Things・4000ステップの全記録）
https://note.com/chbipoo/n/n1631a320139e


----

## Draw Things

『Mac miniでMiniMax-H3』Draw Things対応まで待てなかったのでChatGPTに頼んだら、生成環境ができた
https://note.com/sane_weasel8589/n/n50fafc452948


Draw Thingsとは？無料でMacとiPhoneに使えるローカルAI画像生成の全部 (2026年版)
https://aipicks.jp/mag/draw-things-guide-2026

----

Confirmed — **MPS is fully supported and being used right now.** The log shows exactly how it works:

```
[run_zimage_trainer] loaded on CPU ...        ← initial load (by design)
[run_zimage_trainer] staging VAE on mps for latent cache... → cached 8 latents; VAE off GPU
[run_zimage_trainer] staging text encoder on mps for prompt cache... → cached 8 prompts; TE off GPU
[run_zimage_trainer] staging transformer+LoRA on mps (res=768, rank=16, dtype=torch.bfloat16)...
[run_zimage_trainer] step 1/640 loss=0.58203   ← ACTUALLY TRAINING
```

So:
- **MPS is active** — the heavy modules (VAE, text encoder, transformer+LoRA) are each staged onto the Metal/MPS device for their compute pass, then moved off to free memory (that's the `off GPU`/`off` pattern).
- **"loaded on CPU"** just means the initial model load happened on CPU because the full pipeline can't sit in unified memory at once — it's the same memory-saving strategy the 16 GB CUDA path uses.
- **Training is running:** it's at **step 1 of 640**, loss 0.58203.

So Elara's LoRA training is genuinely in progress on MPS. The monitor will report when it completes (640 steps, then a `Elara_v*.safetensors` will be saved).