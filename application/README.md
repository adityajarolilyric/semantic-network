# Semantic application

The React/TypeScript workspace for `backend`: upload CSVs to ClickHouse, preview stored data, and capture semantic definitions end to end in MongoDB. This is the only application UI; root `npm run dev` and `npm run build` use these directories.

## Container startup

Run `docker compose up --build -d --wait` from the repository root to build and start all four services. Open http://localhost:5174. See [Compose deployment](../docs/OPERATIONS.md#full-stack-startup).

## Develop from the repository root

```bash
npm run setup
npm run db:up
npm run dev
```

Open [the new UI](http://127.0.0.1:5174). The API runs on 8788, with [Swagger UI](http://127.0.0.1:8788/docs). The database command starts metadata MongoDB on 27018 and local ClickHouse on 8123; it preserves volumes.

If ClickHouse is already running, keep it and start only the metadata database:

```bash
docker compose -f backend/compose.dev.yml up -d --wait metadata-mongo
```

Configure the existing warehouse with `METADATA_CLICKHOUSE_*` in `backend/.env`. No database name, connection profile, or credentials are sent by the browser or stored in semantic source definitions.

Run only this UI with `npm run dev:application`. Its Vite server proxies `/api` to `http://127.0.0.1:8788`. Override `METADATA_API_TARGET` in `application/.env.local` for another backend. If API authentication is enabled, set `METADATA_API_KEY` in that server-only file or export it before starting Vite. Never put secrets in `VITE_*` variables.

## Source behavior

- Select/drop **up to 10** CSV files, review each table name, and upload. Files are processed sequentially; per-file limits still apply. Failures do not block later files, and retry skips successful imports.
- The backend accepts UTF-8 comma-delimited CSV with a header, including BOM, quoted commas, escaped quotes, CRLF, and multiline cells.
- Limits are **5,000 data records**, excluding the header, and **10 MiB** by default. Blank physical lines are skipped; multiline records count once.
- Headers and string values are preserved. Duplicate/empty headers, outer header whitespace, inconsistent row widths, invalid encoding, and oversized cells are rejected rather than silently repaired.
- Integers, exact decimals, ISO dates, and lowercase booleans are inferred conservatively. Leading-zero IDs and ambiguous/mixed values remain strings. Empty cells become null. Columns are nullable only when needed; header-only unknown columns are nullable strings.
- Existing names return a conflict; tables are never overwritten. Unchanged retries keep their idempotency key; changing the file/name generates a new key.
- Successful imports are selected and previewed from ClickHouse. Data/catalog remain available after browser reload.
- Previews show physical column names/types, nulls, 25/50/50-row pages, and exact totals. Ordering is by column values, not CSV line order.
- Catalog results include pre-existing warehouse tables and exclude private staging tables. The first 1,000 tables are returned with an explicit truncation notice if needed.

On an empty installation, a default Planning Model and its first draft are created automatically. Model/revision selectors and new-version actions appear only on Sources. Mark a table as **Managed** in the source catalog grid, next to its preview shortcut. This captures the warehouse schema and saves its registration in MongoDB in one action. Business keys are chosen later in dimensions or transactions.

Continue through **Sources → Dimensions → Dimensions → Transactions → Measures**. Managed Tables is hidden from the left navigation; register managed inputs on Sources and configure object-owned transformations in Dimensions/Transactions. Managed Tables use the existing `inScope` availability flag plus a saved transformation definition and revision-owned view. Set the revision's base dimension on Dimensions. Dimensions use a single column grid for source names, editable display names, keys, and exposure; all columns start exposed and users can deselect non-key attributes. Key columns are always exposed. Dimensions offer Select all/Deselect all for dimension attributes; distinct projections inherit complete parent lookup mappings from retained columns. Binding rows separately select a dimension column and any column from the chosen dimension, including non-primary attributes. Grain names are inherited and cannot be edited in the UI. Technical IDs are internal. Dimensions also support time configuration; measures support referenced values and calculated formulas such as `ABS(FQTY-SQTY)` with AggregateFirst. Referenced measures select their Transaction and Value column directly below Measure type and use Float64 internally, with no data-type controls in the form. Precision/Scale controls are hidden; Dis-Aggregation policies appear below Aggregation policies; new Reference and CalculateFirst measures default to Weighted by self for edits. Existing saved policies are preserved. Source materialisation follows aggregation-derived split/repeat. Unit and Aliases are hidden; existing metadata is preserved and undeclared units remain unspecified. Transaction creation does not ask for expected rows per grain. Execution aggregates each transaction by its mapped native keys, then LEFT JOINs it to the native planning population. Managed views, planning populations and measure materialisation are implemented.

Each definition has create/edit/delete controls. Saved definitions are reloaded from MongoDB; the browser stores navigation and unsaved forms only. Validate and publish a completed revision, then use New Draft on Sources to clone it for later changes. A stale edit returns a conflict instead of overwriting another user's work; reload metadata and reopen the object to reconcile it. See [metadata authoring design](../docs/METADATA.md#shared-ui-and-field-help) and the [simplified UI decisions](../docs/METADATA.md#shared-ui-and-field-help) for the complete flow.

## Measure null policies

These settings control missing values during semantic-network materialisation. The UI/API save the policies and the engine applies them to transaction aggregation, joins and measure calculations. **NULL means missing/unknown, not zero.**

### Source NULL handling (fixed)

The backend always preserves NULL values in existing transaction rows and passes them to the configured aggregation policies. There is no Source null policy control in the UI. Older `reject` definitions are interpreted as `preserve` without rewriting stored revisions. For example, an empty Sales quantity stays NULL until aggregation decides its effect.

### Null input policy (under Aggregation policies)

Controls an aggregation group that contains NULL inputs:

- **Ignore** (`ignore`, default): aggregate the non-null values. For SUM of `[10, NULL, 20]`, the result is `30`; for AVG, it is `15`. Ignore does not convert NULL to zero.
- **Propagate** (`propagate`): any NULL input makes that group's result NULL. SUM of `[10, NULL, 20]` therefore returns NULL.

Source NULLs always reach this step. At later rollups, NULL measure values can also come from missing transaction matches or previous calculations.

### All-null result policy (under Aggregation policies)

When Null input policy is Ignore and a group has **no non-null values**, this determines the result:

- **Null** (`null`, default): SUM of `[NULL, NULL]` returns NULL, keeping “unknown/no usable values” distinct from a measured zero.
- **Zero** (`zero`): that all-null group returns `0`.

**Precedence:** Propagate always returns NULL when any input is NULL, including an all-null group. All-null result does not override Propagate; it is the fallback used by Ignore when there are no usable inputs. Real inputs `[0, 0]` sum to `0` under either all-null setting.

### Missing transaction value is a separate policy

A NULL quantity in a row is different from **no transaction row for a dimension key**. After aggregating each transaction by its native keys, the engine will LEFT JOIN it to the native dimension population. If no aggregate row matches, Missing transaction value determines `null` (default) versus `zero`.

All-null result Zero does not by itself fill an absent native transaction group. An all-null group contains rows; an absent group has no row to join. Null input and All-null result policies also apply when subsequently rolling measure values up to a coarser grain.

The calculated-measure definition also has a formula-level `nullInputPolicy`, currently fixed to `propagate`. This is separate from aggregation: for a formula such as `ABS(FQTY-SQTY)`, a NULL input produces a NULL result unless the expression explicitly handles it, for example with COALESCE.

## Structure

- `src/lib/api.ts`: class-based typed API client and structured errors.
- `src/hooks/useSources.ts`: connection/catalog state, selection, paging, request cancellation, and stale-response protection.
- `src/components/UploadPanel.tsx`: selection, naming, sequential upload, retry identity, progress, and feedback.
- `src/components/TableCatalog.tsx`: searchable catalog and table selection.
- `src/components/DataPreview.tsx`: typed data table, null rendering, paging, and loading/error states.
- `src/metadata/`: API client, persisted model/revision context, source registration, per-context editors, formula binding, validation/publication.
- `src/App.tsx`: workspace composition; `src/styles.css`: responsive styles without runtime CDN/font dependencies.

## Verification

```bash
npm run test:ui:v1
npm run build:v1
backend/.venv/bin/ruff check backend
```

For browser tests against real MongoDB and ClickHouse, install local `mongod` and Docker, install Chromium once, and run:

```bash
application/node_modules/.bin/playwright install chromium
backend/.venv/bin/python backend/scripts/test_csv_integration.py --with-ui
```

The runner creates a disposable ClickHouse container and unique test databases. Playwright starts a temporary local MongoDB replica set, the actual API, and Vite on 8798/5184. Tests cover CSV ingestion and the full sixteen-object PLT/PT metadata workflow, reload persistence, concurrent edits, validation, publication, draft cloning, and mobile layout. Temporary servers/databases/container are cleaned up afterward.

Screenshots and failure traces go to ignored `test-results/`. See [CSV source-flow design](../docs/SOURCES_AND_MANAGED_TABLES.md) for backend behavior and failure semantics.

`npm run build:v1` creates `application/dist/`. Production hosting must serve those static assets and proxy `/api` to the authenticated backend. Vite's proxy is development-only; service credentials must remain at the server/gateway.


## Managed views and pivot (2026-11-01)

The SQL engine now builds a revision-owned identity view for every newly enabled Managed Table and supports one optional pivot stage with multiple attribute/value pairs. Sources retain raw data/schema; semantic editors consume the server-derived managed schema. Duplicate pivot cells fail rather than aggregate; identity duplicates are preserved unless a managed key is declared. Builds run durably in MongoDB, activate atomically, and preserve the prior working view on failure.

Configure new object-owned pivots in Dimensions/Transactions. Existing shared pivot configurations remain accessible through their compatibility route (`?tab=managed`). New drafts use engine contract 2; older drafts can use Initialize managed views on Sources. Cloned revisions need their own view builds. Planning-table and measure execution are implemented; see Decision View for semantic-network builds.

See [SQL engine implementation, APIs, recovery and revision behavior](../docs/ENGINE_AND_STORAGE.md) for authoritative details and test commands.


### Pivot output selection simplification (2026-11-01)

Pivot outputs now use the same checkbox-list control as Retain / group by columns, with Select all and Deselect all. Discovery selects every discovered output by default. Deselected options stay visible and can be reselected while editing. The UI no longer has individual output-name, expected-attribute-value, expected-output-name, or value-type inputs. Existing saved output names/IDs remain intact; expected-value and alias support in the metadata API is unchanged. Refresh discovery to show other source values when reopening a saved subset.


## Dimension tables (2026-11-01)

Planning-table generation is implemented through the shared engine: single managed populations, Cartesian factors, distinct parent projection, population attributes and dimension enrichment, with complete grain/lookup checks and revision-owned MergeTree artifacts. Add dimension tabs incrementally; select grain keys and population attributes side by side. Save stores metadata and Preview queries up to 50 rows without creating tables. Build Semantic prepares populations and values together. Contract-2 publication requires ready planning tables. Decision View builds transaction-backed and calculated measure values across compatible levels.

See [dimension implementation, APIs, migration and limits](../docs/DIMENSIONS.md) for the current operating contract. Earlier statements that planning materialization is deferred are superseded by this increment.

## Semantic network materialisation

**Decision View** contains the build controls, pivot and materialised-table preview; there is no separate Build Semantic navigation item. Build managed views and planning populations, then use **Build Semantic Network** to populate compatible measure representations. The Materialised table dropdown below the pivot lists built tables; selecting one previews up to 50 saved rows with a total count. Build Semantic Network is incremental; Full Semantic Refresh requires confirmation and replaces manual edits. All measure rollups use the configured Time/NonTime policy. Dis-Aggregation None/null repeats values; it never selects AVG implicitly. AggregateFirst recalculates at each target grain. Generations activate atomically across levels. Interactive measure editing is available in Decision View.

See [implementation, policies and APIs](../docs/MEASURES.md).

## Reset workspace

Sources offers **Nuke everything** with one confirmation. It removes all application metadata (including published revisions) and all tables/views in the configured ClickHouse database, then leaves a fresh default semantic model and draft revision. Active application operations are fenced/drained first. A failed reset stays in maintenance and can be retried from Sources. See [scope, coordination and recovery](../docs/OPERATIONS.md#workspace-reset).

Attribute-grain levels such as Brand × Zone × Sold To use distinct selected parent columns. Optional bindings check membership; selected extra attributes must be unambiguous without changing grain. See [attribute-grain dimensions](../docs/DIMENSIONS.md#relationships-and-base).

## Measure numeric policy

Referenced measures always use Float64, including when their source column is integer or Decimal, so disaggregation can produce fractions. New calculations default to Float64; explicit Decimal calculations remain supported. Data-type controls are hidden. Float64 is approximate, so tiny rounding differences are expected. Rebuild existing draft values in Decision View to apply the policy. See [numeric types and compatibility](../docs/MEASURES.md#numeric-policy).

Decision View sits below Measures. Select dimension attributes for Rows and Columns and choose measures; measures are rendered on rows. Values and calculation policies come from the backend. See [query behavior](../docs/DECISION_VIEW.md).

Dimension actions are Cancel, Preview and Save at bottom right. Preview queries proposed data without building a table. Save stores metadata; Build Semantic owns physical materialisation. See [editor lifecycle](../docs/DIMENSIONS.md#save-preview-and-build-semantic).

## Multi-grain transactions

Transactions support **Resolve multi grain** with fixed or level/value matching, Split/Copy per selected numeric column and explicit source application order. Later assignments win at any grain, including coarse rows. Preview shows up to 100 resolved rows without saving; Semantic Network builds use the same engine operator. Direct mapping remains available. See [semantics, metadata, APIs and limits](../docs/TRANSACTIONS.md).

Transactions also support inclusive **Date range** resolution through a daily Time dimension. Daily contributions map to native Day/Week/Month keys, with independent member/date Split/Copy policies and configured Time aggregation during measure loading. See [date-range resolution](../docs/TRANSACTIONS.md#date-ranges-and-native-periods).

Decision View now edits eligible referenced measures at native or proven coarser grains. Writes use Base where reachable, otherwise the native level, and update compatible coarser representations atomically. Weighted by self edits preserve NULL cells and distribute across populated values. No edit journal is retained: incremental builds preserve unchanged values, while Full Refresh replaces manual edits from sources. See [writeback semantics, API and limits](../docs/DECISION_VIEW.md#edit-eligibility-and-write-target).

Managed pivots group pair discriminators/values with retained columns automatically, collapsing identical records. For attributes on separate rows, choose optional Record keys from retained fields, such as Customer ID. No Pivot Mode or interpretation control is shown. Existing saved behavior remains compatible. Raw pair inputs are consumed; generated outputs are available as optional primary keys before Save. See [pivot grouping and output keys](../docs/SOURCES_AND_MANAGED_TABLES.md#pivot-grouping-and-final-output-keys).

Materialisation initialises finer measures from their aggregation policies: SUM axes split equally; other axes repeat, in reverse aggregation order. Dis-Aggregation policies configure edits only. Measures are stored to six decimal places and displayed to two; edit inputs retain the stored precision. Rebuild an older network in Decision View after this policy change.

Transactions support an independent Date range resolution section: select Start/End, daily Time calendar and columns (for example Week and Month), preview the expanded input, then map those columns alongside Product/Location. Configuration is server-owned; previews show up to 50 rows. Existing axis-level date ranges reopen unchanged. See [transaction design](../docs/TRANSACTIONS.md#date-ranges-and-native-periods).

Decision View remembers row/column attributes and measures in browser storage per model/revision. Returning or refreshing reloads fresh values with that layout. Selections survive Semantic Network rebuilds; **Clear selections** resets them. Removed fields/measures are dropped on refresh; values are always queried again. No result values or semantic definitions are stored in the browser.

Dimensions and Transactions now offer Raw Data Preview plus independent Pivot configuration. Normal Preview shows transformed data. Transaction authoring runs optional Pivot → Date resolution → Multi-grain resolution; new matching rules use Column equals attribute only. Raw inputs/shared Managed Tables remain unchanged. Pivot schema feeds dimension attribute capture and referenced-measure value choices, including generated numeric columns.

Dimensions, Dimensions and Transactions include a final Filters section with typed conditions and nested AND/OR groups. Normal Preview includes these rules; raw/intermediate previews do not. See [filter design](../docs/FILTERS.md).

Transaction-first onboarding: choose Create Dimension, optionally apply Pivot and Date resolution, then select effective output grain columns and Save metadata. It creates a normal source population/direct mapping without building tables; first-grain Base selection is automatic when unset. One Dimension is generated; individual Product, Location and Time Dimensions are optional. Decision View can group by dimension fields, and an existing population can be replaced with explicit source-column remapping while retaining key IDs. Missing Sales intersections remain user-managed. See [Transaction-first flow](../docs/TRANSACTIONS.md#start-from-a-table-without-an-existing-dimension).

Materialise **Build Semantic Network** uses incremental mode and preserves unchanged edited values; **Full Refresh** explicitly reloads sources after one confirmation. No-change jobs retain the active generation. Source/definition/population versions drive dependency invalidation; direct external row updates require Full Refresh. Pivot row-dimension and Measure columns are pinned using measured widths. See [incremental semantics](../docs/MEASURES.md#incremental-build-and-full-refresh).


The formula library groups functions by Arithmetic, Conditional, Logical, Text, Date, Counting and Time series. Temporal expressions automatically select CalculateFirst and expose calendar/series settings. Referenced measures have a Units and currency panel for defining a stored conversion factor or applying one through multiplication/division. Factors use existing Transactions and measures; they are read-only unique values and repeat at finer proven grains. See [measure rules](../docs/MEASURES.md#stored-conversion-factors-and-converted-references).
