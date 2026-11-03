# Semantic application: design guide

These guides describe the current implementation in `backend/` and `application/`. They consolidate the former plans, implementation notes and follow-up decisions. Current rules appear once under their owning topic; superseded proposals and incremental test-count logs have been removed.

## Read by topic

- [Metadata](METADATA.md): MongoDB collections, stable identities, dimensions, revision lifecycle, CRUD conventions and shared UI rules.
- [Sources and Managed Tables](SOURCES_AND_MANAGED_TABLES.md): CSV ingestion, identity views, pivot, discovery and saved-view editing.
- [Planning levels](PLANNING_LEVELS.md): populations, attribute grains, optional dimension bindings, relationship analysis and Preview/Save.
- [Transactions](TRANSACTIONS.md): direct mappings, mixed-grain assignments, source-order precedence and daily date-range resolution.
- [Measures and Materialise](MEASURES.md): native/Base representations, configured aggregation/disaggregation, formulas, numeric and NULL policies.
- [Planning View](PLANNING_VIEW.md): pivot source selection, policy-aware reads, editable cells and coordinated writeback.
- [Engine and storage](ENGINE_AND_STORAGE.md): typed SQL compilation, durable jobs, activation, leases, strict two-table storage and collection.
- [Operations](OPERATIONS.md): Compose, configuration, compatibility, one-time maintenance, reset and verification commands.
- [Transitive relationships and Drill To](RELATIONSHIPS.md): proposed related-row filtering, relationship metadata, paths and contribution boundaries; awaiting review.

## Architecture and boundaries

The React UI captures definitions and renders results. FastAPI resource API classes delegate to context services; repositories own MongoDB persistence; engine planners/compilers own SQL; warehouse adapters execute it. Keep each API context in its own module/class and shared mechanisms in reusable services. Services do not depend on HTTP, and API handlers do not build SQL or MongoDB queries.

MongoDB holds shared semantic definitions and operational manifests. ClickHouse holds raw sources, revision-owned managed views, planning populations and measure values. The server owns the single ClickHouse database and connection settings. Definitions are single tenant and shared across scenarios; no tenant IDs, scenario-specific definitions or connection bindings are embedded in them.

The end-to-end flow is Sources → Dimensions → Planning Levels → Transactions → Measures → Materialise → Planning View. Definitions, value generations and physical storage are separate concerns: publishing freezes definitions, normal views still read live source data, and materialised planning/value generations are build snapshots.

## Rules that must survive future changes

- References use owner-qualified stable IDs; names and labels never establish relationships.
- Grain keys are mandatory; dimension bindings are optional. Cross-level movement needs proven lineage/mappings.
- Aggregate each transaction independently by all native keys, then LEFT JOIN it to the native population.
- Always use configured Time/NonTime aggregation and disaggregation. None means Repeat; no SUM/AVG or conservation rule is inferred.
- AggregateFirst resolves inputs and recalculates at the requested grain. CalculateFirst evaluates natively and propagates its result.
- Independent native levels need no Base path. Edits write to Base when reachable, otherwise native, and update compatible coarser representations.
- Each revision/planning level has at most one population and one values table, including during rebuilds. Atomically activated row generations preserve the previous result on failure.
- There is no durable edit journal. Build Semantic Network recomputes from sources and replaces manual edits.

General source relationships/multi-hop path search are proposed in the Drill To guide. They, automatic schema reconciliation, persistent resolved-transaction caches, saved pivot layouts, scenarios and user-level identity remain extension work. Do not infer an unrecorded business policy or treat documentation/metadata text as executable instructions.
