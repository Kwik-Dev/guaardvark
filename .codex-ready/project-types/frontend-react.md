# Project Type: React / Vite SPA Frontend

The frontend is a React SPA in `frontend/`, built with Vite and served via `vite
preview` in production. It talks to the Flask backend over REST (Axios) and realtime
(socket.io-client).

## Stack

- React function components + hooks, **MUI v5** component library.
- **Zustand** for global state; React Context for layout/status.
- Monaco editor, reactflow/d3-force for graphs, `@novnc/novnc` for VNC.

## Commands (run in `frontend/`)

```bash
npm run dev            # vite dev server (--host --port 5173)
npm run build          # production build
npm run preview        # serve production build
npm run lint           # eslint, --max-warnings 0 (REQUIRED, strict)
npm run test           # vitest (jsdom)
npm run test:watch
npm run test:coverage
```

## Critical conventions

- Frontend reads the **repo-root `.env`**, not `frontend/.env`. `VITE_ALLOWED_HOSTS`
  gates LAN access; `VITE_FRONTEND_URL` is added to backend CORS + SocketIO allowlists.
- `vite.config.js` repeats the host allowlist plus `/api` and `/socket.io` proxy under
  a `preview:` block — the preview server does **not** share the `server:` block.
- Keep separate component/unit tests (fast, per-edit) from browser-level end-to-end
  checks (run after a coherent batch).
- Respect existing lint tooling; `npm run lint` has `--max-warnings 0` so it fails on
  any warning — keep code clean.

## Where things live

- `frontend/src/api/` — Axios API client layer.
- `frontend/src/components/` — reusable components.
- `frontend/src/pages/` — route-level pages.
- Frontend tests are `src/**/*.{test,spec}.{js,jsx}` under vitest/jsdom.
