# Decisions

## TF-D01

Retain chart instance ownership, time/price conversion, DrawingLayer and the existing versioned scene API. Add a small primary-plot pointer adapter with gesture ownership, pointer capture, cancel/lost-capture teardown and atomic anchor commits. Restore the unchanged chart gesture paths when unarmed. Keep previews on the top primitive layer so ordinary snapshot capture excludes them.

Study the upstream v5 manager and OrderCraft canvas, retaining their separation of placement state, coordinate projection and persistence as a design reference. A direct dependency would require mapping a different tool and persistence model and broader lifecycle work. This iteration uses independently implemented TradeForge code and the installed Lightweight Charts v5 API. No upstream code or assets were copied.

A direct golden-shape stroke click selects the existing drawing. The existing toolbox then edits individual anchors using chart placement. Persisted-body dragging, resize handles, and broader hit testing belong to TF-D02.
