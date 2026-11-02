# Semantic metadata backend

Design rationale, agreed metadata semantics, and preserved project context: [Metadata v1 design](../docs/METADATA.md).

A standalone Python/FastAPI service for MongoDB metadata and CSV source ingestion. `application/` is the current UI; this is the only backend implementation. Metadata CRUD and warehouse operations use separate modules. CSV imports create new ClickHouse tables with catalog/preview access; managed views, planning populations and measure values are built by the shared engine; general client SQL and scenario state remain outside this version.

Source-flow behavior and failure semantics: [CSV sources v1 design](../docs/SOURCES_AND_MANAGED_TABLES.md).

## Architecture

Requests flow through a resource API class, an application service class, and a repository. HTTP concerns stay out of services; MongoDB concerns stay out of API classes. A shared repository protocol permits real MongoDB and transactional in-memory tests.

```text
app/
  api/             ModelAPI, RevisionAPI, SourceAPI, DimensionAPI,
                   PlanningLevelAPI, TransactionAPI, MeasureAPI,
                   EvidenceAPI, ExampleAPI, ContractAPI, WarehouseSourcesAPI, HealthAPI
  schemas/         Typed Pydantic request/response models, one domain per module
  services/        Context-specific classes and reusable CRUD invariants
  validation/      Cross-document references, grain identity, formulas, lineage
  repositories/    Persistence interfaces and asynchronous PyMongo implementation
  warehouse/       CSV parser, source service, ClickHouse repository, response schemas
  db/              MongoDB collection envelope validators
  core/            Configuration, preconditions, errors, authentication, logging
  container.py     Explicit service dependency wiring
  main.py          Application factory, lifespan, router and exception registration
```

Each context's routes are registered by its own API class and module. `RevisionResourceAPI` shares pagination, ETags, and delete mechanics; its concrete subclasses declare resource-specific typed bodies. A router registry in `main.py` wires classes together and contains no CRUD handlers.

## Run locally

Use Python 3.11+; tested with Python 3.13 and MongoDB 8.0. Commands below run from the repository root.

```bash
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements-dev.txt
cp backend/.env.example backend/.env
docker compose -f backend/compose.dev.yml up -d --wait
backend/.venv/bin/python -m uvicorn backend.app.main:create_app --factory --host 127.0.0.1 --port 8788 --reload
```

