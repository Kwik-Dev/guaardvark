import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { NodeGlobalsPolyfillPlugin } from "@esbuild-plugins/node-globals-polyfill";
import { NodeModulesPolyfillPlugin } from "@esbuild-plugins/node-modules-polyfill";
import rollupNodePolyFill from "rollup-plugin-polyfill-node";

const FLASK_PORT = process.env.FLASK_PORT || process.env.FLASK_RUN_PORT || 5000;
const VITE_PORT = process.env.VITE_PORT || 5173;

// Extra hostnames/IPs allowed to reach the dev server (comma-separated).
// Set VITE_ALLOWED_HOSTS to your LAN IP (e.g. "192.168.1.108") to reach the UI
// from another device, or "all" to skip the host check entirely (trusted nets only).
const EXTRA_ALLOWED_HOSTS = (process.env.VITE_ALLOWED_HOSTS || "")
  .split(",")
  .map((h) => h.trim())
  .filter(Boolean);
const ALLOWED_HOSTS = EXTRA_ALLOWED_HOSTS.includes("all")
  ? "all"
  : ["localhost", "127.0.0.1", ".local", ...EXTRA_ALLOWED_HOSTS];

// Shared by both the dev (`server`) and production-preview (`preview`) servers.
// The frontend calls a relative "/api" and connects the socket to the page
// origin, so whichever server serves the page must proxy these to Flask.
// `xfwd: true` forwards the originating client IP as X-Forwarded-For — the backend
// auth_guard relies on it to still recognize a LAN device (proxied via loopback)
// as remote, so proxying the UI does not silently bypass the host check.
const PROXY = {
  "/api": {
    target: `http://127.0.0.1:${FLASK_PORT}`,
    changeOrigin: true,
    secure: false,
    xfwd: true,
  },
  "/socket.io": {
    target: `http://127.0.0.1:${FLASK_PORT}`,
    changeOrigin: true,
    secure: false,
    ws: true,
    xfwd: true,
  },
};

export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/test/setup.js',
    include: ['src/**/*.{test,spec}.{js,jsx,ts,tsx}'],
    coverage: {
      reporter: ['text', 'json', 'html'],
      exclude: ['node_modules/', 'src/test/'],
    },
  },
  optimizeDeps: {
    include: [
      '@emotion/react',
      '@emotion/styled',
      '@mui/material',
      '@mui/material/Tooltip',
      '@mui/material/Popper',
      '@popperjs/core',
    ],
    esbuildOptions: {
      define: {
        global: "globalThis",
      },
      plugins: [
        NodeGlobalsPolyfillPlugin({
          buffer: true,
          process: true,
          global: true,
        }),
        NodeModulesPolyfillPlugin(),
      ],
    },
  },
  build: {
    chunkSizeWarningLimit: 1000,
    rollupOptions: {
      plugins: [rollupNodePolyFill()],
      output: {
        manualChunks: {
          vendor: ['react', 'react-dom'],
          mui: ['@mui/material', '@mui/icons-material', '@emotion/react', '@emotion/styled'],
          routing: ['react-router-dom'],
          api: ['axios', 'socket.io-client'],
          utils: ['zustand', 'react-grid-layout', 'react-markdown', 'react-syntax-highlighter']
        }
      }
    },
    sourcemap: false,
    minify: 'terser',
    terserOptions: {
      compress: {
        drop_console: false,
        drop_debugger: true,
        pure_funcs: ['console.debug'],  // Only strip debug, keep error/warn/log
      },
    },
  },
  server: {
    host: '0.0.0.0',
    port: parseInt(VITE_PORT),
    strictPort: true,
    allowedHosts: ALLOWED_HOSTS,
    proxy: PROXY,
  },
  // `start.sh` serves the production build via `vite preview`, which does NOT
  // share the `server:` block above — so host allowlist + API/WS proxy must be
  // repeated here, or LAN clients get "Blocked request" and /api + sockets 404.
  preview: {
    host: '0.0.0.0',
    port: parseInt(VITE_PORT),
    strictPort: true,
    allowedHosts: ALLOWED_HOSTS,
    proxy: PROXY,
  },
});
