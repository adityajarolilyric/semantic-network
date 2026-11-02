# Sources and Managed Tables

Sources describe physical ClickHouse tables; Managed Tables expose revision-specific identity or pivot views. All downstream semantic access uses the saved managed view and server-derived effective schema. A missing view is unavailable—there is no raw-table fallback or conditional transformation path. Database selection and credentials remain server-owned.

## CSV import and preview

Select/drop **up to 10 UTF-8 comma-delimited CSV files together**. The UI uploads them sequentially through the existing one-file endpoint, creating one **new** table per file. Bounded synchronous ingestion supports at most 5,000 data records, 200 columns, 65,536 characters per cell and a default 10 MiB file limit. The multipart envelope has its own bound.

Python's CSV parser supports BOM, quoting/escaped quotes, CRLF and multiline records. Skip empty physical lines; a multiline record counts once, and an all-empty delimited record still counts. Reject malformed quoting/encoding, inconsistent widths, empty/duplicate headers, outer header whitespace/control characters and null bytes. Headers are at most 128 characters and are never silently trimmed or renamed. Preserve string whitespace/content; empty cells, including quoted empty strings, become NULL.

Inference examines every nonempty value: signed Int64 when compatible without leading-zero identifier syntax; exact `Decimal(38, scale)` for fitting decimals/larger integers; Date32 for valid ISO dates in 1900–2299; Bool for lowercase true/false; otherwise String. Mixed values, leading-zero codes and unsupported ranges remain text. Nullability reflects actual empty cells. Header-only input yields nullable String columns and a warning. Do not guess locale, timezone, units, alternate dates or delimiters. Imports expose no append/overwrite/schema-edit operation and do not automatically register semantic sources or infer business keys.

Import validates/parses first, creates a unique private MergeTree (`ORDER BY tuple()`), inserts typed rows and verifies count, then renames it to the public name. Name collisions return 409 without replacing existing data. An internal comment stores import ID and original-byte SHA-256; the same table/key/bytes replays success. Idempotency is table-scoped. Failed operations clean only their own staging object; a crash/uncertain outcome can leave hidden staging, with no automatic CSV staging collector. External changes to rows/comments are outside replay guarantees. This import staging is separate from the strict planning/value table limit.

Raw previews read persisted ClickHouse rows with exact counts and deterministic ordering over selected values, not original file order. Dates use ISO text, decimals/large integers use strings, booleans lowercase text and NULL JSON null. Identical duplicate rows remain indistinguishable. Large pre-existing tables can make count/sort expensive; the upload cap does not constrain every catalog table.

Warehouse endpoints, relative to `/api/v1/warehouse`:

- `GET /status`: connection, display-only database and upload bounds.
- `GET /tables`: catalog excluding engine/staging objects; at most 1,000 entries with a truncation flag.
- `POST /imports/csv`: one multipart `file`, `tableName`, required `Idempotency-Key`; rejects extra database/configuration fields; returns 201 with schema/count/warnings/replay state.
- `GET /tables/{table}/preview?offset=0&limit=50`: limit 1–200, offset 0–1,000,000.

New source names start with a letter/underscore, contain only letters/digits/underscores and are at most 128 characters; `__csv_ingest_` is reserved. Existing catalog names with spaces are safely quoted for preview. Bind catalog values; quote identifiers/DDL literals. Browser uploads use FormData with its generated multipart boundary.

The upload queue shows an editable table name and status per file. Filename-derived suggestions are disambiguated within the batch (including sanitized/truncated-name collisions); duplicate edited names fail before any request. Validate file count, extension and size before replacing the queue. Controls are disabled while uploading. Each request keeps the existing per-file row/size/parse/type checks. A failure does not block later files; Retry submits only failed/pending entries and never reimports successes. Unchanged retries retain each file's own idempotency key; changing its table name creates a new key. A failed catalog refresh after a successful import is a display warning, not a failed import. Catalog/preview refresh after each success. Keep the page open to finish the browser-owned queue; reload loses pending selection/status, not already-created tables. No bulk backend endpoint or client-side data persistence is added.

## Registration and managed availability

The Sources grid has a Managed checkbox beside preview. Enable registers the raw warehouse schema if needed, then builds an identity/saved managed view; availability activates only after success. A failed first enable may leave a raw registration with `inScope=false`. Disable is dependency-aware, preserves raw data and saved managed configuration, and cannot remove something still used downstream. `source.definition.inScope` is the sole availability flag.

Raw registration (`POST .../source-registrations`) accepts table name, optional ID/keys/scope/description; the service discovers authoritative columns/types. Keys default to empty. Automatic source IDs derive deterministically from the table-name hash within model/revision, avoiding duplicate automatic registrations; explicit IDs remain supported. Captured source-local column IDs remain stable through cloning. Nullable key declarations are rejected, but registration does not attest live uniqueness or reconcile later schema drift. Contract-2 management uses the managed lifecycle rather than independent source-scope writes.

`semantic_managed_tables` holds one definition per `(modelId,revisionId,sourceId)` with version/schema version/hash, `primaryKeyColumnIds`, pipeline and server-derived columns. Keep raw Source schemas unchanged. `EffectiveSchemaResolver` supplies effective managed columns to graph validation and all downstream editors.

## Identity and pivot semantics

An empty pipeline explicitly projects captured raw columns through a normal view, preserving all rows/duplicates. One optional pivot stage supports multiple attribute/value pairs:

- Stage: stable `stageId`, enabled flag, grouping column IDs and pairs.
- Pair: stable `pairId`, raw attribute/value IDs and outputs containing typed literal, alias and server-verified output ID.
- Retained raw columns keep IDs. Pivot IDs hash source/stage/pair/typed literal, excluding alias, order and revision. Lineage records those inputs. Persist stage/pair IDs once; cloning and disabling/re-enabling preserve them.

