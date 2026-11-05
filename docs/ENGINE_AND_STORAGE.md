# Shared SQL engine and physical storage

Managed identity/pivot, planning populations, resolved transactions, measures, Planning View and edits share one engine. Domain services submit typed intent and expected versions; planners resolve meaning; compilers generate SQL; executors run checks/write candidates; MongoDB manifests activate results. Do not add a separate SQL-string service per feature.

## Backend module boundaries

The backend uses composition around existing API/service/repository boundaries; resource APIs remain class-based and separated by context. No new framework, queue or persistence format is introduced.

- **Intent and reads — `services/`:** validate requests, capture metadata/artifacts, submit jobs, report status and serve bounded previews. `capture_build_inputs` and `capture_population_inputs` make their scope explicit. `MeasureBuildInputs` names the captured fields while retaining existing tuple-unpacking compatibility.
- **Execution — `engine/*_builds.py`:** managed, planning and measure handlers execute captured intent. `EngineWorker` owns claims, heartbeats, steps and failure state, dispatching through a code-owned `EngineRunHandler` registry wired by the container. It no longer accesses planning/measure services through the managed-table service. Unknown targets fail without executing a managed build; legacy jobs lacking a target retain managed semantics.
- **Activation:** planning and measure activation classes receive named candidate records. They retain distinct preview, revision, input, generation and edit guards. `ActivationWriter` is a transaction-cached metadata repository component that only shares binding writes and terminal run persistence; callers must fence and validate inside the same Mongo transaction. It does not replace domain guards or wrap ClickHouse writes in a database transaction.
- **Shared compilation:** `CompilationContext` owns input pivot reuse, CTE registration, accumulated checks and query limits without constructing a measure compiler. Population and standalone input/calendar/dimension previews use it directly; measure and full transaction resolution compose their domain-specific compilation with the same state. Compiler methods such as `compile_measure_at_level` distinguish compilation from artifact lookup.
- **Transformation previews:** explicit input, calendar, dimension-output and transaction-output entry points replace combinations of Boolean preview flags. `InputPipelineCompiler` uses the existing pivot/calendar operators and dimension filters. Final transaction mapping/filtering retains its existing compilation path and stage order.
- **Shared infrastructure:** `CapturedRunInputs` interprets existing immutable input prefixes; `ManagedInputCapture` resolves ready source snapshots directly, so transactions and measure builds do not fabricate planning-level objects to capture source inputs. `ManagedInputVerifier` verifies captured source artifacts, not newly active bindings. `ArtifactReadLeases` shares transactional pin acquisition, 15-second renewal, 180-second expiry, validation and guaranteed release. `WarehouseCheckRunner` and `CheckDiagnostics` share execution/formatting while preserving per-context settings, messages and diagnostic bounds.
- **Semantic rules — `validation/`:** the graph validator remains the public entry point and shared diagnostic/dependency context; dimension, planning, transaction, measure and formula rules live in focused classes. Rules continue to validate metadata without querying the warehouse.
- **Policies — `domain/`:** numeric type interpretation, legacy measure normalisation and source-NULL policy are shared below services and engine. The former service-module import remains a compatibility re-export. Clock and revision identity helpers belong to `core/metadata.py`, with existing service imports retained.

Structural extraction preserves HTTP routes/payloads, Mongo document shapes, run input formats, error codes/paths, compiler/policy version strings, CTE names and storage ownership. It requires no data migration, forced Full Refresh, extra physical tables or edit journal. Existing queued jobs continue through the same persisted contracts; activation and failed-build isolation remain unchanged.

## Persistence boundaries and component lifetimes

Services and engine coordinators do not create database clients, construct MongoDB selectors, or execute SQL/client callbacks. They orchestrate business rules and transactions, using typed metadata repositories and warehouse operations. SQL construction belongs to compilers; execution and physical object ownership checks belong to `warehouse/`. Existing engine executor/storage/retention imports are compatibility exports of those warehouse implementations.

- `repositories/metadata/` owns model, revision, object, managed-table, record, job, artifact, binding, lease and storage-resource persistence. Immutable lookup objects express domain IDs, expected versions, states and cutoffs. Only this package translates them into collection names and MongoDB selectors/operators. It rejects raw operator dictionaries. The low-level Mongo adapter owns driver calls, session propagation, indexes and schema setup.
- `warehouse/query_execution.py` owns population/value previews, Planning View reads, resolution preview/count diagnostics and profile reads. Managed view execution, planning-table execution, versioned storage and retention have dedicated warehouse adapters. Services receive rows/results and never receive a client or an executable client callback. Preview limits, ownership checks, settings and diagnostics remain unchanged.
- `MongoMetadataStore` owns the pooled MongoDB client; `ClickHouseWarehouseRepository` owns its lazy ClickHouse client and guarded execution. The application container creates services once per app/worker. No service opens another client.
- Sessionless Mongo reads reuse one document adapter and cached domain repositories for the store lifetime. Each MongoDB transaction attempt has a separate session-bound adapter; its domain repositories, record repositories and activation writer are created lazily once and reused throughout that attempt. Caches hold repository bindings, never query results. Reopening the store invalidates its read adapter. A transaction repository must never be a global singleton or be shared with another transaction.
- Warehouse query adapters, check runners, immutable check diagnostics and input verifiers are reused. Stateless filter compilation and managed-table planning use class/static operations without allocating a component per call. Planning compilation has an application-scoped facade with fresh internal compilation state. Measure/query compilation contexts, validation graphs, input snapshots and heartbeat tasks remain operation-local because they hold mutable or request-specific state.

