import { defineConfig } from "vite";

export default defineConfig({
  server: {
    port: 4180,
    proxy: { "/api": "http://127.0.0.1:8000", "/research/api": "http://127.0.0.1:8000" },
  },
  build: { rolldownOptions: { input: { terminal: "index.html", catalog: "catalog.html" } } },
});
