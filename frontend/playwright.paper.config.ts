import {defineConfig} from '@playwright/test';
import {browserConfig} from './playwright.config';
if(!process.env.TRADEFORGE_PAPER_STORAGE)throw Error('Run scripts.verify_plan_paper');
const config=browserConfig('research_backend.tests.recorded_browser_app:app',process.env.TRADEFORGE_PAPER_STORAGE);
export default defineConfig({...config,testDir:'./paper-e2e',testMatch:process.env.TRADEFORGE_PAPER_PHASE==='integrity'?'review-integrity.spec.ts':process.env.TRADEFORGE_PAPER_PHASE?.startsWith('package-')?'historical-package.spec.ts':process.env.TRADEFORGE_PAPER_PHASE==='month'?'month-replay.spec.ts':process.env.TRADEFORGE_PAPER_PHASE?.startsWith('review-')?'decision-reviews.spec.ts':'plan-paper.spec.ts',outputDir:'./paper-test-results',use:{...config.use,trace:'off',video:'off',screenshot:'off'}});
