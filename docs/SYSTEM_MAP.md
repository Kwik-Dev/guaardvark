# System Map — How To Use

Guaardvark's **System Map** is an *X-ray of the running codebase* — a live
"constellation" graph of your source tree. Every module is a node, every import
dependency is an edge, and a side panel ranks **findings** (real, actionable
issues). It's built by `backend/services/system_mapper/` and rendered in the
browser — see the README there for how it's generated, and the findings taxonomy.

> The map is a **read + act** tool: you can fix bugs it finds, hand issues to the
> self-improvement agent, and map blast radius before changing code.

---

## 1. Access it

Open the **System Map** page in the web UI. It fills the viewport below the top
bar:
- a large **canvas** (the constellation) in the center,
- a **Findings / Activity** panel on the right,
- a **legend** strip and HUD (search, reset, refresh) across the top.

---

## 2. What you're looking at

| Element | Meaning |
|---------|---------|
| **Node** | one source module (a `.py` file or frontend path) |
| **Edge** | an import dependency between two modules |
| **Node size** | how many other modules import it (more = bigger = more central) |
| **Node hue** | which section it belongs to (API, Services, Utils, Tools, Tasks, Frontend, Plugins) |
| **Node opacity** | lifecycle (`active`, `dormant`, `archived`, `test`, …) |
| **Pulsing glow** | a module doing something right now (live tool call / search hit / finding) |

Click a **legend chip** to spotlight an entire section (matching nodes glow and
the rest dim). Click again to remove it.

---

## 3. Navigation & controls

| Action | How |
|--------|-----|
| **Pan** | drag (left, middle, or Shift+left) |
| **Zoom** | mouse wheel (or pinch) |
| **Center on a module** | search (`/`) then Enter, or click a finding's "locate" |
| **Reset view** | press `R` or click the reset button (top-right) |
| **Inspect a module** | hover to see it highlighted; click to keep it selected |

**Keyboard shortcuts:** `/` (or `Cmd/Ctrl+K`) focuses search · `Esc` clears ·
`R` resets the view. *(Space doesn't pause here — that's the Video Editor.)*

---

## 4. The Findings panel (the actionable part)

The panel's default tab shows **ranked findings**. Each entry has a **severity
filter** (`Actionable`, `Critical`, `All`) and a colored severity bar, plus a
**summary and the file paths** it touches.

Common finding *kinds* and what they mean:

| Kind | Severity | What it usually means |
|------|----------|------------------------|
| `url-collision` | high | Two files register the same URL — the second **silently shadows** the first (a real bug) |
| `ghost-api-caller` | medium | Frontend calls `/api/x` with **no backend route** — dead/broken feature |
| `ghost-endpoint` | low | Backend route with **no frontend caller** — possibly dead code or just a public API |
| `import-cycle` | medium/low | A→B→A imports — brittle under refactor |
| `over-coupled` | medium | Module participates in 5+ cycles — refactor hotspot |
| `unwired-tool` / `unregistered-tool` | high | A tool is listed but the agent can't reach it |
| `untested-module` | low | No `tests/test_<name>.py` exists |
| `dormant-module` | low | No module imports it — possibly dead code |
| `backup-artifact` | low | `_BACK` / `_archive` / backup files left in the tree |

### Act on a finding
- **Click it** → the camera flies to the offending module and shows its details.
- **Dispatch** (the send icon) → hands the finding to the **self-improvement
  agent**, which proposes a real code diff. It lands as a **PendingFix** for you
  to review/approve in **Settings**.
- **Dismiss** (the eye-off icon) → acknowledge and stop showing it (undo-able).
  Dismissals are persisted and survive re-analysis.

> This is the "make the map fix itself" loop: dispatch high-severity findings,
> review the agent's patches, apply what you like.

---

## 5. Inspector / detail view

When you hover or select a node, the right panel switches to its **detail view**:
- the module's **section**, **lifecycle**, and **importer count** chips,
- any **findings** attached to that module,
- press `Esc` to clear.

Use this to gauge **blast radius** — select a module and the constellation
**spotlights its neighbors**, so you can see exactly who depends on it before
refactoring.

---

## 6. Activity tab (watch the system work)

Switch to **Activity** to see live tool calls flowing through the chat. Each entry
shows the tool and the module it resolved to; the matching **module pulses** in
the constellation. It's live observability — "what is the agent touching right
now?"

---

## 7. Overlay toggles

Two optional overlays (both **off by default**) on the legend strip:
- **Ghost endpoints** `(n)` — dashed orange rings mark modules owning an orphaned
  route.
- **Tool graph** `(n)` — solid green rings mark modules a registered LLM tool
  resolves to; dashed green edges link them to the chat engine.

---

## 8. Refreshing the data

The snapshot is **disk-cached for 5 minutes**. Click **refresh** to force a
re-compute (that's `?refresh=1` on `GET /api/system-map/snapshot`). The HUD shows
how old the current cache is, so you know when you're looking at fresh data.

---

## 9. Practical workflows

1. **Bug-hunting:** open Findings → filter to `Actionable` → click any
   `ghost-api-caller` or `url-collision` — the map flies you to the exact module.
2. **Cleanup-as-a-queue:** dispatch the high-severity findings, let the agent
   propose fixes, review the `PendingFix`s in Settings.
3. **Before a refactor:** search/select the module you want to change and read its
   importer count + neighbors (blast radius).
4. **Orientation in an unfamiliar repo:** read the legend + hub sizes, then
   search for a file you care about.

---

## 10. Limitations to keep in mind

- **Static analysis** — import edges come from AST/regex, so dynamic imports
  (`lazy(() => import('./Foo'))`) are missed.
- **Module-level only** — it graphs module→module imports, not function→function
  calls.
- **Python + JavaScript** only today; the discoverer shape is pluggable for other
  languages.

---

## 11. Beyond the web UI

The same engine powers a **CLI** one-shot report:
```bash
python -m backend.services.system_mapper /path/to/codebase --out /tmp/out
# → system_map.json, system_map.md (human report), system_map.mmd (Mermaid cycles)
```
And it can map any codebase via the API: `GET /api/system-map/snapshot?root=<path>`.
