# Sources and Managed Tables

Sources describe physical ClickHouse tables; Managed Tables expose revision-specific identity or pivot views. All downstream semantic access uses the saved managed view and server-derived effective schema. A missing view is unavailable—there is no raw-table fallback or conditional transformation path. Database selection and credentials remain server-owned.

## CSV import and preview

Select/drop **up to 10 UTF-8 comma-delimited CSV files together**. The UI uploads them sequentially through the existing one-file endpoint, creating one **new** table per file. Bounded synchronous ingestion supports at most 5,000 data records, 200 columns, 65,536 characters per cell and a default 10 MiB file limit. The multipart envelope has its own bound.

Python's CSV parser supports BOM, quoting/escaped quotes, CRLF and multiline records. Skip empty physical lines; a multiline record counts once, and an all-empty delimited record still counts. Reject malformed quoting/encoding, inconsistent widths, empty/duplicate headers, outer header whitespace/control characters and null bytes. Headers are at most 128 characters and are never silently trimmed or renamed. Preserve string whitespace/content; empty cells, including quoted empty strings, become NULL.

Inference examines every nonempty value: signed Int64 when compatible without leading-zero identifier syntax; exact `Decimal(38, scale)` for fitting decimals/larger integers; Date32 for valid ISO dates in 1900–2299; Bool for lowercase true/false; otherwise String. Mixed values, leading-zero codes and unsupported ranges remain text. Nullability reflects actual empty cells. Header-only input yields nullable String columns and a warning. Do not guess locale, timezone, units, alternate dates or delimiters. Imports expose no append/overwrite/schema-edit operation. After physical import, the backend registers and scopes the table, builds its managed view and creates default semantic metadata as described below.

Import validates/parses first, creates a unique private MergeTree (`ORDER BY tuple()`), inserts typed rows and verifies count, then renames it to the public name. Name collisions return 409 without replacing existing data. An internal comment stores import ID and original-byte SHA-256; the same table/key/bytes replays success. Idempotency is table-scoped. Failed operations clean only their own staging object; a crash/uncertain outcome can leave hidden staging, with no automatic CSV staging collector. External changes to rows/comments are outside replay guarantees. This import staging is separate from the strict planning/value table limit.

Raw previews read persisted ClickHouse rows with exact counts and deterministic ordering over selected values, not original file order. Dates use ISO text, decimals/large integers use strings, booleans lowercase text and NULL JSON null. Identical duplicate rows remain indistinguishable. Large pre-existing tables can make count/sort expensive; the upload cap does not constrain every catalog table.

Warehouse endpoints, relative to `/api/v1/warehouse`:

- `GET /status`: connection, display-only database and upload bounds.
- `GET /tables`: catalog excluding engine/staging objects; at most 1,000 entries with a truncation flag.
- `POST /imports/csv`: one multipart `file`, `tableName`, required `Idempotency-Key`; rejects extra database/configuration fields; returns 201 with schema/count/warnings/replay state.
- `GET /tables/{table}/preview?offset=0&limit=50`: limit 1–200, offset 0–1,000,000.

New source names start with a letter/underscore, contain only letters/digits/underscores and are at most 128 characters; `__csv_ingest_` is reserved. Existing catalog names with spaces are safely quoted for preview. Bind catalog values; quote identifiers/DDL literals. Browser uploads use FormData with its generated multipart boundary.

The upload queue shows an editable table name and status per file. Filename-derived suggestions are disambiguated within the batch (including sanitized/truncated-name collisions); duplicate edited names fail before any request. Validate file count, extension and size before replacing the queue. Controls are disabled while uploading. Each request keeps the existing per-file row/size/parse/type checks. A failure does not block later files; Retry submits only failed/pending entries and never reimports successes. Unchanged retries retain each file's own idempotency key; changing its table name creates a new key. A failed catalog refresh after a successful import is a display warning, not a failed import. Catalog/preview refresh after each success. Keep the page open to finish the browser-owned queue; reload loses pending selection/status, not already-created tables. No bulk backend endpoint or client-side data persistence is added.

