import {defineConfig} from '@playwright/test';
import {browserConfig} from './playwright.config';

const config=browserConfig('research_backend.tests.smt_pin_browser_app:app',process.env.TRADEFORGE_PAIR_STORAGE);

export default defineConfig({
  ...config,
  testDir:'./comparison-e2e',
  testMatch:'**/smt-pin.spec.ts',
  outputDir:process.env.TRADEFORGE_SMT_PIN_RESULTS??'./recorded-test-results',
  use:{...config.use,serviceWorkers:'block',video:'off'},
});