Architecture tests reject client/driver access, raw document operations and MongoDB operator construction outside persistence infrastructure, and reject collection names/SQL generation in services. Lifecycle tests verify adapter reuse, separate transaction sessions and read-cache invalidation on close/reopen. This changes code ownership, not API payloads, metadata documents, aggregation policies, storage generations or the two-table contract.

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

Final-output filters use the shared typed engine module and nested AND/OR editor. See [Filters](FILTERS.md) for stage placement, scalar operators, NULL semantics, schema APIs and compatibility.

### Schema reuse and incremental materialisation direction

Versioned table builds reuse the authoritative schema returned by `TableStorage.prepare` for column slots, insertion metadata and artifact output. They do not rerun the full compiled SELECT with LIMIT 0 in the executor. Legacy builds retain their header-discovery path. This is a performance-only change; both pre/post input checks and candidate table validation remain intact.

Incremental materialisation is implemented by `engine/incremental.py` and `services/incremental_builds.py`. `materialisationManifest` on each values artifact stores a policy version, per-measure/per-target evaluation fingerprints and an output-layout hash. Fingerprints include the recursive formula dependency closure, native/Base/target population artifacts and projection ancestry, relevant Dimension definitions, Transaction transformations/mapping, managed input artifacts and numeric/materialisation/compiler policies. Unrelated new measures/levels do not invalidate existing representations. Edit allocation/permission and output labels are separated from evaluation; changed labels/layout are copied into a new schema without reloading unchanged values.

Job manifests are stored per level in `engine_run_inputs` (rather than one unbounded measure × level map on the run document), keeping individual MongoDB documents bounded. Incremental Build captures and pins a coherent set of active values alongside its ordinary inputs. Matching representations read explicit columns at the captured generation token; changed/new representations use the existing compiler and can read valid upstream/native representations. AggregateFirst still recalculates from dependencies at the requested grain; CalculateFirst still rolls up its native results. New referenced measures load their own source, even if another edited measure references the same source column. The engine never substitutes that other measure's edits implicitly.

A no-change job validates the stored tables and atomically reuses/retags the current bindings: no source evaluation, value INSERT or revision-version bump, and the existing generation remains active. Other incremental jobs combine preserved columns and newly computed columns in fresh candidate row generations within the existing values tables. This still writes candidate rows; it avoids recomputing unchanged sources/formulas. Pre/post input checks, candidate cardinality/NULL/type checks, snapshot and revision fences, atomic activation and the strict two-table invariant remain unchanged. Failure or stale inputs preserve the prior active network. Removal drops a measure from the active logical schema; normal retention later retires unused physical slots.

Compatible pre-upgrade artifacts establish their baseline from immutable `engine_run_inputs`, including artifacts from edit jobs. Current metadata must never be guessed to be the old baseline. Missing captures or incompatible numeric/compiler policies require explicit Full Refresh and leave existing values intact. Each full build and edit writes a current manifest for subsequent incremental reuse.

Freshness uses supported managed-artifact/definition versions and population artifacts. Rebuilding a managed source signals new input data and invalidates its dependent measures/formulas. Arbitrary direct ClickHouse row mutations do not advance those tokens: use explicit **Full Refresh** for them. Full Refresh reloads all sources and replaces manual edits; incremental builds preserve unchanged values/edits and recompute only invalidated representations. Changing a measure's source/semantic policy or its required populations can therefore replace that affected measure's edits; this is not a durable edit-replay journal.

## Formula compilation boundary

The `formula_language` package separates Lark syntax, versioned semantic AST/resolution, type/function contracts and SQLGlot scalar lowering. `engine/counting.py` plans aggregate arguments at the measure’s Output Planning Level before projecting to output grain. Relational planners consume these expressions; repositories alone execute warehouse I/O. Grammar/parser instances are cached; request resolvers and build compilers hold only operation-local state. Legacy saved ASTs are accepted; versioned authoring source is reparsed on validation/save. Window/recurrence semantics remain future extensions described in [Measures](MEASURES.md#formula-language-architecture).
