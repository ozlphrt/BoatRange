import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  // GitHub Pages serves project sites below /<repository>/ rather than at
  // the domain root. Local development keeps the normal root path.
  base: process.env.GITHUB_ACTIONS ? "/BoatRange/" : "/",
  plugins: [react()],
  server: {
    port: 2343,
    strictPort: true,
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
  // maplibre-gl bundles its own web worker via new Worker(new URL(...)) —
  // Vite's esbuild dep pre-bundler mangles that reference (it looks for a
  // pre-optimized "maplibre-gl-worker.mjs" that never gets emitted, so the
  // worker silently fails to start and the map never paints). Excluding it
  // from optimizeDeps lets Vite serve it as real ESM instead.
  optimizeDeps: {
    exclude: ["maplibre-gl"],
  },
});