## Automatic semantic setup after upload

The CSV endpoint orchestrates setup in the default workspace through app-scoped services; the browser only uploads files and refreshes metadata. All new uploads are scoped through the existing managed-view lifecycle. No Base Dimension or shared Dimension binding is inferred.

- **No numeric columns:** create a source-backed Dimension named after the managed table. All columns are grain keys and exposed attributes; column display names default to physical names. Do not infer a Time Dimension from column names or date types.
- **One or more numeric columns:** create a standalone Transaction named after the managed table. All non-numeric columns form its grain; every numeric column becomes a referenced measure named after the column. Repeated source keys are valid: configured aggregation combines them.
- Numeric means the existing supported integer, floating-point and decimal types, including nullable wrappers. Dates, booleans and strings are non-numeric. Numeric identifiers are deliberately treated as measures under this default; users can revise the generated metadata.
- Generated measures use Time **Sum**, Non-Time **Sum**, editable **false**, missing value **NULL**, Non-Time then Time order, ignore NULL inputs and all-NULL result **NULL**. Source NULLs are preserved; the existing Weighted by self edit policy remains the default. Time key stays unset until explicitly selected.
- A numeric-only table has no inferable grain. It is scoped and returns a warning asking the user to select grain keys manually. The same manual fallback applies beyond 32 inferred keys or 128 generated measures; do not truncate columns or invent keys. Existing NULL-key validation remains in force.

**Reason:** these are editable onboarding defaults, not claims about business identity. Users can reach analysis from a single table and later bind compatible Transactions to explicitly defined Dimensions.

The managed view must activate before dependent metadata is created. Transaction plus measures are inserted in one Mongo transaction after full semantic validation; failure leaves no partial Transaction/measure set. The physical CSV import and managed-view activation have separate lifecycles. The request waits up to 30 seconds for the view; incomplete setup returns `semanticSetupComplete=false` plus warnings while retaining the uploaded data. The UI keeps that file retryable using its original import key. A retry reuses physical data and ready managed views, completes missing setup and never overwrites an existing Transaction/source-backed Dimension or user-edited measures. Stable source-derived identities prevent duplicate generated objects. No automatic Semantic Network build is triggered.

## Registration and managed availability

The Sources grid has a Managed checkbox beside preview. Enable registers the raw warehouse schema if needed, then builds an identity/saved managed view; availability activates only after success. A failed first enable may leave a raw registration with `inScope=false`. Disable is dependency-aware, preserves raw data and saved managed configuration, and cannot remove something still used downstream. `source.definition.inScope` is the sole availability flag.

Raw registration (`POST .../source-registrations`) accepts table name, optional ID/keys/scope/description; the service discovers authoritative columns/types. Keys default to empty. Automatic source IDs derive deterministically from the table-name hash within model/revision, avoiding duplicate automatic registrations; explicit IDs remain supported. Captured source-local column IDs remain stable through cloning. Nullable key declarations are rejected, but registration does not attest live uniqueness or reconcile later schema drift. Contract-2 management uses the managed lifecycle rather than independent source-scope writes.

`semantic_managed_tables` holds one definition per `(modelId,revisionId,sourceId)` with version/schema version/hash, `primaryKeyColumnIds`, pipeline and server-derived columns. Keep raw Source schemas unchanged. `EffectiveSchemaResolver` supplies effective managed columns to graph validation and all downstream editors.

## Identity and pivot semantics

An empty pipeline projects captured raw columns through a normal view, preserving all rows/duplicates. One optional pivot stage supports multiple attribute/value pairs:

