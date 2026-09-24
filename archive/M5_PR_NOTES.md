# M5 PR notes — LLM providers + OpenAI-compatible routing / smart escalation

Working notes for the **M5** PR. Branch: `pr/m5-llm-providers`, single commit
`0cb64be0` titled *"M4: LLM providers + voice routing"* (numbering note below).
**No PR exists yet** (`gh pr list --head pr/m5-llm-providers` → empty) — create one later.
Verified against `upstream/main` = `11af2238`.

Workspace equivalents: `feat/llm-providers` (`ll`) plus the stacked `chore/llm-providers-followup`
and `docs/issues-*` branches.

---

## 1. What was the upstream default value?

The provider layer (`backend/services/llm_provider.py`) is **local-first with a hard master gate**:

- **Default provider = `ollama` (local).**
- **Master switch `cloud_models_enabled` defaults OFF** — the docstring calls it the
  "single airplane-mode flip"; *"a fresh install is fully local until the operator turns this on."*
- While it is off, `get_active_provider()` **always returns `OLLAMA` regardless of configuration**
  (hard gate, line ~150).
- Upstream's only cloud provider is **Mistral**; default cloud model =
  `mistral-large-latest` (`GUAARDVARK_MISTRAL_MODEL`, `backend/config.py`).
- Upstream has **no OpenAI-compatible provider** and no `OPENAI_BASE_URL` /
  `OPENAI_API_KEY` / `OPENAI_DEFAULT_MODEL` config at all.
- Escalation default = `claude_escalation_mode: "manual"` (`claude_advisor_service.py:50`),
  Anthropic model default `claude-sonnet-4-20250514` (`GUAARDVARK_CLAUDE_MODEL`).

## 2. Is it kept?

**Yes — the local-first default is preserved.**

- M5 does **not** modify `get_active_provider()` or `cloud_models_enabled()`; both keep
  upstream's behaviour (Ollama + cloud off by default).
- M5 adds **`openai`** as a second `CLOUD_PROVIDERS` entry (`_openai_available()`), behind the
  *same* master switch and per-provider selection. With nothing configured, behaviour is identical
  to upstream (Ollama, fully offline).
- The chat dispatch was generalized from `is_mistral_active()` to
  `get_active_provider() != OLLAMA`, dispatching `mistral_provider` vs `openai_provider` and using
  `get_active_cloud_model()`. The default branch is unchanged.
- M5's new `OPENAI_DEFAULT_MODEL` default is **`gpt-4o-mini`** — only used if the operator selects
  the provider.
- Escalation `auto` resolution preserves the legacy path: `GUAARDVARK_ESCALATION_BASE_URL` set →
  OpenAI-compatible; else `ANTHROPIC_API_KEY` present → Anthropic; else none (offline).
  `ANTHROPIC_API_KEY` + `GUAARDVARK_CLAUDE_MODEL` still work as before.

## 3. How is the feature enabled?

Two independent surfaces.

### 3a. Main chat provider — OpenAI-compatible (DB/UI-gated, same as Mistral)

- **UI:** enable the master **cloud models** switch (`cloud_models_enabled`) and select the
  **OpenAI-compatible** provider + model in `ModelManagementSection.jsx` / Settings
  (`modelService.js`, `llm_provider_api.py`).
