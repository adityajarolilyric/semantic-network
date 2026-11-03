# Shared SQL engine and physical storage

Managed identity/pivot, planning populations, resolved transactions, measures, Planning View and edits share one engine. Domain services submit typed intent and expected versions; planners resolve meaning; compilers generate SQL; executors run checks/write candidates; MongoDB manifests activate results. Do not add a separate SQL-string service per feature.

## Contracts and compilation

Engine-owned immutable contracts include owner-qualified ColumnRef, RelationSchema (ordered IDs/types/nullability/keys/lineage), logical plans, compiled execution/check steps, captured metadata inputs, durable runs, artifacts and active bindings. Source/stage plus column ID is required; bare `col_1`, names and labels are insufficient.

Managed pivots compile explicit grouping: empty record keys group retained fields plus raw pair discriminators/values; supplied record keys merge entity attribute rows with strict cell/carried-value checks. Layout detection and data profiling are removed. Schema analysis is metadata-only; saved legacy operations remain compatible. See [managed pivot contracts](SOURCES_AND_MANAGED_TABLES.md#pivot-grouping-and-final-output-keys).

Planning compilation always applies DISTINCT to the final public output for source, Cartesian and parent-projection populations. This collapses identical intersections; NULL keys, conflicting attributes per grain and invalid dimension lookups still fail. Preview and Build compile the same output semantics.

Measure source initialisation derives SUM/equal versus repeat from each expansion axis, reversing aggregation order. Edit disaggregation is owned by MeasureEditCompiler and continues to use configured allocation. Representation boundaries round stored values to six places before downstream propagation; the policy participates in network fingerprints. See [measure execution](MEASURES.md).

Typed operators include scans/projects, Pivot, CrossJoin, DistinctProject, filters and lookup enrichment, with measure dependency DAGs/resolution operators composing further queries. Only code-owned allowlisted implementations execute; neither client payloads nor vocabulary documents register arbitrary SQL/functions.

The IR and stored semantic formula AST are independent of SQLGlot. ClickHouse adapters use pinned SQLGlot 27.28.1 for AST construction/parsing/rendering, with quoted identifiers, typed/escaped literals and validated types. No API accepts arbitrary SQL/physical execution plans. SQLGlot does not replace semantic validation or real ClickHouse tests.

Use captured input artifact names/generation tokens throughout a run, not whatever binding becomes active later. Metadata snapshots freeze definitions, not live raw rows. Materialisation captures rows read by a build without claiming a distributed snapshot across arbitrary external writers. App-imported CSV sources have no row-update API; future refresh requires explicit data-generation guarantees.

## MongoDB operational records

Keep these separate from semantic definitions:

- `engine_runs`: scoped target, expected versions, request/idempotency hash, run state, owner/lease/fence, fingerprints, candidates, timestamps and bounded errors.
- `engine_run_inputs`: one immutable captured definition/input per run/input ID, avoiding revision-sized BSON documents. Edit payloads are transient exceptions removed at completion.
- `engine_run_steps`: compile/check/create state, SQL/plan hashes, attempt/fence and timing. Recovery updates attempts; this is not a complete append-only attempt audit.
- `engine_artifacts`: owned physical kind/name, schema/lineage, plan/definition/compiler/source hashes, lifecycle, creating run and generation/storage-column maps.
- `engine_artifact_bindings`: active artifact per `(modelId,revisionId,resourceKind,resourceId)`.
- `engine_artifact_pins`: build/run input retention by `(runId,artifactId)`.
- `engine_read_leases`: renewable retention for active reads; TTL clears expired leases.
- `engine_table_resources`: stable physical table/column registry described below.

Runs dispatch managedTable, planningLevel, planningLevelPreview and measureSet jobs; level value bindings/artifacts use measureValues. PlanningLevelPreview jobs produce planningLevel artifacts without binding them. Compatible legacy managed sourceId paths remain readable. Startup backfills generic identity and replaces the source-only unique index with partial managed-source and generic target indexes. Unique managed-definition/target/idempotency/step identities and queued/lease/retirement indexes protect recovery. Deploy API/worker code together; old source-only workers cannot process the generalized queue.

## Durable execution and activation

1. Capture bounded consistent metadata/input artifacts, expected versions and idempotency hash. Pin dependencies transactionally; pin acquisition and retirement touch the same artifact record to serialize their race.
2. Queue durable work and return HTTP 202/run handle. Idempotency is model/revision scoped: same key/request replays its run; changed request conflicts.
3. A Mongo-backed worker claims a 60-second lease, increments fence and renews every 10 seconds. Expired work is recoverable by another process; no Redis/Celery or in-process-only queue is required.
4. Compile, run domain-specific checks, create/insert the candidate, inspect schema and validate full candidate. Keep diagnostics bounded without restricting checked input to preview rows.
5. In a short Mongo transaction recheck owner/fence/unexpired lease, target/revision versions, input hashes/bindings and draft rules. Revalidate downstream schema references as applicable, then activate definition/binding/run atomically. Network/edit jobs swap every affected level to one coherent generation.

Failure, cancellation, superseded input or stale versions preserve the previous definition/bindings. First managed enable may retain unavailable raw registration. Unrelated revision edits can conservatively supersede builds; do not automatically replan over newer intent. Cancellation fences activation but cannot promise interruption of in-flight ClickHouse calls. Shutdown/recovery leaves durable work for lease reclamation. Warehouse and MongoDB do not share a distributed transaction; correctness comes from validated writes before the fenced manifest switch.

Generic endpoints are revision-scoped `GET /engine/runs/{run}` and `POST /engine/runs/{run}/cancel`. The UI polls while open; jobs continue across reloads. No run-history UI exists yet.

## Managed view ownership

Each managed build has a unique candidate view with a reserved `__semantic_` name, hidden from catalog/raw preview. Verify ownership before recovery/collection or accepting an existing object; never replace an active view in place.

On ClickHouse 25.3, create the view then ALTER TABLE MODIFY COMMENT for its ownership marker. If interrupted between them, parse/verify the engine-owned subquery alias in `system.tables.as_select` with SQLGlot before applying the comment. Unknown ownership is retained for investigation. Pivot row expansion uses direct per-record projection; legacy/explicit merge mode retains full duplicate-cell checks. Identity/pivot semantics are in [Sources and Managed Tables](SOURCES_AND_MANAGED_TABLES.md).

## Strict two-table storage

For each `(modelId,revisionId,planningLevelId)`, lazily allocate **at most two physical tables**, including during rebuilds: population (keys/attributes) and values (keys/available measures). No per-measure, temporary, staging or replacement planning/value tables. Imported sources and managed views have separate lifecycles.

`engine_table_resources` is unique by model/revision/kind/resource and physical name. Kinds are planningLevel and measureValues, resource ID is the planning-level ID. It owns physical names, ownership markers, storage slots, retired names and pending removals. Names allocate once and remain stable through rebuilds and later metadata renames:

- Population: `grain_{planning_level_name}_{id}`.
- Values: `grain_{planning_level_name}_mat_{id}`.

Labels preserve ASCII case, replace spaces/punctuation with underscores, cap at 80 characters and have an empty-label fallback. The opaque 28-character hex ID scopes an incarnation across revisions/models; deleting/recreating allocates a new name to protect against delayed cleanup. Generated shapes/ownership hide them from source discovery; ordinary `grain_inventory` remains a valid source name. Existing-name migration is an [operator-only one-time action](OPERATIONS.md#one-time-planning-table-name-migration), never startup logic.

Each artifact has `storageMode=versioned`, a unique generationToken and explicit per-column storageName inside the shared table. Fresh MergeTrees ORDER BY the hidden String `__semantic_generation`; ordering is not uniqueness enforcement.

Builds derive schema, reserve slots transactionally under the attempt fence, create/add columns if needed, then INSERT SELECT with a fresh token. Validate only candidate rows before activation. Never truncate/overwrite active rows. Recovery uses a new token in the same table, isolating late expired-worker writes. Readers project explicit logical columns through storage names and filter the captured token. Raw SELECT * includes internals and retained generations and is not the active logical dataset.

## Schema changes and removal

Storage slots key on logical ID, role, physical type and nullability. Type changes allocate another slot in the same table; logical renames reuse slots through aliases. Newly added slots are physically Nullable so schema generations coexist; readers restore logical nonnullable types and validation still rejects invalid keys/results. Protect hidden/retired names against collisions.

Removing a measure and rebuilding removes it from the active schema immediately; its physical column stays while retained/pinned generations need it. Collection later drops unused columns. Record retired names before DDL and never reuse them, so delayed cleanup/readdition cannot delete a new slot. Legacy sorting-key columns remain physically protected, with logical visibility still generation-scoped.

## Collection, compatibility and limits

Unbound artifacts require a terminal run, no active binding, no build pins/read leases and a 24-hour grace. The idle worker scans bounded pages. For views, drop only verified owned objects. For versioned tables, use synchronous generation-filtered DELETE, then remove unneeded columns; do not drop the shared table while other generations need it. A deleted level's resources drop only after all generations retire; a retiring registry blocks new reservations and names never reuse. Ownership changes cause refusal. Reset explicitly clears everything through its barrier.

The byte limit covers the **whole physical table**, including retained/candidate generations; rebuilds need headroom. Per-generation row limits, query memory/time/SQL-size/node/thread limits remain separate. Two tables bounds table count, not retained rows, tombstones or audit documents. Long-term operational retention beyond current grace/TTL policies remains an explicit deployment decision.

First builds over legacy bound tables add the generation column with the old artifact ID as constant default, preserving rows without copying to another table. Remove extra unbound legacy tables only after ownership/terminal/binding/pin/read-lease checks; active old dependencies block migration rather than triggering an extra table. Existing extras may remain until rebuild/collection. Rebuild parents before projections, then the network. Published generations remain readable and cannot be rebuilt in place.

Storage/compiler policy versions enter fingerprints; incompatible queued work must be resubmitted. Cloning copies definitions/IDs, not bindings. Contract-2 publication requires current managed views/populations but not values. Ordinary view ensure can repair unchanged published definitions; frozen population/value snapshots require restoration or a new draft rather than requerying changed data.

Regression tests inspect table counts/names during INSERT, schema add/remove/readd, recovered-token isolation, failed multi-level activation, retained/pinned generations, generation deletion/column retirement, legacy adoption, stale metadata and ownership refusal. See [Operations](OPERATIONS.md#verification) for runners.

Object-owned input pivots reuse ManagedTablePlanner/ClickHouseCompiler through `engine/object_inputs.py`. Planning RelationScan may carry an optional logical transform for dimension lookups. Measure compilation caches each owner/artifact/pivot input as a CTE and carries its full-input assertions into the execution plan. This supports independent Dimension/Transaction transforms without shared-source mutation or extra physical tables; calendar and multi-grain stages consume the resulting schema.