- Stage: stable stageId, enabled, groupByColumnIds (retained raw columns), recordKeyColumnIds and pairs.
- Pair: stable pairId, raw attribute/value IDs, and selected outputs with typed literal, alias and server-verified output ID.
- Retained columns keep IDs. Generated IDs hash source/stage/pair/typed literal, excluding alias, grouping, order and revision. Lineage records those inputs. Cloning and disabling/re-enabling preserve identities.

## Pivot grouping and final output keys

New pivots have explicit recordKeyColumnIds and no mode/resolvedMode. Grouping follows configuration only; there is no data-layout detector, completeness profile or ambiguity-driven fallback.

- **Empty record keys:** automatically GROUP BY retained columns plus every selected pair's raw attribute and value columns. Conditional expressions produce named outputs without splitting rows. Exact grouping tuples collapse, including NULL tuples. Attribute discriminators prevent values with different meanings from merging. For Forecast, retain Week/Quantity and select Product attribute/value → Brand and Location attribute/value → Zone. Distinct Brand/Zone/Week/Quantity combinations survive even when quantities match. Differences only in unselected source columns do not create separate records. To preserve repeated occurrences, retain an actual occurrence identifier.
- **Record keys supplied:** keys must be distinct retained source columns, excluding consumed pair inputs. GROUP BY those entity keys, for example Customer ID when Channel and Sold To arrive on separate rows. Other retained columns must have one distinct value per key tuple, including NULL; conflicts fail conflicting_retained_values rather than selecting an arbitrary value. Each selected pivot cell is (record keys, pair, literal); multiple contributing rows fail duplicate_pivot_cell even when values are identical. No SUM/MAX/FIRST policy is inferred.

Raw pair inputs are consumed and excluded from the output/primary-key choices. Scalar types and units are preserved; absent/unselected outputs are NULL and zero remains zero. Separate raw grouping tuples can project the same visible NULL outputs when literals are unselected; the optional output key still validates the actual output. Discovery supplies available literals, not a grouping decision. New literals never silently widen saved columns. Expected literals/aliases remain available through the API.

The optional output primaryKeyColumnIds is independent of transformation grouping and adds only a duplicate-output-key check. NULL key values are not separately rejected. Grouping and carried-value guards run over full inputs before/after view creation and on preview/publication; diagnostics are bounded to 20 groups. Tuple-wrapped bounded arrays preserve NULL cells and reject ambiguity. The preview LIMIT does not certify uniqueness.

POST /managed-tables/{source}/schema derives names/types/lineage/stable output IDs from registered metadata only, with no warehouse profiling or writes. The editor debounces/cancels stale analysis and offers generated columns for key selection before Save. Save validates selected keys and revision dependencies. Schema errors block Save and offer Retry output columns.

Legacy compatibility: omitted/null mode retains historical merging by all retained columns; expand_rows retains row-preserving projection without deduplication; previously saved auto plus resolvedMode runs its frozen operation. Opening or saving an unchanged pivot preserves its fields, hashes, IDs and compiler version. Editing its pivot configuration in the UI adopts explicit grouping: former merge configurations inherit their retained grouping keys; former row expansion adopts value grouping. Changing only the optional output key preserves the saved operation. Unresolved old auto requests require explicit grouping instead of reintroducing inference. Legacy mode fields cannot coexist with recordKeyColumnIds. No startup migration or data rewrite occurs.

A normal view freezes its definition, not source rows. Guards are not continuously enforced warehouse constraints; external writes can race application checks. Imported CSVs have no row-edit API.

## Managed editor

Save, Preview and Close sit at the bottom right below Optional managed output primary key:

- Save builds and persists configuration, stays open and reloads the normalized saved definition/version. A failed save preserves unsaved inputs and the previous working view.
- Preview reads the **saved view**. With unsaved changes, ask to Save/build first; declining performs neither operation. Editing clears an old preview. Use the shared Source `DataPreview`, capped at 50 rows without an extra refresh action.
- Close returns to the list, confirms discarding unsaved changes and performs no build/save. Disable it during an operation. Published definitions allow saved previews and tab browsing, not mutation.

