# Code Search (zvec-grep) — trial plugin

[zvec-grep](https://github.com/zvec-ai/zvec-grep) (`zg`, Apache 2.0) indexes a
checkout once and answers three ways: by meaning, by keyword, or by ripgrep.
Guaardvark already runs a vector + full-text hybrid over documents; what the
agents lack is that shape over source code, where `search_code` is a grep.
This plugin exposes zg's two agent tools (`zvec_grep_search`,
`zvec_grep_rg`) to the chat, the code agent and the swarm through the MCP
client, so the question "does the agent finish with fewer tool calls and
tokens" can be measured before anything becomes a default.

## What stays on the machine

- The embedding model is local (`local/potion-code-16m-v2`, a static
  Model2Vec model, CPU, no GPU). It is downloaded once by `scripts/setup.sh`,
  the plugin's Install step, into `cache/models`. No script that runs at
  startup downloads anything.
- zg's remote embedding providers are never configured. `scripts/common.sh`
  unsets every credential and endpoint variable zg would read, and no script
  passes `--allow-remote`.
- The daemon listens on loopback only. Indexes live in `<root>/.zvec-grep`,
  ignored by git.

## How the chat reaches it

The model never sees zg's own tool. `search_codebase` (`backend/tools/code_search_tools.py`)
is the one name for "search the code": it supplies the workspace root itself (the request's
`project_root`, else the Guaardvark root), asks zg when the `zvec_grep` MCP server is
connected, and falls back to the repository's regex search when it is not, so it never
advertises more than the machine can do. Questions about the source keep it in the prompt
(`_pin_code_search_tools` in the chat engine) with one system line saying the code is already
indexed, because the persona's "ask for a folder path" lesson otherwise wins. Its result
budget is declared on the tool (`observation_chars`); the engine's default of 500 characters
is right for chatty tools and useless for a search.

zg honours `.gitignore` but not `.git/info/exclude`; `scripts/index.sh` passes the exclude
file and skips `data/`, `logs/`, `pids/` and the private workspace directory, so the index
holds what the repository holds.

## Use

```bash
plugins/zvec_grep/scripts/setup.sh              # Install: npm ci + model fetch
plugins/zvec_grep/scripts/index.sh              # index this checkout (incremental)
plugins/zvec_grep/scripts/start.sh              # daemon on 127.0.0.1:7999
cp plugins/zvec_grep/mcp_servers.example.json data/config/mcp_servers.json   # or merge
```

Paths in the example are relative to the Guaardvark root, which is the
backend's working directory. Restart the backend after adding the entry; the
MCP client connects the server and its tools appear beside the built-in ones.

## Trial protocol

`docs/local-workspace-only/zg_trial.py` (not shipped) sends the same set of
code questions through `/api/chat/unified` with the server attached and with
it absent, and records tool calls, tokens and wall time per question. The
decision to keep the plugin, and any claim about it, follows those numbers.
