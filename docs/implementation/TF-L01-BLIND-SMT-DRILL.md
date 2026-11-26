# TF-L01 first increment: blind SMT exercise

In Learning Journal, the synthetic-only drill presents a single M5 comparison cutoff. The browser does not receive the next candle, answer, or review until the learner submits a valid choice and reasoning. It is an educational simplification, not production SMT evaluation or order advice.

The hypothesis and tagged chart journal entry are committed together in an additive SQLite table under the existing single-user storage; the exercise has a versioned ID. The same submission can safely be retried, while a different submission for the completed version receives HTTP 409. On restart, the recorded answer and linked journal entry are restored, and the existing Markdown view is rebuilt from SQLite. Existing journal records are unchanged. A failed attempted commit does not leak the answer or leave a partial journal entry.

Focused HTTP verification: `python -m pytest -q research_backend/tests/test_learning_drill.py research_backend/tests/test_workflows.py`. Browser scenario: `npm --prefix frontend run e2e -- learning-drill.spec.ts` with the standard Windows harness. For rollback, revert the application commit without dropping the new table; the standard Learning Journal still reads the historical chart entry.

This is one drill, not the complete Learning Coach. Full syllabus, multiple lessons/attempts, proficiency tracking, spaced review, voice, and shared chart replay integration are remaining TF-L01 scope.