With Pivot enabled, show retained columns and optional record keys left and compact pair tabs right, stacking at widths ≤1100px. Only the active pair expands; tabs preserve configurations, show mapping/output counts and support arrows/Home/End. Discover is a button beside the attribute/value choices; Remove pair occupies that compact action area. Outputs remain checkbox lists with Select all/Deselect all. Discovery selects all initially; deselected choices remain visible. Do not show expected-value, output-name or value-type inputs. Reopening must initialize from the complete saved Source/managed attachment, not the raw API serializer that strips it.

## Managed APIs, discovery and compatibility

Paths below are relative to `/api/v1/models/{model}/revisions/{revision}`:

- `GET /managed-tables` and `GET /managed-tables/{source}`: paginated definitions, ETags, schema/readiness.
- `POST /managed-tables/enable`: `{tableName}`, Idempotency-Key; register/build, HTTP 202.
- `PUT /managed-tables/{source}`: definition, If-Match, Idempotency-Key; queue save/build.
- `POST /managed-tables/{source}/disable`: If-Match; preserve configuration/data.
- `GET /managed-tables/{source}/attribute-values?columnId=...&offset=0&limit=200`: structured values/nextOffset. Default reads raw input for Pivot; `managedOutput=true` leases/verifies saved output for transaction selector discovery and returns the artifact ID.
- `POST /managed-tables/{source}/schema`: metadata-only proposed output analysis, including generated IDs before Save.
- `GET /managed-tables/{source}/preview`: checked active-view rows, maximum 50. Compatibility POST accepts an unsaved proposed definition without saving/creating an artifact; current UI does not use it.
- `POST /managed-tables/{source}/ensure-view`: Idempotency-Key; repair unchanged saved definition, including published views, without changing semantics.
- `POST /managed-tables/upgrade-contract`: revision If-Match, after enabled tables are initialized.

Limits: one stage, 20 pairs, 200 outputs/pair, 32 retained/grouping or key columns and 2,000 total outputs. Discovery exhausts pages before Select all and fails clearly beyond bounds; never silently select a first page. Typed literals support strings/numbers/booleans/NULL, including quoted/comma-containing text; unsupported nonscalar value types fail analysis. Referenced output removal or incompatible pivot disabling fails graph validation rather than clearing keys/dependencies.

New/UI revisions use contract 2. Initialize managed views on Sources builds missing views sequentially before upgrading an old draft. Contract-1 published metadata stays frozen; compatibility derives an identity definition and ensure-view provisions it operationally. Clones copy definitions/IDs, reset versions and create their own artifacts. Publication checks ready saved views, live schema/ownership and duplicate probes before the final revision-guard transaction.

## Implementation and checks

Warehouse modules: `api/warehouse_sources.py`, `warehouse/{csv_parser,service,repository,schemas}.py`. Managed modules: `schemas/managed_tables.py`, `services/managed_tables.py`, `engine/{planner,compiler,executor,resolver}.py`, separate ManagedTablesAPI/EngineAPI. The lazy warehouse client allows metadata-only work during warehouse downtime; blocking work runs under a guarded thread boundary and cancellation waits for cleanup.

Regression coverage includes ten-file sequential uploads, count/extension/size bounds, name collisions, partial failure/retry with stable per-file keys, catalog-refresh warnings, mobile overflow, typed CSVs, malformed headers, replay/name races/cleanup, identity duplicates, NULL pivot ambiguity, multiple pairs, stable IDs, quoted literals, failure preserving bindings, saved pivot reopening, effective-column consumption and desktop/mobile layouts. Shared execution/recovery lives in [Engine and storage](ENGINE_AND_STORAGE.md).


## Independent Dimension and Transaction pivots

