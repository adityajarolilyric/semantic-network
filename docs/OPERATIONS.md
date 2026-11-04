# Operations, compatibility and verification

## Full-stack startup

From the repository root with Docker and Compose 2.24+:

```bash
docker compose up --build -d --wait
```

No host Python/Node or environment file is required. UI: http://localhost:5174. API docs: http://localhost:8788/docs. `npm start` runs the same command when Node is installed. `docker compose logs -f api web` shows logs; `docker compose down` stops services and retains volumes.

The `semantic` project runs MongoDB 8.0 (single-node metadata-rs replica set, loopback 27018), ClickHouse 25.3 (8123), Python 3.13/FastAPI as a non-root API (8788), and a Node 24-built UI served by Nginx (5174). Database health checks run before API, API readiness before web. Mongo health initializes/elects an empty replica set. The API contains the engine worker; readiness verifies MongoDB. Containers restart unless stopped. Nginx proxies `/api/`, serves SPA fallbacks, re-resolves container DNS and permits the default CSV upload plus multipart overhead.

## Configuration and data continuity

Root `.env` (optional, copy `.env.example`) controls Compose ports, credentials, database names, volumes and authentication. Host ports: METADATA_WEB_PORT, METADATA_API_PORT, METADATA_MONGO_PORT, METADATA_CLICKHOUSE_PORT. Internal service DNS/ports remain fixed when host ports change.

Optional `backend/.env` supplies additional API settings. Root Compose overrides environment/API key and database connectivity/names/ClickHouse credentials so containers agree; put those overrides in root `.env` or exported variables. Host development uses backend/.env. Defaults are development mode without auth, with all published ports bound to loopback.

Production mode requires both `METADATA_ENVIRONMENT=production` and `METADATA_API_KEY`. Nginx passes the same key server-side; never bake it into browser assets or VITE_* variables. This authenticates the proxy to the API, not browser users. External deployments need their own TLS/user-access boundary; the single-host databases are not HA. JSON logs/errors use correlation IDs and avoid exposing driver credentials/raw request values. Liveness and database readiness are separate.

The previous implementation was removed; current paths are `backend/` and `application/`, with corresponding imports, scripts, CI and repaired local environment launchers. Historical :v1 npm aliases remain. Storage identity is intentionally independent of directory names: preserve `backend_v1_metadata-mongo`, `backend_v1_sources-clickhouse` and database `semantic_metadata_v1`. Override METADATA_MONGO_VOLUME/METADATA_CLICKHOUSE_VOLUME only to intentionally select another volume/fresh install; old semantic_ch-data is not auto-migrated. No data reset or service-side name migration occurs at startup.

MongoDB advertises localhost:27018 for host development. Container API uses metadata-mongo:27018 with replicaSet=metadata-rs and directConnection=true, enabling transactions without rewriting the single-member configuration. Custom host ports may require equivalent direct-connection settings in host clients.

## Development and compatibility

`npm run setup`, `npm run db:up`, `npm run dev` install/start host development; stop container API/UI first to free their ports. `backend/compose.dev.yml` starts only databases and shares root project/volumes. `npm run db:down` stops those services; `npm run stop` stops the root stack. To reuse another ClickHouse, start only metadata-mongo and configure host backend settings.

Metadata contract 2 is explicit for new/UI revisions; generic revision-create defaults to contract 1 for older clients. Initialize managed views before upgrading old drafts. Published legacy definitions remain immutable; compatibility provisions unchanged identity views operationally. Clones preserve semantic IDs/definitions, not bindings: initialize managed views, build planning parents/children, then Materialise. Numeric/storage/compiler changes may stale artifacts; rebuild drafts rather than rewriting published snapshots.

