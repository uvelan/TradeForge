# TF-U01: keep chart evidence attached to the view that saved it

The Market Terminal associates a completed chart capture only with the current
committed view generation and newest save. The view includes instrument,
timeframe, source and loaded feed identity, requested archive range and refresh,
solo/comparison layout, and frozen replay session and cutoff. Normal streamed
candle refreshes within an unchanged feed do not erase an attachment. A new
primary request must finish, and any requested comparison must settle, before
the current view permits another save. Comparison and secondary panes fence
loads and polling by request instance, including a return to the same key.

Pending requests from obsolete views may finish and preserve their immutable
snapshot under the original server metadata. Neither their IDs nor their
status/errors are consumed by the current view. This also applies when leaving
and then returning to an identical key. Existing snapshot APIs, prior saved
artifacts, and the standalone Signal Explorer capture flow are unchanged.

The browser fixtures delay real snapshot responses across timeframe, source,
same-key round trips, and replay cuts, check a stale failure on a comparison
change, and let a current save beat the old reply. They inspect real plan and
journal links. An additional paired-pane fixture holds both the alternate
request and the fresh request for the original symbol, confirming the old
pane cannot be saved. This fixes evidence ownership but does not close the broader
TF-U01 panel layout or 100-cycle soak work.
