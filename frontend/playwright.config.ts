import { defineConfig } from "@playwright/test";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

function localPort(value: string) {
  if (!/^\d{1,5}$/.test(value) || Number(value) < 1 || Number(value) > 65535) {
    throw new Error("Expected a valid loopback port");
  }
  return Number(value);
}

const apiPort = localPort(process.env.TRADEFORGE_E2E_API_PORT ?? "8000");
const frontendPort = localPort(process.env.TRADEFORGE_DEV_PORT ?? "4180");
const apiUrl = `http://127.0.0.1:${apiPort}`;
const frontendUrl = `http://127.0.0.1:${frontendPort}`;

export default defineConfig({
  use: { baseURL: frontendUrl },
  testDir: "./e2e",
  webServer: [
    {
      command: `.venv\\Scripts\\python.exe -m uvicorn tradeforge.api:app --host 127.0.0.1 --port ${apiPort}`,
      cwd: "..",
      env: { TRADEFORGE_STORAGE: mkdtempSync(join(tmpdir(), "tradeforge-e2e-")) },
      url: `${apiUrl}/api/health`,
    },
    { command: "npm run dev", url: frontendUrl, env: { TRADEFORGE_DEV_API: apiUrl } },
  ],
});
