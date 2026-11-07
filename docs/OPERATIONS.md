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

## Historical table-name migration

The earlier population/value layout used an operator-only name migration (`rename_planning_tables.py`), never startup/service renaming. Current installations use the current-storage consolidation below instead; the historical rename procedure is superseded for the new one-table/view layout.

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

Measured on the saved nine-measure example on 2026-11-07 (ProdLocWeek: 576 rows; product: 9; BrandZoneWeek: 144). The original incremental build failed with a memory error after 12.52 seconds. Dependency-ordered writes removed that failure. Profiling then exposed repeated compilation of unreachable CTEs and validation plans while preparing final SQL; pruning dependencies before parsing and omitting duplicate display-only checks cut equivalent full builds from about 43 seconds to about 20 seconds. Actual pre/post validation remains unchanged.

Final controlled Full Refresh comparison (two runs per query-memory cap, in order 512 MiB → 1 GiB → 2 GiB → 2 GiB → 1 GiB → 512 MiB):

- **512 MiB:** 19.605 s, 20.722 s; mean **20.16 s**.
- **1 GiB:** 18.620 s, 19.952 s; mean **19.29 s**.
- **2 GiB:** 20.020 s, 22.198 s; mean **21.11 s**.

All six runs recomputed the network rather than using the no-change shortcut. Every population/value child used its captured budget, and checksums of all three active values outputs matched across runs. The small average difference at 1 GiB overlaps run-to-run variation; 2 GiB provided no gain. These two-run samples do not predict larger datasets. Default query memory remains **512 MiB**; authored definitions and raw sources were unchanged.

