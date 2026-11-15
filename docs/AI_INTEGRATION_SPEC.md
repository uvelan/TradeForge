# Manual AI review

Planned exports pin the plan version, context cutoff, dataset and cost-model versions, evidence and metric references, and artifact checksums. Pre-trade and in-trade snapshots filter unavailable data in application code. Quick exports are Markdown, with JSON, CSV, chart artifacts and ZIP for deeper review.

An importer validates a versioned response schema, exact package and plan identity, and independent evidence and metric references before persisting the original response. Human disposition remains distinct from the imported assessment.

The draft [version 1 response schema](schemas/ai-review-v1.schema.json) defines the transport contract. The future importer must resolve every reference against the exact export manifest and check unsupported numeric claims in review prose.
