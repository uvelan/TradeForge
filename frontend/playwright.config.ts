import { defineConfig } from "@playwright/test";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

export default defineConfig({
  testDir: "./e2e",
  webServer: [
    {
      command: ".venv\\Scripts\\python.exe -m uvicorn tradeforge.api:app --host 127.0.0.1 --port 8000",
      cwd: "..",
      env: { TRADEFORGE_STORAGE: mkdtempSync(join(tmpdir(), "tradeforge-e2e-")) },
      url: "http://127.0.0.1:8000/api/health",
    },
    { command: "npm run dev", url: "http://127.0.0.1:4180/" },
  ],
});
