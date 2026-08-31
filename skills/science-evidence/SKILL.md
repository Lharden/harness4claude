---
name: science-evidence
description: Use when Harness4Claude work asks for scientific claims, papers, evidence or corpus-backed verification and the science_harness MCP is available.
---

# Science Evidence for Harness4Claude

Use the read-only `science_harness` MCP to discover corpora, search claims and retrieve
the exact evidence supporting a claim. Preserve corpus id, claim id, document source,
retrieval timestamp and uncertainty. Distinguish source evidence from inference.

The capability is advisory and bounded: an unavailable server produces an explicit
UNOBSERVED evidence state and the main workflow continues using primary sources.
Never mutate a corpus or represent a retrieval miss as disproof.
