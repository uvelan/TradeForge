# Runtime and command notes

Local Windows with Python 3.13.14 and Node 24.14.0. Vitest and Playwright install from the frontend lockfile. Backend verification uses the already installed requirements lockfile environment.

The first full browser invocation was stopped during the drawing cases so the verification could run against the corrected evaluator. Its partial output is preserved in browser-interrupted.txt. The earlier focused evaluator browser failure is in browser-first.txt. The initial bullish fixture invocation failed because the fixture signature did not expose the direction argument. The corrected run is in bullish-fixture.txt and the final verification runs it as part of the full backend suite.

The first hosted workflow pair used code SHA 3bc32802bf629b406ec26f803fb9ef7bedea0414. Both failed the Windows archive-isolation test because Windows returned a DOS short path for its temporary directory while Python configuration used a resolved long path. The retained excerpts show the original assertion. The production archive was not involved.
