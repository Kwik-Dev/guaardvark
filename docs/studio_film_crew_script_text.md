**No — you don't need to pre-create ELARA or the Wolf in the Cast Library.**

### How it works
The Film Crew **auto-creates** cast subjects from the Script Text. In `production_swarm_tasks.py`, when the Screenwriter runs, it:
1. Parses your markup (`[[...]]`, `{{...}}`).
2. For each subject it finds, it looks up `Subject` by name+kind; if none exists it **creates a new `Subject` row** and links it to the production.
3. `[[Name]]` sets that subject's `cast_required = True`.

So ELARA and the Corrupted Wolf get auto-created as Subjects — no Cast Library setup needed up front.

### The catch: `[[...]]` gating
Because they're marked `cast_required`, the production will **pause at the Casting stage** until you resolve them. The actual LoRA assignment/training is **user-gated** (`confirm_casting` / `cast_subject` — the pipeline deliberately does *not* auto-train on your GPU). At that point you'll be asked to either:
- **Attach an existing trained LoRA** (if one is in the Cast Library), or
- **Trigger LoRA training** for that subject (needs reference images).

Until you do, the production can't move past casting.

### If you don't want that gating
For characters you're fine generating per-shot without a locked identity, use **`{{...}}`** instead of `[[...]]`:
- `[[ELARA]]` → identity-locked, LoRA-gated at casting.
- `{{ELARA}}` → generated inline each shot, **no** casting gate.

So you can start the production with no Cast Library entries either way. Use `[[...]]` only if you actually want a stable, trained character identity; otherwise `{{...}}` keeps the pipeline moving automatically.😴