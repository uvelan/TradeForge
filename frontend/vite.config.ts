import { defineConfig } from "vite";

const apiTarget = process.env.TRADEFORGE_DEV_API ?? "http://127.0.0.1:8000";

export default defineConfig({
  server: {
    port: Number(process.env.TRADEFORGE_DEV_PORT ?? 4180),
    proxy: { "/api": apiTarget, "/research/api": apiTarget },
  },
  build: { rolldownOptions: { input: { terminal: "index.html", catalog: "catalog.html" } } },
});
