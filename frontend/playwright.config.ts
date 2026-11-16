import { defineConfig } from "@playwright/test";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

function port(value: string){
  if(!/^\d{1,5}$/.test(value)||Number(value)<1||Number(value)>65535)throw Error("Invalid test port");
  return Number(value);
}
const apiPort=port(process.env.TRADEFORGE_E2E_API_PORT??"8000");
const uiPort=port(process.env.TRADEFORGE_E2E_UI_PORT??"4180");
const baseURL=`http://127.0.0.1:${uiPort}`;

export default defineConfig({
  use:{baseURL,trace:"retain-on-failure",screenshot:"only-on-failure",video:"retain-on-failure"},
  workers:1, // Suites share one isolated server and may write the same drawing scope.
  testDir: "./e2e",
  webServer: [
    {
      command: `.venv\\Scripts\\python.exe -m uvicorn tradeforge.api:app --host 127.0.0.1 --port ${apiPort}`,
      cwd: "..",
      env: { TRADEFORGE_STORAGE: mkdtempSync(join(tmpdir(), "tradeforge-e2e-")) },
      url: `http://127.0.0.1:${apiPort}/api/health`,
    },
    {command:`npm run dev -- --config e2e/vite.config.ts --port ${uiPort}`,url:baseURL},
  ],
});