An unchanged incremental build separately reused its existing values in 2.25 seconds. Query ceilings are captured on durable jobs and inherited by children using task-local context; benchmarking never mutates shared service settings. Source-backed Dimensions stay virtual. New/changed populations and Full Refresh initialise keys/attributes, then populate columns in dependency order. Ordinary incremental builds update affected columns only. See [build barrier and recovery](ENGINE_AND_STORAGE.md#schema-reuse-and-incremental-materialisation-direction).

The memory ceiling is a query safety limit, distinct from Docker/host resources. The measured improvement came from reducing SQL planning work and bounding execution stages. Full Refresh replaces edits as usual.

Completed engine steps retain startedAt/durationMs per fenced attempt. Measure-level step `timingsMs` separates compile, preChecks, tableBuild (prepareSchema/tableSetup/insert/verifyStoredTable/total), postChecks and total. These diagnostics are stored in MongoDB `engine_run_steps`; they are not a new UI progress contract. Retries start fresh timing. The measured query limits and all semantic validation remain in force.

Incremental network builds are now available. Use UI **Build Semantic Network** for dependency-scoped reuse and **Full Refresh** for a complete source reload (one edit-replacement confirmation). New API clients should explicitly send `mode=incremental`; omitted bodies preserve legacy full-build semantics. Evaluation manifests and captured active artifacts live in MongoDB; no new physical data table is created. A compatible historical build can be bootstrapped from its captured inputs, while an unprovable baseline fails safely and requests Full Refresh. Direct out-of-band ClickHouse row changes require explicit refresh because artifact versions do not detect them automatically.

## Persistence implementation refactor

Database clients remain owned by the Mongo store and ClickHouse repository. Typed repositories reuse the active transaction session; read repositories and stateless warehouse adapters are app-scoped. Services do not execute SQL or build MongoDB selectors. Engine executor/storage/collector imports remain compatible while physical I/O implementations live under `warehouse/`. No data migration, table rename, forced rebuild or policy-version change is required. See [persistence boundaries and component lifetimes](ENGINE_AND_STORAGE.md#persistence-boundaries-and-component-lifetimes).

## One-time current-storage consolidation

Run `backend/.venv/bin/python backend/scripts/migrate_current_storage.py` to inspect, then add `--apply` to migrate the default model/revision. Maintenance drains requests/workers; wait for queued/running builds first. The script preserves current materialised values (including manual edits), selects grain sorting from initial data, creates stable views and checks count/content fingerprints before updating bindings. Source-backed lookup dimensions become view/query references. After all active outputs migrate, it deletes only obsolete engine-owned tables, never registered raw sources. It is an operator action, not service startup code. Failure leaves maintenance active for inspection/retry; original tables are retained until all copying/metadata steps succeed.

Current storage requires Atomic database support for EXCHANGE TABLES and CLONE AS support (integration-tested on ClickHouse 25.3). One permanent table per materialised dimension is the steady-state constraint; allow temporary candidates and disk headroom during rebuilds. Constant-time publication refers to metadata exchange, not the full build or synchronous old-table cleanup.

## Current-table benchmark and production sizing (7 November 2026)

The new storage path was measured on an **isolated MongoDB/ClickHouse copy of the current example**, not by migrating/deleting live tables. Nine measures cover three materialised dimensions: ProdLocWeek (576 rows), product (9), BrandZoneWeek (144). ClickHouse 25.3, four query threads; host has 12 logical CPUs / 18 GiB RAM and Docker exposes about 7.26 GiB. Query-memory ceilings are distinct from server/container RAM. No container resource allocation was changed.

After one warm-up Full Refresh, two measured runs per ceiling produced:

- **512 MiB:** 18.847 / 19.601 seconds; mean **19.22 seconds**.
- **1 GiB:** 19.501 / 19.583 seconds; mean **19.54 seconds**.
- **2 GiB:** 19.668 / 19.473 seconds; mean **19.57 seconds**.
- **Unchanged incremental:** **2.14 seconds**, reused existing values.

Every measured refresh produced identical refreshed output checksums. The differences between memory settings are smaller than run-to-run variation: there is no evidence that raising this example's query limit above 512 MiB speeds it up. These are small-data end-to-end timings, not million-row throughput or concurrency guarantees; they include orchestration, compilation, validation, writes and exchanges.

The existing stored example has discount_pct=10 across all 576 Base rows. A fresh calculation against its captured definitions/current promotion source instead yields 26 populated cells (15/20/50) and 550 NULLs. Migration preserved the stored result exactly. An isolated **migration → unchanged incremental** test completed in **2.28 seconds**, reported reuse and retained the original checksum. Full Refresh intentionally uses the fresh-source result; its warm-up establishes the baseline for the six timed runs. No claim is made about why the earlier stored result differs, or about a like-for-like speedup over that older output.

### Millions of rows: architecture before hardware

Current source uploads retain the intentional 5,000-row CSV limit; larger ingestion should use bulk warehouse loading/registration. Planning population defaults to one million rows; date/multi-grain expansion has its own bounds. Size the **expanded intersections**, not just source row counts or number of tables. Fifty source tables should not imply fifty duplicated populations: lookup dimensions use views, and stored measure targets remain Native/Base/compatible native dimensions.

The current bounded column writer fetches one measure's results into Python and applies synchronous mutations in batches of 1,000 cells. This preserves existing validation/edit semantics and works for the current example, but it is **not an efficient bulk writer for millions of cells**. More RAM alone will not fix repeated mutation scans or Python result buffering. Before a million-row production rollout:

1. Add a server-side bulk materialisation operator: aggregate each transaction once at its native grain; evaluate dependent formulas in stages; construct the build candidate using narrow, set-based joins/projections. Avoid transferring entire measure columns through the API process. Keep the clone/exchange publication boundary and use a separately bounded path for small interactive edits.
2. Give build workers separate processes/resources from API reads. Retain durable jobs, admission limits, cancellation/fences and network-level publication protection; a replica alone does not solve coherent multi-table activation.
3. Bound concurrent builds and query memory together. Use version-tested spill-capable grouping/sorting/joins and NVMe spill space; do not raise every query's limit to the entire server RAM. Validate policy-aware AVG, Time ordering, NULL/Mixed and AggregateFirst behaviour on each bulk operator.
4. Record actual peak memory, read rows/bytes, elapsed CPU, spill volume, mutation/merge backlog and part counts from query/system logs. Benchmark 1M/10M expanded intersections with realistic attribute widths, sparse measures, skew and concurrent pivots before claiming a capacity target.
5. Keep disk headroom for live plus all candidate outputs retained until network activation, and for merges/spill. Clone creation shares unchanged immutable parts, but full rebuilds/new parts still consume space. Base this on the full materialised working set; one permanent table does not mean zero temporary disk demand.

### Starting resource budgets—not capacity guarantees

For a first production load test of 1–10 million intersections and modest concurrency, start ClickHouse at **8 vCPU / 32 GiB RAM / local NVMe SSD**. For wider models, expensive joins, many active measure targets or concurrent pivots, use **16 vCPU / 64 GiB** as a more comfortable starting point. These are engineering estimates, consistent with ClickHouse's general-purpose 4 GiB-per-vCPU guidance, not measured requirements for this application. [Official sizing guidance](https://github.com/ClickHouse/ClickHouse/blob/master/docs/guides/oss/best-practices/sizing-and-hardware-recommendations.mdx)

On a 64-GiB node, an initial test budget could be 2–4 GiB per interactive query, 8–16 GiB per build, 4–8 threads per build, and one active build until concurrency tests establish headroom. Reserve memory for ClickHouse merges, caches, OS and competing queries; account for the sum of admitted workloads. Lower budgets and/or spilling may be appropriate on 32 GiB. Join algorithm/spill settings must be checked against the deployed release; do not assume options introduced after 25.3 are available. [Official join guidance](https://github.com/ClickHouse/clickhouse-docs/blob/main/docs/guides/joining-tables.md)

Budget API separately at roughly **2–4 vCPU / 4–8 GiB**, and a compilation/build worker at **4–8 vCPU / 8–16 GiB** initially. With bulk work kept in ClickHouse, worker RAM should be bounded rather than proportional to all measure cells. Production metadata should use a MongoDB replica set; **2–4 vCPU / 8–16 GiB per member** is a starting estimate for this metadata-only workload, to refine from metadata/trace retention and concurrency. Scale CPU for sustained compute saturation, memory for proven working-set/spill pressure, and storage for measured I/O demand; scale out/replicate for capacity and availability after single-node profiling.

Live consolidation was subsequently approved and applied. Verification found exactly **three permanent generated tables**, all with views, no temporary build objects and no `__semantic_generation` columns. Every registered raw source retained its name, and active output checksums were unchanged. The first live unchanged incremental build completed in **2.65 seconds** with `reused=true`; no live Full Refresh was performed. Recorded historical Build SQL is unavailable for pre-capture builds; their next actual rebuild will capture execution statements.

These sizing estimates assume dedicated resources and realistic load testing. The current executor targets owned MergeTree tables in an Atomic database; replicated/distributed/cloud-specific engines need an explicit publication/ownership adapter and multi-node consistency design. Production replication and external backups are separate work from the temporary clone safety mechanism.

### What Python buffering and 1,000-cell mutations mean

Measure computation already runs in ClickHouse. In `warehouse/in_place_values.py`, Python fetches an entire measure result with `.result_rows`: grain-key JSON, value and associated flags. It checks row count/uniqueness/NULL contracts, sends updates in batches of 1,000 native cells using parameter arrays, waits synchronously (`mutations_sync=2`) for each `ALTER TABLE ... UPDATE`, then reads the stored measure back and compares dictionaries. This operates on the build candidate, so failure does not change the live table.

Batches bound command payloads and provide cancellation/fencing checkpoints; they do **not** bound total Python result memory. One million Float64 values occupy about 8 MB before keys, flags, Python objects, sets/dictionaries, transport and the verification read. The actual Python footprint depends on key widths/types and can be much larger. Results are processed one measure at a time, rather than holding the complete wide model at once.

For one million native rows and ten updated measures, 1,000-row batches mean roughly **10,000 mutation commands**, plus calculation and verification queries. ClickHouse update mutations process affected parts and rewrite updated data; repeated small batches can revisit the same parts. The current predicate uses JSON-encoded grain keys, so adding grain sorting does not automatically make those mutations efficient primary-key range updates. [ClickHouse mutation semantics](https://github.com/ClickHouse/docs-reference/blob/master/docs/en/sql-reference/statements/alter/update.md)

Bulk materialisation should keep the row stream inside ClickHouse: aggregate transaction sources at native grain using configured policies, merge by keys, calculate formulas in dependency order and populate the candidate through a bounded number of set-based SQL stages. Python should coordinate plans, validation summaries, leases and activation. Retain incremental dependency selection, unchanged edits, one permanent table, temporary build candidates, and exchange publication. This is a separate engine-write-path change, not a bigger batch-size setting or a return to one giant recursive query. Its exact incremental SQL strategy must respect the deployed ClickHouse 25.3 capabilities.
