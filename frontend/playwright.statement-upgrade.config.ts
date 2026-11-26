import {mkdtempSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {execFileSync} from 'node:child_process';
import {browserConfig} from './playwright.config';

const storage=mkdtempSync(join(tmpdir(),'tradeforge-e2e-statement-'));
const root=resolve('..');
execFileSync(join(root,'.venv','Scripts','python.exe'),[
  join(root,'research_backend/tests/statement_v1_probe.py'),'--directory',join(storage,'research')
]);
export default {...browserConfig('research_backend.tests.evaluator_browser_app:app',storage),
  testDir:'./statement-e2e'};
