import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `npm run build` writes the bundle into the Python package, where montivo-ui serves it (committed, so judges need no Node)
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../montivo/server/static", emptyOutDir: true },
  server: { proxy: { "/api": "http://127.0.0.1:8765" } },
});
