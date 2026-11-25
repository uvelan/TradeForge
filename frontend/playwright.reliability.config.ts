import {defineConfig} from '@playwright/test';
import {browserConfig} from './playwright.config';

const phase=process.env.TRADEFORGE_DRILL_PHASE;
if(!process.env.TRADEFORGE_PAIR_STORAGE||!phase)throw Error('Run scripts.verify_local_market_workspace');
const match=phase==='pair'?'recorded-pair':phase==='reopen'?'reopen':'reliability';
const config=browserConfig('research_backend.tests.recorded_browser_app:app',process.env.TRADEFORGE_PAIR_STORAGE);
export default defineConfig({...config,testDir:'./comparison-e2e',testMatch:'**/'+match+'.spec.ts',
  outputDir:'./recorded-test-results',use:{...config.use,serviceWorkers:'block',trace:'off',screenshot:'off',video:'off'}});