Generalized engine target/index migration and stored-metadata adapters remain necessary compatibility code. Removing old applications does not authorize dropping these adapters. API/worker releases must agree on queue contracts. See [engine migration/storage](ENGINE_AND_STORAGE.md) and [managed lifecycle](SOURCES_AND_MANAGED_TABLES.md#managed-apis-discovery-and-compatibility).

## One-time planning-table name migration

`backend/scripts/rename_planning_tables.py` is an operator script, never service/API/build startup logic:

```bash
backend/.venv/bin/python backend/scripts/rename_planning_tables.py
backend/.venv/bin/python backend/scripts/rename_planning_tables.py --apply
```

The first command previews. Apply acquires maintenance, drains work, journals the exact plan, verifies ownership/counts and RENAMEs existing tables without copying rows/creating tables. It updates physical addresses in artifacts, stable registry, runs and captured inputs; historical SQL remains an audit of the original execution. Semantic IDs, definitions, values, generations and ownership markers stay intact. New resources use the same readable naming convention described in [storage](ENGINE_AND_STORAGE.md#strict-two-table-storage).

Default journal: `/tmp/semantic-grain-rename-{configuration-hash}.json`; override with `--journal /path/file.json`. Retain it through verification. Interrupted migration stays in maintenance; retry the same --apply/--journal invocation to complete partial renames/references. Completed journals replay idempotently; a live maintenance owner blocks another invocation. Resume migration to recover—workspace reset is not migration recovery.

## Workspace reset

Sources exposes **Nuke everything** with one confirmation. It applies to the entire single-tenant workspace, not selected model/revision. `POST /api/v1/workspace/reset` requires existing authentication and `{"confirmed":true}`; it accepts no database/deletion identifiers.

Delete every table/view in the server-configured ClickHouse database, including unregistered sources/staging/engine objects; refuse system databases and leave other databases alone. Clear all application semantic/managed/operational collections, including published history and storage registry, then recreate only default_model (Planning Model) and revision_1: contract-2 draft, no parent/Base/objects/published pointer. Preserve validators/indexes, configuration/credentials and the internal maintenance/epoch control record. Reload Sources with old selections/editor/upload state cleared. Canceling confirmation makes no request.

`WorkspaceGate` coordinates API/worker/collector operations across processes. Admission acquires a renewable operation lease. Reset closes admission, increments epoch, cancels/fences runs and drains admitted warehouse work. New requests receive 503 while maintenance is active; health/reset remain available. Metadata transactions validate epoch and write the control record, preventing an old transaction from repopulating the fresh workspace.

Operation leases renew every 10 seconds, expire after 180; reset drains up to 130 seconds. A crashed lease may require expiry before retry. Synchronous warehouse calls stay inside the guarded thread boundary while async heartbeats continue. Drop views before tables using quoted synchronous commands. After ClickHouse cleanup, one Mongo transaction bulk-clears collections, recreates defaults and reopens admission; do not drop the Mongo database/indexes.

A physical failure may partially delete ClickHouse data. Keep maintenance active, release reset ownership and return retry guidance; retry completes the remaining deletion before reopening. Renewable ownership recovers crashes and excludes concurrent resets. No automatic reset runs after startup/error. External administrative writers must be stopped too; stop paused/partitioned processes before retrying after their leases expire.

## Verification

Use these root commands as appropriate to the change:

```bash
npm run test:unit
npm run test:ui
npm run build
npm run test:mongo
npm run test:integration
npm run test:e2e
```

Unit/API/UI tests cover typed contracts and interactions. Mongo integration uses a disposable local mongod replica set. ClickHouse integration uses Docker; browser tests additionally need local mongod and installed Playwright Chromium. Runners clean their own temporary databases/servers/containers. `backend/scripts/test_csv_integration.py --with-ui` runs ClickHouse plus browser coverage; `test_integration.py` runs Mongo coverage. Data-changing verification must not target a user's workspace.

Ruff: `backend/.venv/bin/python -m ruff check backend/app backend/tests`. Validate Compose without printing environment values: `docker compose config --quiet`. CI covers lint/backend/UI tests/build, disposable Mongo/ClickHouse integrations and complete Compose startup with UI/proxy/bootstrap smoke checks. Documentation-only changes require link/anchor/reference checks, not application test reruns.

Latest recorded directory-rename verification (2026-11-02): 137 backend tests, 79 UI tests, seven Mongo integrations and 11 browser tests passed, as did Ruff/build, shell syntax, documentation links and both Compose configs. Full-stack startup was repeatable with four healthy services; headless Chromium verified UI, ClickHouse, proxied metadata and SPA fallback. These are dated results, not a claim that every future checkout has been tested. Domain guides retain behavioral coverage instead of incremental test-count histories.

Operational extensions still needed at sustained scale include metadata/audit/tombstone retention, abandoned CSV staging cleanup and measured large-catalog strategies. Existing grace/TTL rules and strict table counts do not bound total retained data. Source-row snapshot guarantees, scenarios, user identity and new business policies require explicit designs.

## Semantic Network build performance

A local six-measure build (576-row ProdLocWeek and 144-row BrandZoneWeek) took 26.7 seconds. Query logs showed four full-DAG `LIMIT 0` schema queries consuming 13.8 seconds, plus two value INSERTs consuming 7.0 seconds. Versioned storage had already discovered authoritative ClickHouse types in `TableStorage.prepare`; the executor now reuses those columns instead of repeating schema discovery. Legacy builds still discover their header normally. This removes one expensive schema query per level without changing aggregation, checks, generation isolation or the two-table invariant. Expected saving on that observed build is roughly seven seconds; a new full build was deliberately not triggered because it would replace manual edits.

The measured host has 18 GiB RAM/12 logical CPUs; Docker currently has 5 CPUs/7.26 GiB shared memory. Containers have no additional CPU/memory caps. API queries use 4 threads and a 2 GiB memory cap; the slowest query used about 700 MiB. A read-only replay of the slow schema query took 4.24s/4.04s/4.01s with 1/4/5 threads; DESCRIBE of the same subquery was also about 4 seconds. Extra memory or threads does not address this query-analysis bottleneck, so resource limits were not increased. Root `.env` may override METADATA_CLICKHOUSE_MAX_THREADS and METADATA_PLANNING_MAX_BYTES; these are query limits, not Docker Desktop VM resources.

Completed engine steps retain startedAt/durationMs per fenced attempt. Measure-level step `timingsMs` separates compile, preChecks, tableBuild (prepareSchema/tableSetup/insert/verifyStoredTable/total), postChecks and total. These diagnostics are stored in MongoDB `engine_run_steps`; they are not a new UI progress contract. Retries start fresh timing. The measured query limits and all semantic validation remain in force.

Incremental network builds are now available. Use UI **Build Semantic Network** for dependency-scoped reuse and **Full Refresh** for a complete source reload (one edit-replacement confirmation). New API clients should explicitly send `mode=incremental`; omitted bodies preserve legacy full-build semantics. Evaluation manifests and captured active artifacts live in MongoDB; no new physical data table is created. A compatible historical build can be bootstrapped from its captured inputs, while an unprovable baseline fails safely and requests Full Refresh. Direct out-of-band ClickHouse row changes require explicit refresh because artifact versions do not detect them automatically.