Open [Swagger UI](http://127.0.0.1:8788/docs) or [OpenAPI](http://127.0.0.1:8788/openapi.json). Health endpoints are `/health/live` and `/health/ready`.

Development Compose provisions a **single-node replica set** on host loopback port 27018 and ClickHouse on 8123. To use an existing warehouse, start only `metadata-mongo` and configure `METADATA_CLICKHOUSE_*`. `npm run db:up` uses `backend/.env` when it exists. The root `docker compose up --build -d --wait` also starts the containerised API and UI; see [Compose deployment](../docs/OPERATIONS.md#full-stack-startup). These single-node databases do not provide high availability.

## Metadata decisions

- Single tenant; no `tenantId` fields.
- Source metadata stores `tableName` and column identity/type, with no connection, database, or `physicalBinding` fields.
- Column references use stable owner-scoped IDs rather than names, so future supported source-column renames can preserve downstream references. IDs must not be regenerated when column order changes. See [column identity and rename handling](../docs/METADATA.md#stable-column-identity-across-renames); current source-update immutability still applies.
- Source table and column names/definitions are immutable through update. Catalog description, scope, and declared key metadata may be updated if dependencies stay valid. Creating/deleting a catalog entry does not create/drop a physical table.
- Source documents and their columns reject `displayName`. Dimension columns own semantic display names and map to `sourceColumnId`.
- Dimensions store root-level `isTimeDim`; only one time dimension is allowed. `time` configuration must exist exactly when the flag is true.
- Revisions store `basePlanningLevelId`; planning-level documents reject `isBase`. Publication requires a valid base in the same revision. The base cannot be a coarser projection of another level.
- Cartesian populations and distinct projections express PLT and PT. Mappings use stable IDs, not display labels.
- Forecast/FQTY are native at PLT. Sales/SQTY and AggregateFirst Error are native at PT. FQTY is available at PT through projection lineage; SQTY cannot appear at PLT without an explicit allocation rule.
- Formula ASTs are authoritative. `displayText` is descriptive text, not executable SQL. Measure formulas, managed-table views and pivots compile through the separate SQLGlot engine module.

`examples/plt_pt.json` contains the full discussed metadata example: six sources, three dimensions, two levels, two transactions, and three measures. Its `_id` and scope envelopes illustrate stored documents; resource POST bodies omit database IDs, scope, timestamps, and server-managed versions.

Once the service is running, create that draft through the public APIs:

```bash
backend/.venv/bin/python backend/scripts/load_example.py
```

The loader validates the resulting definition, leaves it unpublished, and does not overwrite an existing model. If API authentication is enabled, export the same `METADATA_API_KEY` before running it.

## Measure null-policy contract

Metadata CRUD validates these persisted semantic rules and graph references. Build Semantic Network inspects source rows and enforces their data-handling behavior. User-facing descriptions are in [Measure null policies](../application/README.md#measure-null-policies).

Referenced measures store:

```json
{
  "sourceNullPolicy": "reject",
  "missingTransactionValue": "null",
  "aggregation": {
    "nonTime": "sum",
    "time": "sum",
    "nullInputPolicy": "ignore",
    "allNullResult": "null"
  }
}
```

- `definition.sourceNullPolicy`: `reject` (default) reports an error for NULL in an existing source row's selected value column; `preserve` passes that NULL to aggregation. This is independent of physical column nullability and of missing joined rows.
- `definition.aggregation.nullInputPolicy`: `ignore` (default) excludes NULL values from the aggregate inputs; `propagate` makes any group containing NULL return NULL. With Preserve and SUM over `[10, null, 20]`, Ignore returns `30` and Propagate returns `null`. AVG with Ignore returns `15`, not `10`.
- `definition.aggregation.allNullResult`: under Ignore, an all-null group returns `null` (default) or `zero`. With Preserve, Ignore, and `[null, null]`, the chosen output is NULL or 0. Inputs `[0, 0]` are actual values and SUM to zero regardless of this setting.
- `definition.missingTransactionValue`: determines `null` (default) versus `zero` when an aggregated transaction has no row for a native planning-level key after the LEFT JOIN. It does not replace NULLs in existing source rows.

Execution precedence is explicit:

1. Validate/read the referenced source values; Reject fails on an actual source NULL.
2. Group each transaction independently by all mapped native planning-level keys and aggregate using the measure's rules.
3. In each aggregation group, Propagate takes precedence: any NULL yields NULL, even if `allNullResult` is `zero`. With Ignore, aggregate non-null inputs; only when none remain, apply `allNullResult`.
4. Perform `NativePlanningLevel LEFT JOIN AggregatedTransaction`. Apply `missingTransactionValue` to absent matches, keeping them distinct from present all-null groups.
5. Apply aggregation null policies again when rolling measure results up to a coarser grain. Join-introduced or calculated NULL measure values are aggregation inputs, not source-row errors.

A future compiler must enforce these semantics explicitly instead of relying on warehouse aggregate defaults. The accepted combination Propagate plus All-null Zero still produces NULL for a group containing NULLs. No metadata migration or runtime behavior is implemented by documenting this precedence.

For calculated measures, `definition.nullInputPolicy` is the separate formula-level policy, currently `propagate`: ordinary arithmetic and ABS propagate NULL operands, while an explicit COALESCE expression can handle them. CalculateFirst additionally has aggregation rules for its calculated results; AggregateFirst uses the input measures' aggregation rules before evaluating its formula.

## Collections

- `semantic_models`: identity, draft/published revision pointers, revision counter.
- `semantic_revisions`: versioned model context and selected base level.
- `semantic_objects`: independently stored typed sources, dimensions, levels, transactions, measures.
- `semantic_evidence`: timestamped observations, check scope, source fingerprint, and outcomes.
- `semantic_examples`: business questions, bounded source fixtures, expected rows, and rationale.
- `semantic_contracts`: versioned documentation/schema vocabulary records for agents. These records do not dynamically change the service's Pydantic validators or enable new operators. Supported executable contracts are defined in code and exposed through OpenAPI.

MongoDB structural validators protect required envelopes, versions, timestamps, and key exclusions. Unique compound indexes enforce model/revision/object identities. Pydantic enforces nested shapes; application validation enforces cross-document meaning. MongoDB is not queried to prove physical source data uniqueness or truth of submitted evidence.

## CRUD endpoints

All metadata endpoints are under `/api/v1`.

- `/models`: POST, GET; `/models/{model_id}`: GET, PUT, DELETE.
- `/models/{model_id}/revisions`: POST, GET; `/{revision_id}`: GET, PUT, DELETE.
- `/models/{model_id}/revisions/{revision_id}/validate`: POST, returns diagnostics.
- `/models/{model_id}/revisions/{revision_id}/publish`: POST, validates and publishes atomically.
- Under the same revision prefix: `/sources`, `/dimensions`, `/planning-levels`, `/transactions`, `/measures`, `/evidence`, `/examples`. Each has POST/GET collection routes and GET/PUT/DELETE `/{resource_id}` routes.
- `/contracts`: POST, GET; `/contracts/{contract_id}`: GET, PUT, DELETE.
- `/warehouse/status`, `/warehouse/tables`, `/warehouse/tables/{table}/preview`: GET; `/warehouse/imports/csv`: POST with one multipart file, table name, and `Idempotency-Key`. These routes are independent of model revisions and do not register guessed business keys in MongoDB.

PUT is full replacement of editable request fields, not PATCH. Object IDs and resource kinds cannot change. Read/create/update responses expose `version` and an `ETag`. PUT/DELETE/publish require the resource's current `If-Match: "<version>"`. A missing precondition returns 428; malformed preconditions return 400; stale versions return 409. Each list accepts `limit` (1–100) and an exclusive `cursor`, returning `items` and `nextCursor`; ordering is by stable resource ID.

### Example lifecycle

1. POST a model with `modelId`, `name`, and `displayName`.
2. POST a revision with `revisionId`, `expectedModelVersion`, and optional `basePlanningLevelId`. A draft may name a base that will be created next; validation/publication requires the target to exist.
3. POST sources, then dimensions, levels, transactions, and measures in dependency order. Unknown references are rejected rather than silently accepted as forward references.
4. GET the revision to obtain its current version; every successful definition mutation increments that version.
5. POST `/validate` to inspect publication readiness.
6. POST `/publish` with `If-Match` for the revision and body `{"expectedModelVersion": <current model version>}`.
7. To edit published definitions, create a new draft with `parentRevisionId`. Objects and examples are copied; observed evidence is not copied as if it were newly verified.

There is one active draft per model. Finalized revisions remain immutable and retained. Publishing changes the model pointer in the same transaction. Evidence may be recorded/updated after publication because it describes observations rather than changing the definition. Example records are frozen with the definition.

Deletes are explicit and dependency-aware: delete dependents before dependencies, clear/change a selected base before deleting it, and empty a draft before deleting it. Models with revisions and all published revisions cannot be deleted through these routes. There is no cascading delete or warehouse cleanup.

## Concurrency and durability

Use a MongoDB replica set or sharded deployment: standalone MongoDB is intentionally rejected at startup. Each mutation uses a snapshot transaction and majority writes. Definition mutations acquire a revision write guard; publication uses the same revision document so it cannot race with edits and publish an inconsistent snapshot. Per-resource versions prevent lost updates. Transaction retries are handled by PyMongo; validation failures roll back the revision guard and all document writes.

The store uses asynchronous PyMongo, one application-lifetime client, bounded pools/timeouts, startup indexes/validators, and orderly shutdown. Startup needs collection-management/index privileges in the **configured metadata database**. Configuration uses `METADATA_*` environment variables; no credentials are returned through metadata APIs. The `.env` path is resolved relative to `backend`, independent of the process working directory.

Metadata request bodies default to 1 MiB; list pages to 100; a revision to 5,000 objects and each record category to 5,000 records. These explicit bounds keep validation and cloning bounded. Scale by observed model-definition size rather than fact row count; this service does not load warehouse rows. Copying very large revisions and graph indexing should be measured before raising these bounds.

## Authentication and errors

Set `METADATA_ENVIRONMENT=production` and a secret `METADATA_API_KEY`; production startup rejects missing credentials. Requests then require `Authorization: Bearer <key>`. This is service-level authentication. Put user identity/authorization and TLS at your authenticated gateway before exposing the service to users. Development defaults are suitable for loopback-only use.

Errors use `{"error":{"code":"...","message":"...","requestId":"...","details":[]}}`. Validation errors exclude submitted values; database errors do not expose credentials or raw driver details. Responses include `X-Request-ID`, and JSON logs include request ID, route, status, and duration. Readiness failure returns 503 while liveness remains independent.

## Verification

```bash
backend/.venv/bin/ruff check backend
backend/.venv/bin/ruff format --check backend
backend/.venv/bin/python -m pytest -c backend/pytest.ini backend/tests
```

Unit/API tests use a transactional in-memory repository. Integration tests verify actual MongoDB persistence, indexes, collection validation, rollback, competing writers, and publication racing with mutations.

With `mongod` installed, run a temporary isolated replica set and integration suite:

```bash
backend/.venv/bin/python backend/scripts/test_integration.py
```

Alternatively, run against a dedicated test replica set:

```bash
METADATA_TEST_MONGO_URI='mongodb://127.0.0.1:27018/?replicaSet=metadata-rs' \
  backend/.venv/bin/python -m pytest -c backend/pytest.ini backend/tests/integration -m integration
```

Each integration fixture creates and drops only a generated `metadata_v1_test_*` database. The temporary-server runner selects an unused loopback port, uses a temporary data directory, and shuts its process down afterward.

## CSV source verification

The CSV source suite can run against a temporary ClickHouse container:

```bash
backend/.venv/bin/python backend/scripts/test_csv_integration.py
# Include the real browser/UI flow after installing Playwright Chromium:
backend/.venv/bin/python backend/scripts/test_csv_integration.py --with-ui
```

It creates only isolated test databases and removes its temporary container. See [UI verification](../application/README.md#verification) for frontend setup and [CSV source design](../docs/SOURCES_AND_MANAGED_TABLES.md) for ingestion guarantees.

## Extension points

New object kinds need a typed schema, service/API context, graph rules, and database enum migration. New AST operators/functions need schema, arity/type/unit rules, and tests. Explicit source-to-source relationship paths, mixed-grain transformations, advanced calendars, and planning/measure SQL execution remain future modules; managed-view SQL generation is implemented separately; this version implements the agreed PLT/PT metadata vocabulary rather than accepting unvalidated arbitrary definitions.

## Metadata authoring UI

The [v1 UI](../application/README.md) now captures semantic metadata end to end. `POST /api/v1/workspace/bootstrap` atomically creates the default model and first draft on an empty installation. `POST /api/v1/models/{modelId}/revisions/{revisionId}/source-registrations` reads warehouse columns and registers a semantic source using selected physical key names. See [metadata UI design](../docs/METADATA.md#shared-ui-and-field-help) for scope, payloads, and lifecycle behavior.


## Managed views and pivot (2026-11-01)

The SQL engine now builds a revision-owned identity view for every newly enabled Managed Table and supports one optional pivot stage with multiple attribute/value pairs. Sources retain raw data/schema; semantic editors consume the server-derived managed schema. Duplicate pivot cells fail rather than aggregate; identity duplicates are preserved unless a managed key is declared. Builds run durably in MongoDB, activate atomically, and preserve the prior working view on failure.

Open Managed Tables to configure grouping, pairs, discovered/expected values, aliases, keys and checked previews. New drafts use engine contract 2; older drafts can use Initialize managed views on Sources. Cloned revisions need their own view builds. Planning-table and measure execution are implemented; see Materialise for semantic-network builds.

See [SQL engine implementation, APIs, recovery and revision behavior](../docs/ENGINE_AND_STORAGE.md) for authoritative details and test commands.


## Planning-level tables (2026-11-01)

Planning-table generation is implemented through the shared engine: single managed populations, Cartesian factors, distinct parent projection, population attributes and dimension enrichment, with complete grain/lookup checks and revision-owned MergeTree artifacts. Add dimension tabs incrementally; select grain keys and population attributes side by side. Preview and Build live inside the editor; previews reuse the Source table UI and show at most 100 rows. Contract-2 publication requires ready planning tables. Materialise builds transaction-backed and calculated measure values across compatible levels.

See [planning-level implementation, APIs, migration and limits](../docs/PLANNING_LEVELS.md) for the current operating contract. Earlier statements that planning materialization is deferred are superseded by this increment.

## Semantic network materialisation

**Materialise** is a separate navigation item below **Measures**. Build managed views and planning populations, then use **Build Semantic Network** to populate compatible measure representations. Selecting a planning level displays its table preview (maximum 100 rows). All measure rollups use the configured Time/NonTime policy. Dis-Aggregation None/null repeats values; it never selects AVG implicitly. AggregateFirst recalculates at each target grain. Generations activate atomically across levels. Interactive measure editing is available in Planning View.

See [implementation, policies and APIs](../docs/MEASURES.md).

## Reset workspace

Sources offers **Nuke everything** with one confirmation. It removes all application metadata (including published revisions) and all tables/views in the configured ClickHouse database, then leaves a fresh default semantic model and draft revision. Active application operations are fenced/drained first. A failed reset stays in maintenance and can be retried from Sources. See [scope, coordination and recovery](../docs/OPERATIONS.md#workspace-reset).

Attribute-grain levels such as Brand × Zone × Sold To use distinct selected parent columns. Optional bindings check membership; selected extra attributes must be unambiguous without changing grain. See [attribute-grain planning levels](../docs/PLANNING_LEVELS.md#optional-dimension-membership-and-enrichment).

## Measure numeric policy

Referenced measures always use Float64, including when their source column is integer or Decimal, so disaggregation can produce fractions. New calculations default to Float64; explicit Decimal calculations remain supported. Data-type controls are hidden. Float64 is approximate, so tiny rounding differences are expected. Rebuild existing draft values through Materialise to apply the policy. See [numeric types and compatibility](../docs/MEASURES.md#numeric-policy).

## Planning storage

Each revision/planning level reuses one population table and one values table, including during rebuilds. Candidate rows are generation-scoped inside the same tables; bindings switch only after validation succeeds. See [storage, upgrade and cleanup details](../docs/ENGINE_AND_STORAGE.md#strict-two-table-storage).

New ClickHouse population/value names are `grain_{planning_level_name}_{id}` and `grain_{planning_level_name}_mat_{id}`. Existing-table renames are a one-time operator action using `scripts/rename_planning_tables.py` (preview by default, `--apply` to execute), not service behavior. See the storage document for the journal and recovery procedure.

## Planning queries and editor builds

Planning View adds `GET /planning-view/options` and `POST /planning-view/query` under a revision. It queries saved materialisations with policy-aware source selection, formulas and read leases. `METADATA_PLANNING_VIEW_MAX_GROUPS` defaults to 5000; `METADATA_PLANNING_VIEW_MAX_CELLS` to 50000. `METADATA_CLICKHOUSE_MAX_THREADS` defaults to 4 for engine query/build memory control. See [query semantics](../docs/PLANNING_VIEW.md).

Planning-level Preview stages a fully built row generation without saving metadata. Save atomically commits it through `/planning-levels/editor/draft/commit`. No extra physical tables are allocated. See [Preview/Save APIs and consistency](../docs/PLANNING_LEVELS.md#editor-cancel-preview-save).

## Multi-grain transactions

Transactions support **Resolve multi grain** with fixed or level/value matching, Split/Copy per selected numeric column and explicit source application order. Later assignments win at any grain, including coarse rows. Preview shows up to 100 resolved rows without saving; Materialise uses the same engine operator. Direct mapping remains available. See [semantics, metadata, APIs and limits](../docs/TRANSACTIONS.md).

Transactions also support inclusive **Date range** resolution through a daily Time dimension. Daily contributions map to native Day/Week/Month keys, with independent member/date Split/Copy policies and configured Time aggregation during measure loading. See [date-range resolution](../docs/TRANSACTIONS.md#date-ranges-and-native-periods).

Planning View now edits eligible referenced measures at native or proven coarser grains. Writes use Base where reachable, otherwise the native level, and update compatible coarser representations atomically. Weighted by self edits preserve NULL cells and distribute across populated values. No edit journal is retained: Build Semantic Network replaces manual edits from sources. See [writeback semantics, API and limits](../docs/PLANNING_VIEW.md#edit-eligibility-and-write-target).
