# Decisions

## TF-D01

Retain chart instance ownership, time/price conversion, DrawingLayer and the existing versioned scene API. Add a small primary-plot pointer adapter with gesture ownership, pointer capture, cancel/lost-capture teardown and atomic anchor commits. Restore the unchanged chart gesture paths when unarmed. Keep previews on the top primitive layer so ordinary snapshot capture excludes them.

Study the upstream v5 manager and OrderCraft canvas, retaining their separation of placement state, coordinate projection and persistence as a design reference. A direct dependency would require mapping a different tool and persistence model and broader lifecycle work. This iteration uses independently implemented TradeForge code and the installed Lightweight Charts v5 API. No upstream code or assets were copied.

A direct golden-shape stroke click selects the existing drawing. The existing toolbox then edits individual anchors using chart placement. Persisted-body dragging, resize handles, and broader hit testing belong to TF-D02.

## 2026-11-24: drawing lifecycle and integration boundaries

TF-D02 extended the existing versioned scene instead of introducing a second persistence path. Direct edits use the primary plot; one accepted move or resize writes one revision and can be undone. Canceling leaves the scene unchanged; an optimistic conflict requires a reload before further writes. See the [merged review evidence](TF-D02-REVIEW.md).

Keep the master-plan work in the single [draft integration PR](https://github.com/uvelan/TradeForge/pull/37). Keep "included in the draft", "merged", and "release approved" distinct. Passing tests for an increment do not close the remaining acceptance or authorization gates in the [feature matrix](plan-integration-evidence/FEATURE-MATRIX.md). The [status ledger](STATUS_LEDGER.md#2026-11-24-superseding-status) records the original evidence without applying it to a different commit.