- **Env (`backend/config.py`):**
  - `GUAARDVARK_OPENAI_BASE_URL` — default `https://api.openai.com/v1`; set to any
    OpenAI-compatible endpoint (OpenAI, OpenRouter, Groq, Together, vLLM, Ollama's `/v1`).
  - `GUAARDVARK_OPENAI_API_KEY` (or `OPENAI_API_KEY`) — optional.
  - `GUAARDVARK_OPENAI_MODEL` — default `gpt-4o-mini`.
  - `GUAARDVARK_OPENAI_TIMEOUT` — default `120`.
- **Keyless local endpoints are allowed:** `_openai_available()` / `openai_provider.available()`
  return True when a non-default base URL is set even without a key (local vLLM / Ollama `/v1`).
- `get_active_provider()` only returns `openai` when the master switch is ON **and** the provider
  is selected **and** available; otherwise it degrades back to Ollama (a missing key or flipped
  switch can never wedge chat into a dead provider).

  ⚠️ **Second, env-only enable path:** `backend/utils/llm_service.py::_openai_compatible_llm()`
  routes `get_default_llm()` to the OpenAI-compat endpoint whenever
  **`GUAARDVARK_OPENAI_BASE_URL` *and* `GUAARDVARK_OPENAI_MODEL`** are set — **without consulting
  the DB master switch**. Intent is to avoid loading a local Ollama model (VRAM squatting). This is
  a real inconsistency to settle in the PR: two paths can disagree on whether OpenAI is "active".

### 3b. Smart multi-provider escalation

- Enable via setting `claude_escalation_mode = "smart"` (default `"manual"`, `claude_advisor_service.py`).
- Configure the escalation provider (`GUAARDVARK_ESCALATION_PROVIDER` =
  `auto|anthropic|openai|openrouter|ollama|generic`, plus `GUAARDVARK_ESCALATION_BASE_URL`,
  `GUAARDVARK_ESCALATION_API_KEY`, `GUAARDVARK_ESCALATION_MODEL`); legacy `ANTHROPIC_API_KEY` +
  `GUAARDVARK_CLAUDE_MODEL` still resolve to Anthropic.
- Behaviour (`unified_chat_engine._maybe_smart_escalate`): when smart mode is on and the local LLM
  errors or finishes with an empty answer, the reply is produced by the escalation provider instead.

## 4. What M5 adds on top of upstream

- `backend/services/openai_provider.py` (new, ~302 lines) — OpenAI-compatible client
  (chat/complete/stream/list_models/make_llamaindex_llm), keyed off `config.OPENAI_*`.
- `llm_provider.py` — `OPENAI` provider id, `_openai_available()`, `get/set_openai_model()`,
  `is_openai_active()`, `get_active_cloud_model()`, `provider_state()["openai_model"]`.
- `config.py` — `OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_DEFAULT_MODEL / OPENAI_REQUEST_TIMEOUT`.
- `llm_provider_api.py` — OpenAI model list/set endpoints; "test connection" now tests the
  **active** cloud provider, not just Mistral.
- `unified_chat_engine.py` — provider-agnostic cloud dispatch + `_maybe_smart_escalate()`.
- `llm_service.py` — `_openai_compatible_llm()` guard so local Ollama factories don't construct a
  model when chat is routed to an OpenAI-compat endpoint.
- `claude_advisor_service.py` — escalation provider selection (Anthropic or any OpenAI-compatible).
- `mistral_provider.py` — force `resp.encoding = "utf-8"` on the stream (same mojibake fix as the
  openai provider).
- Frontend: `ModelManagementSection.jsx`, `StatusContext.jsx`, `modelService.js`,
  `MusicVideoPage.jsx` (cloud model in the Music Video Director dropdown).
- **Voice/Discord:** `plugins/discord/*` (pi-omni router + OpenAI-compatible main chat,
  `voice-openai-routing`), plus start/stop scripts and config.

## 5. Review findings / decision points before opening the PR

1. **Two enable paths disagree** (see §3a): DB master switch vs env-only
   `GUAARDVARK_OPENAI_BASE_URL`+`_MODEL`. Decide whether `get_default_llm` should also require
   `cloud_models_enabled()`, or whether the env pair is the intended "headless" override.
2. **Commit title says "M4"** (`M4: LLM providers + voice routing`) but this is the M5 track item.
   Reword the commit / PR title to M5.
3. **Scope split.** The branch mixes LLM providers, smart escalation, and Discord/voice routing.
   Confirm these belong in one PR or split voice/Discord into its own PR.
4. **`openai_provider` naming** — `OPENAI_DEFAULT_MODEL = "gpt-4o-mini"` is OpenAI-specific while the
   base URL is generic (OpenRouter/Groq/vLLM). Confirm the default is sensible for non-OpenAI bases
   (it's only a fallback; operators normally set `GUAARDVARK_OPENAI_MODEL`).
5. **Escalation `auto` order** — base_URL wins over Anthropic; confirm that's intended when both
   are configured.

## 6. Rebase / merge status

- `pr/m5-llm-providers` is based on `23ab76e3` (M1), well behind current upstream `11af2238`.
- `git merge-tree --write-tree upstream/main pr/m5-llm-providers` → **clean, 0 conflicts**.
- Safe to rebase onto `11af2238` when opening the PR.

## 7. Suggested PR checklist (later)

- [ ] Rebase `pr/m5-llm-providers` onto current `upstream/main`; rename commit to M5.
- [ ] Settle §5.1 (env-only vs DB-gated enable) and §5.3 (scope).
- [ ] Verify default = Ollama/offline with nothing configured (`cloud_models_enabled` off).
- [ ] Verify a keyless local OpenAI-compat endpoint (vLLM / Ollama `/v1`) works and that
      `_openai_compatible_llm()` doesn't load a local Ollama model.
- [ ] Verify `claude_escalation_mode=smart` escalates on local failure, and stays off by default.
- [ ] Tests/lint for the touched backend modules + frontend `npm run lint`.
