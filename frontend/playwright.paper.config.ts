import {defineConfig} from '@playwright/test';
import {browserConfig} from './playwright.config';
if(!process.env.TRADEFORGE_PAPER_STORAGE)throw Error('Run scripts.verify_plan_paper');
const config=browserConfig('research_backend.tests.recorded_browser_app:app',process.env.TRADEFORGE_PAPER_STORAGE);
export default defineConfig({...config,testDir:'./paper-e2e',outputDir:'./paper-test-results',use:{...config.use,trace:'off',video:'off',screenshot:'off'}});
