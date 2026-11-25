import {defineConfig} from '@playwright/test';
import {browserConfig} from './playwright.config';

const config=browserConfig('research_backend.tests.comparison_browser_app:app',process.env.TRADEFORGE_PAIR_STORAGE);
export default defineConfig({...config,testDir:'./comparison-e2e',testMatch:process.env.TRADEFORGE_PAIR_REOPEN==='1'?'**/reopen.spec.ts':'**/recorded-pair.spec.ts',outputDir:'./recorded-test-results',
  use:{...config.use,serviceWorkers:'block',trace:'off',screenshot:'off',video:'off'}});