Retained columns define wide-row identity. Select every ordinary column that must survive; no arbitrary value is chosen for ungrouped columns. Pivot values retain the input scalar type/unit; String is not converted to numeric. Missing selected attributes yield NULL; zero stays zero. Groups with only unselected attributes still appear. New raw values do not silently widen a saved schema. API-only expected literals/aliases remain supported even though the UI offers discovery/checkboxes.

A cell is `(grouping tuple, pair, selected literal)`. More than one contributing row fails, including identical values or multiple NULLs. There is no MAX/SUM/FIRST/deduplication policy. To preserve repeated records, retain an existing record/occurrence ID; never invent pairing from row order. An optional managed output key adds only a duplicate-key check; NULL keys are not separately rejected. Pivot adds no range, completeness or other value-quality rules.

The compiler uses `groupArrayIf(2)(tuple(value), condition)` and `throwIf`: tuple wrapping retains NULL and two entries prove ambiguity. Full-input duplicate probes run before build, after DDL, on managed previews and before publication; at most 20 diagnostic groups are returned. Preview LIMIT never certifies uniqueness. Unselected cells do not trigger selected-cell checks. Metadata/type/reference/resource checks still apply.

A normal view freezes its definition, not source rows. Inline guards plus full preflight checks are not a continuously enforced warehouse uniqueness constraint; external writes can race them or bypass application checks. App-imported CSVs have no row-edit API.

## Managed editor

Save, Preview and Close sit at the bottom right below Optional managed output primary key:

- Save builds and persists configuration, stays open and reloads the normalized saved definition/version. A failed save preserves unsaved inputs and the previous working view.
- Preview reads the **saved view**. With unsaved changes, ask to Save/build first; declining performs neither operation. Editing clears an old preview. Use the shared Source `DataPreview`, capped at 100 rows without an extra refresh action.
- Close returns to the list, confirms discarding unsaved changes and performs no build/save. Disable it during an operation. Published definitions allow saved previews and tab browsing, not mutation.

With Pivot enabled, show retained/group-by columns left and compact pair tabs right, stacking at widths ≤1100px. Only the active pair expands; tabs preserve configurations, show mapping/output counts and support arrows/Home/End. Discover is a button beside the attribute/value choices; Remove pair occupies that compact action area. Outputs remain checkbox lists with Select all/Deselect all. Discovery selects all initially; deselected choices remain visible. Do not show expected-value, output-name or value-type inputs. Reopening must initialize from the complete saved Source/managed attachment, not the raw API serializer that strips it.

## Managed APIs, discovery and compatibility

Paths below are relative to `/api/v1/models/{model}/revisions/{revision}`:

- `GET /managed-tables` and `GET /managed-tables/{source}`: paginated definitions, ETags, schema/readiness.
- `POST /managed-tables/enable`: `{tableName}`, Idempotency-Key; register/build, HTTP 202.
- `PUT /managed-tables/{source}`: definition, If-Match, Idempotency-Key; queue save/build.
- `POST /managed-tables/{source}/disable`: If-Match; preserve configuration/data.
- `GET /managed-tables/{source}/attribute-values?columnId=...&offset=0&limit=200`: structured values/nextOffset. Default reads raw input for Pivot; `managedOutput=true` leases/verifies saved output for transaction selector discovery and returns the artifact ID.
- `GET /managed-tables/{source}/preview`: checked active-view rows, maximum 100. Compatibility POST accepts an unsaved proposed definition without saving/creating an artifact; current UI does not use it.
- `POST /managed-tables/{source}/ensure-view`: Idempotency-Key; repair unchanged saved definition, including published views, without changing semantics.
- `POST /managed-tables/upgrade-contract`: revision If-Match, after enabled tables are initialized.

Limits: one stage, 20 pairs, 200 outputs/pair, 32 grouping/key columns and 2,000 total outputs. Discovery exhausts pages before Select all and fails clearly beyond bounds; never silently select a first page. Typed literals support strings/numbers/booleans/NULL, including quoted/comma-containing text; unsupported nonscalar value types fail analysis. Referenced output removal or incompatible pivot disabling fails graph validation rather than clearing keys/dependencies.

New/UI revisions use contract 2. Initialize managed views on Sources builds missing views sequentially before upgrading an old draft. Contract-1 published metadata stays frozen; compatibility derives an identity definition and ensure-view provisions it operationally. Clones copy definitions/IDs, reset versions and create their own artifacts. Publication checks ready saved views, live schema/ownership and duplicate probes before the final revision-guard transaction.

## Implementation and checks

Warehouse modules: `api/warehouse_sources.py`, `warehouse/{csv_parser,service,repository,schemas}.py`. Managed modules: `schemas/managed_tables.py`, `services/managed_tables.py`, `engine/{planner,compiler,executor,resolver}.py`, separate ManagedTablesAPI/EngineAPI. The lazy warehouse client allows metadata-only work during warehouse downtime; blocking work runs under a guarded thread boundary and cancellation waits for cleanup.

Regression coverage includes ten-file sequential uploads, count/extension/size bounds, name collisions, partial failure/retry with stable per-file keys, catalog-refresh warnings, mobile overflow, typed CSVs, malformed headers, replay/name races/cleanup, identity duplicates, NULL pivot ambiguity, multiple pairs, stable IDs, quoted literals, failure preserving bindings, saved pivot reopening, effective-column consumption and desktop/mobile layouts. Shared execution/recovery lives in [Engine and storage](ENGINE_AND_STORAGE.md).
