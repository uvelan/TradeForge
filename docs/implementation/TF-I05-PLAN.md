# TF-I05 plan

Base ba1284784013354989f7e2620766f798124cb807 is merged PR #6. Search found only the existing manual FVG drawing, which is independent of the indicator. Reuse the ordered closed-evidence evaluator, IANA continuity checks, historical archive loader, and EventPage. No schema migration.

FVG v1 uses strict A.high < C.low or A.low > C.high across three contiguous closed observations. Any discontinuity clears the pattern and ATR warm-up. Configured verified calendar closures preserve existing zones only. Minimum rules are inclusive and independent, in price units, bps of the lower boundary, declared tick size or Wilder ATR as of C. Missing tick metadata suppresses tick-filtered formations. Optional directional middle-body displacement has its own version and bps unit.

Evaluate live zones before detecting a new formation. Penetration is monotone toward the opposite edge under wick or close observation. A zero-depth touch adds nothing. A strict close past the far edge invalidates. If the candle is entirely beyond the zone, report an unobserved gap-through and retain the last depth. A fully observed fill with no invalidating close is fully mitigated. Missing open evidence invalidates live zones. Age counts real accepted observations.

Use a threshold heap per direction for mitigation and an expiry heap. Overlap annotations are observational, scoped to live intersecting zones at formation, limited to a documented sample, and never merge identities. Each immutable event records the eligibility time and evidence fingerprint. Independent bounded formation, revision, and current pages share a cutoff-bound fingerprint. Current comes directly from live state.

The chart uses the current projection and bounded terminal markers. History and selection are separate from rendering and preserve scope cancellation. Verify exact thresholds, both directions, closures, data gaps, archive integrity, replay prefixes, old-active truncation, UI interactions, and performance at caller and archive limits.
