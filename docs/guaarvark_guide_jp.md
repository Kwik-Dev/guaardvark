# Guaardvark — 実践ガイド（日本語版）

*セルフホスト型・オフラインファーストの AI ワークステーション。エージェント、メディア生成、RAG、音声、70+ のツールエンジン、そしてそれらを操作する 70+ のサーフェスを、すべてあなたのマシン上で実行します。*

- https://guaardvark.com/features/

  > [個人開発（solo developer）が開発](https://github.com/guaardvark#support-the-project)

---

## 目次
- [Guaardvark — 実践ガイド（日本語版）](#guaardvark--実践ガイド日本語版)
  - [目次](#目次)
  - [1. Guaardvark とは？](#1-guaardvark-とは)
  - [2. 4つのサーフェス](#2-4つのサーフェス)
    - [CLI \& REPL](#cli--repl)
  - [3. アーキテクチャ概要](#3-アーキテクチャ概要)
  - [4. LLM / プロバイダ](#4-llm--プロバイダ)
    - [チャットプロバイダ](#チャットプロバイダ)
    - [設定フロー](#設定フロー)
    - ["Uncle Claude"（エスカレーション / ガーディアンモデル）](#uncle-claudeエスカレーション--ガーディアンモデル)
  - [5. エージェントと Agent Screen](#5-エージェントと-agent-screen)
  - [6. スキルとレシピ](#6-スキルとレシピ)
  - [7. メディア生成](#7-メディア生成)
  - [8. ナレッジ: RAG・検索・メモリ](#8-ナレッジ-rag検索メモリ)
    - [Lesson Pearls（学習メモリ）](#lesson-pearls学習メモリ)
  - [9. Rules \& Prompts](#9-rules--prompts)
  - [10. Swarm / Film Crew / Interconnector](#10-swarm--film-crew--interconnector)
    - [Swarm（並列コーディングエージェント）— `plugins/swarm/`](#swarm並列コーディングエージェント-pluginsswarm)
    - [Film Crew（逐次メディアパイプライン）](#film-crew逐次メディアパイプライン)
    - [Interconnector（マルチマシン同期）](#interconnectorマルチマシン同期)
  - [11. 自己改善](#11-自己改善)
  - [12. プラグインシステム](#12-プラグインシステム)
  - [13. 連携: Discord / MCP / CLI](#13-連携-discord--mcp--cli)
    - [Discord（`plugins/discord/`）](#discordpluginsdiscord)
    - [MCP](#mcp)
    - [プロンプトからのツール呼び出し](#プロンプトからのツール呼び出し)
  - [14. Lesson Pearls メモリシステム](#14-lesson-pearls-メモリシステム)
  - [15. 現実的なユースケース](#15-現実的なユースケース)
  - [16. 注意（実例ベース）](#16-注意実例ベース)
- [比較ガイド: Guaardvark と Hermes の選び方](#比較ガイド-guaardvark-と-hermes-の選び方)
  - [1. 哲学的な分岐: "スタジオ" vs. "アシスタント"](#1-哲学的な分岐-スタジオ-vs-アシスタント)
    - [比較プロファイル（要約）](#比較プロファイル要約)
  - [2. サーフェスとインターフェース](#2-サーフェスとインターフェース)
    - [インターフェース入口](#インターフェース入口)
      - [Guaardvark の 4 サーフェス](#guaardvark-の-4-サーフェス)
      - [Hermes の主入口](#hermes-の主入口)
    - [技術的差異: Vision vs. Text](#技術的差異-vision-vs-text)
  - [3. Guaardvark の強み: Film Crew メディアパイプライン](#3-guaardvark-の強み-film-crew-メディアパイプライン)
    - [Film Crew の逐次パイプライン](#film-crew-の逐次パイプライン)
    - [ローカル生成とハードウェア負荷](#ローカル生成とハードウェア負荷)
  - [4. Hermes の強み: スケジュール自動化とゲートウェイ到達](#4-hermes-の強み-スケジュール自動化とゲートウェイ到達)
  - [5. 学習ループ: Lesson Pearls vs. Honcho Dialectic](#5-学習ループ-lesson-pearls-vs-honcho-dialectic)
    - [Guaardvark（タスク学習）](#guaardvarkタスク学習)
    - [Hermes（ユーザー学習）](#hermesユーザー学習)
    - [メモリ比較](#メモリ比較)
  - [6. 最終判断マトリクス](#6-最終判断マトリクス)
    - [Guaardvark を選ぶべき場合](#guaardvark-を選ぶべき場合)
    - [Hermes を選ぶべき場合](#hermes-を選ぶべき場合)
    - [初学者向けアーキテクチャ原則](#初学者向けアーキテクチャ原則)

---

## 1. Guaardvark とは？

Guaardvark は **セルフホスト型・オフラインファーストの AI ワークステーション** です。クラウド API なしでも、以下を 1 つの環境で扱えます。

- **AI アシスタント / チャット**（ローカルまたはクラウド LLM）
- 実デスクトップを見て操作する **自律スクリーンエージェント**
- 分離 git worktree 上で動く **並列コーディングスウォーム**
- **メディア生成**: テキスト/画像から動画、画像生成、音楽/音声、4K/8K アップスケーリング
- あなたのドキュメント/コードに対する **RAG**
- **音声チャット**、70+ のツールエンジン、Web UI、CLI/REPL、MCP サーバ

構成は **モジュラーモノリス + GPU プラグインサイドカー**: Flask バックエンド、React/Vite フロントエンド、Python CLI、機能ごとのプラグイン（ComfyUI、Swarm、Discord、Audio Foundry など）。

> **基本原則:** デフォルトはローカルファーストかつオフライン。明示的に有効化しない限り、テレメトリやクラウド送信は行いません。メディア生成と埋め込みはローカルのままで、チャットのみ任意でクラウドプロバイダに切り替えられます。

---

## 2. 4つのサーフェス

| サーフェス | 概要 | 得意領域 |
|---|---|---|
| **Web UI** | React 18 / Vite / MUI ダッシュボード | メディア、ダッシュボード、Agent Screen など視覚的・対話的な作業 |
| **CLI + REPL** | `guaardvark` / `llx` | チャット、クイック操作、スクリプト化、自動化 |
| **HTTP API** | Flask、約 90 の自動検出ブループリント | プログラム制御、外部連携 |
| **MCP server** | `python -m backend.mcp` | 外部 AI エージェントからのツール呼び出し |

**Web UI テーマ:** ダッシュボードには複数テーマ（Dark Gray, Light, Guaardvark, Elon's Musk, Fallout, Vader）があり、**Settings → "Change Theme"** で切替可能。設定はブラウザ localStorage に保存されます。チャットプロバイダを変更しても、メディア生成と埋め込みはローカルのままです。

4 つのサーフェスはいずれも同じ Flask バックエンドに接続されます。

### CLI & REPL
- `guaardvark chat "..."` / `guaardvark ask "..."` — 単発チャット
- `guaardvark` 単体起動 — **対話型 REPL**（チャット中心 + スラッシュコマンド）
  - `/imagine`, `/video`, `/voice`, `/agent`, `/web`, `/ingest`, `/search`, `/models`, `/remember`, `/backup`, `/jobs`, `/config`, `/help`
- `guaardvark --json` で機械可読出力、`--server <url>` で接続先インスタンス指定
- コマンドモジュール: `agents`, `images`, `videos`, `generate`, `search`, `index`, `rag`, `files`, `projects`, `clients`, `websites`, `tasks`, `jobs`, `backup`, `outreach`, `settings`, `family`, `status`, `dashboard`, `recipes`

---

## 3. アーキテクチャ概要

```text
Browser / CLI / MCP client
        │ HTTP + WebSocket / stdio
        ▼
Flask backend  (create_app() singleton, ~90 blueprints auto-discovered)
        │
        ├── AgentBrain (Reflex → Instinct → Deliberation routing)
        ├── Tool registry (~70 BaseTool classes, categorized + danger flags)
        ├── Agent executor (see-think-act loop)
        ├── RAG + memory/lessons
        ├── Generation (image/video/audio/voice)
        ├── Swarm + Film Crew
        └── MCP (bidirectional, default-deny)
        │
    PostgreSQL · Redis · Ollama · Celery · plugin sidecars (ComfyUI, etc.)
```

要点:
- **Flask アプリはシングルトン** — `create_app()` は各プロセスで 1 回。`get_or_create_app()` で取得。
- **Blueprint 自動検出** — `backend/api/` に `Blueprint` を export するモジュールを置けば登録される。
- **Tool registry** — 各ツールにカテゴリ、`is_dangerous` / `requires_approval` フラグを保持（MCP セキュリティ境界）。
- **AgentBrain** — Reflex（<100ms）→ Instinct（単発）→ Deliberation（ReACT）。
- **DB スキーマ同期** — `scripts/schema_sync.py` が `models.py` と実 DB の差分を比較（従来型 migration replay ではない）。
- **非同期処理** — Celery worker + beat（動画、学習、自己改善、アウトリーチ、RAG 自動調査）。

システムの正式仕様は [`ARCHITECTURE.md`](ARCHITECTURE.md) を参照してください。

---

## 4. LLM / プロバイダ

チャット/アシスタント LLM の選択はプロバイダ駆動（`backend/services/llm_provider.py`）。デフォルトはローカル Ollama で、明示的に有効化しない限りオフラインを維持します。

### チャットプロバイダ
| プロバイダ | 有効化方法 | 備考 |
|---|---|---|
| **Ollama（ローカル）** | 既定（`127.0.0.1:11434`） | デフォルトで常時利用可能 |
| **Remote/Cloud Ollama** | `.env` に `OLLAMA_BASE_URL=https://<host>:11434` | リモート/クラウド Ollama を利用 |
| **Mistral（クラウド）** | `MISTRAL_API_KEY` + マスタースイッチ + プロバイダ選択 | 内蔵クラウドチャットプロバイダ |
| **OpenAI 互換（クラウド）** | `GUAARDVARK_OPENAI_API_KEY` / `_BASE_URL` / `_MODEL` + 有効化 + 選択 | OpenAI/OpenRouter/Groq/Together/vLLM/Ollama `/v1`/Gemini(OpenAI互換) を共通クライアントで扱う |

OpenAI 互換プロバイダは OpenAI chat-completions プロトコルを話すため、同じコードで多くのエンドポイントに接続できます。

Settings → Model Management UI では、**マスタースイッチ**、プロバイダ切替（Ollama / Mistral / OpenAI-compatible）、およびモデルドロップダウン（Mistral / OpenAI-compatible）を提供（`/api/llm/provider/<provider>-model`）。ローカル保存されたチャットモデル選択は **`data/active_model.json`**（`active_model`）にも保持され、起動時にそのモデルが Ollama 側に存在すれば復元されます。

### 設定フロー
キーを **`.env`** に追加し、UI（Settings → Model Management）または API で設定:

```http
POST /api/llm/cloud-enabled {"enabled":true}      # マスタースイッチ（既定 OFF）
POST /api/llm/provider {"provider":"openai|mistral"}
POST /api/llm/provider/openai-model {"model":"..."}   # または /mistral-model
POST /api/llm/provider/test
```

- **Embeddings / RAG は常にローカル Ollama**（RAG ベクトルストア整合性のため）。
- ローカルへ戻すには `ollama` を選択するか、マスタースイッチを OFF。

### "Uncle Claude"（エスカレーション / ガーディアンモデル）
メインチャット LLM とは別の、任意のガーディアン/メンターレイヤ:

- **Escalation** — 難問やメッセージをクラウドにエスカレーション
- **Guardian** — 自己改善のコード変更を適用前レビュー
- **Advisor** — システムヘルスへの助言

設定キー: `GUAARDVARK_ESCALATION_PROVIDER` / `_MODEL` / `_API_KEY` / `_BASE_URL`（Anthropic または OpenAI 互換）。

モード:
- `manual`（既定）— 自動エスカレーションなし（ガーディアンレビュー + オンデマンド）
- `smart` — ローカル失敗時にエスカレーション
- `always` — すべての応答をエスカレーションプロバイダ経由

> 注: 本ガイド時点でチャットエンジンに配線済みなのは `always` と `smart`。`smart` はローカル失敗時の自動エスカレーションとして実装済みです。

---

## 5. エージェントと Agent Screen

**Agent Screen**（Agent Vision Control）は、エージェントが **実デスクトップを見て操作** するための機能です。

- **see-think-act ループ**（`agent_control_service.py`）:
  - 画面取得 → ビジョンモデル解析（例: `gemma4:e4b` の `box_2d` 座標）→ 判断 → 実行
- **アクション語彙**: `click`, `right_click`, `double_click`, `drag`, `hover`, `type`, `hotkey`, `scroll`, `move`, `wait`, `navigate`, `done`
- **ServoController**: 閉ループでクリック精度を補正（狙う→検証→補正）
- **Recipes**: ループをバイパスする決定論的アクション列
- **Self-calibration**: `servo_knowledge_store` + `servo_self_improvement` でモデルごとのスケール係数を最適化

**プラットフォーム注意:** 代表的なスクリーンエージェントは **Linux 仮想デスクトップ（Xvfb + XFCE）** 前提。macOS では `DESKTOP_AUTOMATION_ENABLED` が既定 `false` のため、明示有効化しない限り実質 OFF。**Raspberry Pi**（Linux/ARM）は相性が良い構成です。

---

## 6. スキルとレシピ

Guaardvark における「スキル」は **agent recipes**（`data/agent/recipes.json`）です。これは決定論的で再利用可能なアクション列であり、信頼性のために see-think-act をバイパスします。

```json
{
  "description": "Navigate to a domain-shaped URL in the current tab.",
  "triggers": ["^(?:navigate|go)\\s+to\\s+..."],
  "steps": [{ "action": "hotkey", "keys": ["ctrl", "l"] }, ...]
}
```

- **`triggers`**: 自然言語リクエストに対する正規表現マッチ
- **`steps`**: 実行順序を持つ決定論的アクション（hotkey / type / click / wait）
- 任意の **`success_proof`**: レシピ成功判定のためのビジョン条件
- CLI: `guaardvark recipes list | show | validate`

**区別が重要:**
- `Rules & Prompts` = プロンプト束（モデルに何を伝えるか）
- `recipes/skills` = 実行アクション
- `plugins` = 実行機能

---

## 7. メディア生成

| 領域 | 内蔵機能 |
|---|---|
| **画像** | オフライン diffusers（Z-Image Turbo, SDXL）+ ComfyUI/FLUX + バッチ + 顔/解剖補正 |
| **動画** | Wan 2.2, CogVideoX, LTX。解像度プリセット、フレーム補間、プロンプト強化、ComfyUI/オフラインフォールバック |
| **音楽/音声** | ACE-Step 楽曲生成、Stable Audio Open FX、神経音声（Chatterbox/Kokoro/Piper）、同意ゲート付き音声クローン |
| **アップスケーリング** | Real-ESRGAN 系、HAT-L、NMKD など。4K/8K、2パス、動画フレーム単位 |
| **編集** | 内蔵 Shotcut-lite タイムライン（video/text/audio レーン） |

**ComfyUI 経由の Z-Image（再ダウンロード回避）:**

```bash
GUAARDVARK_ZIMAGE_USE_COMFYUI=1   # .env
```

有効時は、通常の Z-Image 生成がオフラインダウンロード経路ではなく、実行中の ComfyUI（`z_image_turbo_bf16.safetensors`）を再利用します。モデル名は `GUAARDVARK_ZIMAGE_UNET/_CLIP/_VAE/_SAMPLER/_SCHEDULER` で上書き可能、CFG は `GUAARDVARK_ZIMAGE_CFG`（既定 1.0。ComfyUI KSampler は `cfg >= 1.0` 推奨。`cfg 0` は品質不良）。

---

## 8. ナレッジ: RAG・検索・メモリ

- **RAG** — ハイブリッド BM25 + ベクトル、AST 対応コードチャンク、プロジェクト別インデックス、エンティティ抽出、RAG 自動調査
- **`/search`** — インデックス済みドキュメントへの意味検索
- **`/ingest <path>`** — ファイル/ディレクトリを RAG に取り込み
- **Memory** — `AgentMemory` 長期記憶（重要度・信頼度・重み・ランク付き）

### Lesson Pearls（学習メモリ）
- **Pearl** = 「これは有効だった」を 👍 で記録（`ToolFeedback.positive=True`）
- **Lesson** = `POST /api/lessons/start` で開始し、途中で 👍、`POST /api/lessons/<id>/end` で終了
- **Distillation** = 複数 Pearl を 1 件の再利用可能な構造化 `AgentMemory` に圧縮（タイトル + 順序付きパラメータ化ステップ）
- 👍 自体は通常チャットでも常時記録されるが、構造化レッスン蒸留は Begin/End での括りが必要

---

## 9. Rules & Prompts

LLM に渡す文脈を宣言的に制御する **プロンプト束**。ルールは DB の `Rule` 行で管理:

- **`level`**（スコープ/優先）: `SYSTEM`, `PROJECT`, `CLIENT`, `USER_GLOBAL`, `USER_SPECIFIC`, `PROMPT`, `LEARNED`
- **`type`**: `PROMPT_TEMPLATE`, `QA_TEMPLATE`, `COMMAND_RULE`, `FILTER_RULE`, `FORMATTING_RULE`, `SYSTEM_PROMPT`, `OTHER`
- **`command_label`**（例: `/createfile`）, **`rule_text`**, **`target_models`**（既定 `__ALL__`）, **`is_active`**, **`project_id`**

**利用方法:** 実行時に `command_label` / level で取得し、プロンプトへ注入。

- **Command rules** — `get_active_command_rule(label, db, model)` によりコマンド固有のプロンプト化
- **SYSTEM rules** — チャットの system prompt に統合
- **QA templates** — 既定テンプレート

優先順: `SYSTEM (0) → LEARNED (1) → それ以外`

---

## 10. Swarm / Film Crew / Interconnector

### Swarm（並列コーディングエージェント）— `plugins/swarm/`
分離された **git worktree** ごとに最大 N エージェントを並列実行し、依存順マージ・競合検出・テスト検証を行います。

- **バックエンド**: `claude`（Claude Code, cloud）または `cline`（local, `ollama/gemma4:e4b`）
- **Flight Mode**: オフラインを自動検出しローカルへフォールバック
- 設定: `plugins/swarm/config.yaml`（`/swarm` UI から操作）
- CLI: `python plugins/swarm/swarm_cli.py launch <plan.md> [--flight-mode] [--max-agents N] [--auto-merge]`

> **命名注意:** `plugins/swarm/` は並列コーディング基盤であり、Film Crew そのものではありません。Film Crew は `backend/services/swarm/`（歴史的ディレクトリ名）上の逐次メディアパイプラインです。

### Film Crew（逐次メディアパイプライン）
5 役のエージェントがログラインから完成動画までを担当:
**Screenwriter → Casting（LoRA）→ Cinematographer → Storyboard → Editor**

実装: `/backend/services/swarm/` + `production_swarm_tasks.py`

### Interconnector（マルチマシン同期）
複数 Guaardvark インスタンス間のマスター/クライアント同期層。

- 同期対象: **チャット履歴**、**学習結果（self-improvement fixes）**、**画像**、**ファイル**、**バックアップ**、**ハードウェアプロファイル**
- **承認ゲート付き配信**、安全ディレクティブの配布

> **注意:** ライブな agent-to-agent 会話を作る層ではありません。データ同期 + 制御の層です。リアルタイム制御は各ノードの **HTTP API / MCP** と組み合わせて構成します。

---

## 11. 自己改善

自己改善エンジンはバグ検出から修正までを自動化しつつ、人間の承認ゲートを維持します:

```text
test → code_assistant agent dispatch → verify → broadcast (via Interconnector)
```

**モード:**
- **Scheduled** — 定期テスト（`pytest` の部分集合）→ 失敗解析 → 修正 → 再検証
- **Reactive** — 実行時例外で発火（500 ハンドラが traceback 抽出、file:line ごとにクールダウン）
- **Directed** — 手動タスク

**安全機構:**
- `self_improvement_enabled`, `codebase_locked`（+ lockfile）, `self_improvement_apply_enabled`
- 任意の **Uncle Claude** ガーディアンレビュー
- すべての修正は **PendingFix** としてステージングされ、UI で人間が承認/拒否
- 監査ログ: `SelfImprovementRun`, `changes_made`, JSONL

---

## 12. プラグインシステム

プラグインは `plugins/<id>/` 配下の機能パッケージで、`plugin.json`（id/type/port/`vram_estimate_mb`/endpoints/default config）を持ちます。

- **Discovery:** `PluginRegistry` が起動時に `plugins/` を走査。実行状態は `data/plugin_state.json`。
- **Lifecycle:** `PluginManager` が start/stop/health-check。service プラグインは CUDA 安全な `plugin_runner` サイドカー経由で `scripts/start.sh` 実行。
- **Types:** `service`（Discord, ComfyUI, Swarm）、`extension`、`tool`（エージェントツール追加）、`ui`
- **GPU/VRAM:** 重い GPU プラグイン同士や Ollama との競合を調停

**plugin と rule は別物**です。rule はプロンプト制御、plugin は実行機能です。

---

## 13. 連携: Discord / MCP / CLI

### Discord（`plugins/discord/`）
Guaardvark の前段として動く Discord ボット（port 8200）。

- スラッシュコマンド: `/chat`, `/claude`, `/imagine`, `/video`, `/search`, `/status`, `/voice`
- メンション応答、監督付きアウトリーチ、レート制限、管理者ロール、VIP DM
- `DISCORD_BOT_TOKEN` と招待先サーバが必要

### MCP
- **サーバとして:** `python -m backend.mcp`（stdio）で MCP クライアント（Claude Desktop、Cursor、Zed）にツール/リソースを公開。`backend/mcp/config.py` の **default-deny** ポリシーにより desktop/agent/system/browser 系は既定非公開。
- **起動統合:** `GUAARDVARK_START_MCP=1` で `start.sh` が MCP サーバ smoke test（`list-tools`）を実施し、クライアント設定スニペット（`GUAARDVARK_MCP_CLIENTS`）を表示。`stop.sh` は孤立 MCP プロセスを回収。
- **クライアントとして:** `mcp_connect` / `mcp_execute` で外部 MCP サーバを呼び出し。
- **注意:** サーバは **stdio 専用**。常駐共有デーモンではないため、リモート制御には HTTP API を使用。

### プロンプトからのツール呼び出し
LLM は構造化 `<tool_call>`（XML/JSON）でツールを要求:

```xml
<tool_call><tool>image_generate</tool><prompt>a red fox</prompt></tool_call>
```

`parse_tool_calls_xml` が name + params を抽出し、executor が `registry.get_tool(name)` で実行、観測結果として返します。

---

## 14. Lesson Pearls メモリシステム

Section 8 の要約:

- **Pearls** = 👍 で記録される「有効だった」瞬間
- **Lessons** = Begin/End で括った Pearl 群
- **Distillation** = Lesson を構造化・再利用可能な `AgentMemory`（タイトル + 手順）へ変換
- **Reconciler（Phase 5）** = セッション横断の `belief_update` を集約し、3 セッション以上で一致すると `self_knowledge_compact.md` や `recipes.json` の編集案を **PendingFix** として提案

---

## 15. 現実的なユースケース

| ユースケース | Guaardvark での実現 |
|---|---|
| **ローカル AI アシスタント** | ドキュメントに対するオフライン RAG チャット |
| **スクリーン自動化（ロボット）** | Agent Screen + see-think-act + recipes（Linux / Raspberry Pi が適性） |
| **メディア制作** | 画像/動画/音楽/音声生成 + アップスケーリング + 編集 |
| **AI コーディングスウォーム** | worktree 並列で大規模リファクタ |
| **マシン間学習の共有** | self-improvement の学習結果を Interconnector で配布 |
| **ソフトウェアロボット群** | ノードごとの API/MCP 制御 + Interconnector で全体運用 |
| **SEO / 競合分析コンテンツ** | 競合 URL を含む CSV からキーワード抽出し WordPress ページ生成へ反映 |
| **Discord 経由の操作** | Discord プラグイン経由でチャット/生成 |
| **AI エージェントからのプログラム制御（pi）** | HTTP API / CLI（pi ネイティブ MCP クライアントは将来予定） |

---

## 16. 注意（実例ベース）

- **macOS の port 5000 競合:** ControlCenter / AirPlay Receiver が `:5000` を使用。`.env` の `FLASK_PORT=5055` などへ変更後 `./start.sh`。
- **Python 3.12 必須:** ML スタックは 3.13/3.14 の wheel 未整備。macOS は `brew install python@3.12`（bootstrap でも自動実行）。
- **ComfyUI "not installed":** バンドル plugin の話であり、外部 ComfyUI は `COMFYUI_URL` が到達可能なら利用可。
- **ComfyUI 経由 Z-Image:** `GUAARDVARK_ZIMAGE_USE_COMFYUI=1`。KSampler cfg は `>= 1.0`。
- **Smart escalation:** 以前は「ローカル失敗時自動」が未実装だったが、現在は実装済み。
- **メインチャット LLM:** Ollama / Mistral / OpenAI-compatible をサポート。OpenAI/OpenRouter をメインチャットに使う場合は OpenAI-compatible プロバイダ + base URL を使用。
- **概念の柱を混同しない:** `Rules & Prompts`（プロンプト束）、`recipes/skills`（画面操作手順）、`plugins`（実行機能）、`MCP`（エージェントプロトコル）。


---

# 比較ガイド: Guaardvark と Hermes の選び方

自律コンピューティングの世界は、単純な LLM ラッパーから、より複雑なエージェントシステムへ移行しています。本章では、ローカルメディア/自動化ワークステーションである Guaardvark と、永続的・多チャネルなアシスタントである Hermes を比較し、設計思想と運用前提の違いを整理します。

## 1. 哲学的な分岐: "スタジオ" vs. "アシスタント"

Guaardvark は「1 台完結のスタジオ」です。高負荷なメディア制作とデスクトップ自動化を 1 台の強力なマシンに集約し、Flight Mode により完全オフライン運用を重視します。

Hermes は「ユーザーと共に成長するエージェント」です。クラウドや低電力サーバでの常時性を重視し、重いローカル処理よりも長期的ユーザーモデリングと多チャネル到達性を優先します。

### 比較プロファイル（要約）

| 指標 | Guaardvark | Hermes |
|---|---|---|
| 主体 | セルフホスト AI ワークステーション | 自己改善型自律エージェント |
| 配置モデル | ローカルファースト / Flight Mode | マルチプラットフォーム（Cloud, VPS, Termux） |
| UI パラダイム | 可視ダッシュボード + 仮想デスクトップ | TUI + メッセージングゲートウェイ |
| ハードウェア要件 | 高い（専用 NVIDIA GPU） | 柔軟（低価格 VPS 〜 GPU クラスタ） |

## 2. サーフェスとインターフェース

Guaardvark はビジョン座標制御、Hermes はテキストベースの構造化 UI/ブラウザ操作が中心です。

### インターフェース入口

#### Guaardvark の 4 サーフェス
1. **Web UI**: React/Vite ダッシュボード + ドラッグ可能 VNC ビューア。Linux 仮想デスクトップ（Xvfb + XFCE）をリアルタイム監視可能。
2. **CLI + REPL (`llx`)**: チャット中心操作、スクリプト、モジュラーコマンド実行。
3. **HTTP API**: およそ 90 の自動検出モジュールによるプログラム制御。
4. **MCP Server**: Claude Desktop や Cursor など外部クライアントへツールを公開。

#### Hermes の主入口
1. **TUI**: 高速コマンドライン環境（複数行編集、ツール出力ストリーミング）。
2. **Messaging Gateways**: Telegram/Discord/Slack/WhatsApp/Signal/Email。

### 技術的差異: Vision vs. Text

Guaardvark は Gemma4 vision などを使い、画面上のピクセル座標を出力して操作します。ServoController により「移動 → 検証 → 補正」を繰り返し、一般的な Linux GUI アプリを扱います。

Hermes は主にアクセシビリティツリーやクラウドブラウザ（Browserbase など）を通じ、DOM の参照 ID を使って構造的に操作します。データ中心タスクには高速ですが、非 Web GUI 自動化には向きにくい場面があります。

## 3. Guaardvark の強み: Film Crew メディアパイプライン

Guaardvark は単発プロンプトではなく、Sequential Parallelism に基づく制作工程で高品質メディアを生成します。

### Film Crew の逐次パイプライン
1. Screenwriter: 台本とショット分解
2. Casting: LoRA/キャラクタ割当
3. Cinematographer: 画角・レンズ・動き設計
4. Storyboard: キーフレーム生成
5. Editor: タイムライン編集で仕上げ

### ローカル生成とハードウェア負荷

| モデル系 | 主用途 | VRAM 要件 |
|---|---|---|
| Wan 2.2 | Text/Image-to-Video | 16GB（preflight） |
| CogVideoX | Text-to-Video | 16GB - 20GB |
| LTX (Distilled) | 長尺動画 | 14GB |
| ACE-Step | 楽曲生成 | 10GB |

## 4. Hermes の強み: スケジュール自動化とゲートウェイ到達

Hermes は常時性と導入障壁の低さに強みがあります。

- **Nous Portal**: API キー管理の手間を抑え、300+ モデルとツールゲートウェイを単一契約で提供。
- **Cron 自動化**: 自然言語で定時タスクを設定し、通知配信まで無人実行。
- **Serverless Persistence**: Modal/Daytona などで休止・復帰を行い、常時稼働コストを抑制。
- **Messaging Continuity**: Telegram などで開始した作業を別デバイスで継続可能。

## 5. 学習ループ: Lesson Pearls vs. Honcho Dialectic

両者とも自己改善しますが、対象が異なります。

### Guaardvark（タスク学習）
- 👍 された操作を Pearl として記録
- Lesson として束ね、ローカル LLM が構造化 Recipe に蒸留
- 結果: 手順として再実行可能なスキルを獲得

### Hermes（ユーザー学習）
- Honcho による二重ピアモデル（User peer / AI peer）
- 結果: 端末横断で好みや文脈を継続適用

### メモリ比較

| 機能 | Guaardvark（Lesson Pearls） | Hermes（Honcho） |
|---|---|---|
| 取得方法 | ポジティブフィードバック（👍） | 対話ターン/観測 |
| 想起形式 | パラメータ化された実行手順 | ピアカード注入 + セマンティック検索 |
| 主眼 | タスク学習（Skills） | ユーザー学習（Identity） |

## 6. 最終判断マトリクス

### Guaardvark を選ぶべき場合
- 機密データのためにオフラインファーストが必要
- 非 Web ソフトを含む Linux デスクトップ自動化が必要
- 16GB+ VRAM の NVIDIA GPU があり、4K 動画/音楽/神経音声をローカル生成したい

### Hermes を選ぶべき場合
- Telegram などで自動レポート配信する常駐 bot が必要
- $5 VPS、Termux、サーバレスなど弾力的基盤で運用したい
- Nous Portal で高性能モデル/ツールへ迅速アクセスしたい

### 初学者向けアーキテクチャ原則
1. **ローカル決定性 vs クラウド弾力性**: Guaardvark は厚いクライアント、Hermes は基盤非依存。
2. **視覚操作 vs メッセージ常在性**: Guaardvark は UI を見てクリック、Hermes はチャットアプリ常在で調査。
3. **手順蒸留 vs ユーザーモデル**: Guaardvark は手順を学び、Hermes はユーザーを学ぶ。

ローカル特化の Guaardvark でも、常在型の Hermes でも、いずれも自律・自己改善システムの最前線に触れる選択です。