Dimensions and Transactions optionally store `definition.pivot`, using the same typed PivotStage/operators as Managed Tables. Configuration is owned by that object and never edits the shared managed definition/view. Input is the saved managed output, including any pre-existing shared transformation. New owner-scoped output IDs hash owner object + stage + pair + literal; retained source IDs remain stable. Absent pivots are omitted from serialization to preserve old hashes.

Both editors offer **Raw Data Preview** (physical imported data), an optional Pivot section, and normal **Preview** (the transformed object output). All previews use the shared Source table component and show at most 50 rows; data checks cover complete inputs. Dimensions apply pivot before their column mappings/filters/exposure; transactions apply pivot before Date resolution and Column-equals-attribute multi-grain matching. Retained columns and optional record keys follow the existing grouping contract. Generated names are disambiguated on collision; semantic display names remain independent.

No extra physical staging/output table or persistent view is created for an object pivot. Planning dimension lookups, calendars, transaction resolution, materialisation and writeback use the same compiled input. Existing dimension-key nullability and mapping contracts still apply.

Metadata-only schema and read-only preview endpoints (revision scoped):

- `POST /dimensions/input/schema`, `/dimensions/input/preview`.
- `POST /transactions/input/schema`, `/transactions/input/preview`.
- Payload: `{objectId, sourceId, pivot}`; omit/null pivot for the managed input schema.
- `POST /dimensions/preview` accepts a full DimensionInput and previews its exposed transformed columns.
- `POST /transactions/resolution/preview` accepts the full TransactionInput and previews the final staged data.

Schema discovery can run before native mappings are complete. Previews capture/renew managed read leases, verify input definitions and reject revision changes; they do not save metadata. Object pivots are committed with normal object CRUD and execute from frozen metadata snapshots when consumed. Previously saved Level/Value rules remain readable/executable during migration; the new UI creates only Column equals attribute rules.

Preview UX: all table samples are capped at 50 rows and display shown/total rows (for example `50/159`); counting does not limit builds or full-input checks. Dimension/Transaction actions appear as Cancel → Raw Data Preview → Preview → Save metadata. Pivot and date resolution have collapsible configuration sections with status; object-owned transformations remain independent.

## Pivot suggestions

`POST /models/{modelId}/revisions/{revisionId}/managed-tables/pivot-suggestion` accepts `{ "tableId": "source_object_id" }`. The table ID is the revision-scoped Source identity, not a physical name or database. The API reads the saved managed input, before Dimension/Transaction-local transformations, and returns `tableId`, a standard `pivot` configuration or null, `confidence`, `rowsExamined`, `complete`, `reasons` and `warnings`. No semantic definition or warehouse view is saved.

Profile at most the **first 100 rows**, using a bounded LIMIT without a full count/sort or duplicate-validation scan. ClickHouse has no implicit source-row order. `complete` is true only when fewer than 100 rows are returned; at exactly the cap, completeness is unproven and the response warns that other selector values may exist. Never treat the sample as proof of grain, uniqueness or full selector coverage. Normal build checks remain unchanged.

The deterministic detector suggests unambiguous named attribute/value pairs (including multiple paired prefixes such as product_attribute/product_value). It collects observed selector values into output columns, retains unconsumed columns, and disambiguates output-name collisions. It does not guess unnamed or ambiguous partners. Multiple pairs preserve source records. For one pair, identifier-like retained keys are suggested for separate attribute rows only when sampled groups have unique selectors and consistent retained values; otherwise no record grouping is inferred. Numeric values are not aggregated during suggestion.

The **Suggest pivot** button in Dimension and Transaction Pivot Data fills only the editable object-local draft. Users can change every suggested field and use existing **Discover** to load additional selector values. No result leaves the current draft intact; errors remain visible. Saved managed-view ownership and schema are verified, a read lease protects the captured view, and a final binding/version check rejects input changes during profiling. Bounded reads live in the warehouse query executor; the app-scoped service and pure engine detector own orchestration and inference respectively.
