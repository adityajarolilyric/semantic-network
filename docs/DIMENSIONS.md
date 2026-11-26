# Dimensions

A Dimension defines valid intersections: its unique grain keys, descriptive attributes, population and optional relationships. Product (Product ID) and ProductLocationTime (Product ID, Location ID, Week ID) use the same concept. There is no separate user classification for single-key and compound Dimensions.

## Metadata and ownership

Persist one `kind: "dimension"` aggregate in MongoDB. Its `definition` contains `population`, `grain`, `populationAttributes`, `memberBindings` and `filters`. A table-backed Dimension can additionally own `memberCatalog` metadata: source columns, labels, exposed attributes, keys, Time configuration, pivot and filters. `memberCatalogId` is the stable identity of that embedded component, not a separate persisted semantic object. `isTimeDim` belongs to the Dimension root.

The engine derives transient member lookup nodes from these aggregates. This lets relationship, calendar and formula operators reuse the same member schema without duplicating authoring or creating another MongoDB object. Source-member editing is a capability facade over `DimensionService`; it uses the same repository and version guard. Changes commit as one Dimension update.

The revision stores `baseDimensionId`. A Transaction stores exactly one `definition.dimensionId`; every Dimension grain key must be mapped. Measures store `nativeDimensionId`. Stable column IDs remain independent of physical names and display labels. Selecting an ordinary attribute does not turn it into a grain key.

## Population methods

- **From a Table:** distinct selected intersections from a managed input. A table-backed Dimension may also configure its member columns, display names, exposure and Time semantics.
- **From Multiple Tables:** Cartesian product of selected factor relations. Preserve existing Product/Location combinations as one factor; do not infer equality joins from matching names or split valid intersections into independent domains.
- **From Existing Dimension:** distinct projection using retained parent keys and/or parent attributes promoted to keys. Ignore unselected parent columns; selected descriptive attributes do not change the new grain.

Apply final filters and SELECT DISTINCT to public outputs. Identical rows collapse. A grain must be non-NULL and determine one attribute combination. Conflicting attributes fail; do not silently choose MAX/FIRST. For example, Week can replace Date, but carrying a Month that varies inside the same Week is ambiguous unless the grain also determines it.

## Relationships and Base

Relationships are optional. They validate membership, bring attributes and prove compatible aggregation/edit paths. Map any compatible population field to an attribute of the related member catalog, including composite tuples. Without enrichment, multiple member rows may match. With enrichment, each referenced tuple must determine one distinct requested attribute combination. Missing members and conflicting lookups fail rather than silently dropping intersections or choosing arbitrary rows.

Projection lineage can prove a path without explicit member bindings. Names and key counts alone cannot. A Brand mapping does not make Product ID available. Finer expansion follows actual populated intersections.

One Base Dimension is selected on the revision. It is finest among proven connected Dimensions; independent Dimensions remain valid. A measure stays native if Base is unreachable and can propagate to other proven compatible Dimensions. Editing and aggregation rules are unchanged; see [Measures](MEASURES.md) and [Decision View](DECISION_VIEW.md).

Population method is visible during both creation and editing, including source-backed Dimensions: **From a Table**, **From Multiple Tables**, or **From Existing Dimension**. Changing strategy edits the existing aggregate, retaining its Dimension/catalog identity and version guard. Changing strategy starts population configuration afresh: clear the previous keys, population attributes, relationships and final filters, then show the same selectors used during creation. Multiple-table mode exposes key and attribute selection for each included table; existing-Dimension mode exposes the parent selectors. Keep the Dimension identity/version; reuse a key identity only when a selected source field has proven identical source/column lineage. Changed grains remain subject to downstream dependency validation. The explicit remapping mode is reserved for replacing a table within an unchanged saved source-population strategy. Cancel keeps saved metadata unchanged. Existing embedded member metadata stays attached, preserving lookup references. The normal metadata and build validations reject cycles, invalid mappings or combinations that break downstream contracts. Reopening the Dimension shows its saved population strategy rather than reverting to the source-member facade.

## Save, Preview and Build Semantic

During network builds, single-source populations are virtual queries over their saved input; no copied population table is required. Their transformed output still applies final DISTINCT and validates keys, attributes and member mappings. Multi-table and derived populations are built physically in dependency order and reused while current.

**Save** validates and publishes the object's transformed view together with metadata. **From Multiple Tables / From Existing Dimension** also submits the existing durable population-only build, preparing projection ancestors before the derived physical table; measure values remain a later network-build step. **Preview reads the saved view** and shows at most 50 rows plus the total. Unsaved/new forms ask to Save first; cancellation or a failed Save prevents Preview. Raw Data Preview reads the physical input. **View SQL** shows the saved preview SELECT and supports copying it; the view's full definition is retained as artifact build SQL. See [saved view publication](OBJECT_VIEWS.md).

**Build Semantic** prepares all required populations and measure values. Its durable coordinator orders Dimension dependencies, reuses current populations, builds missing/stale populations, then delegates measure materialisation to the existing engine. Children use durable jobs, fences and guarded activation. The coordinator waits without occupying the worker, resumes after restart, rejects changed definitions and reports failed stages. Build requests are idempotent. Full Refresh refreshes measure values; unchanged Dimension populations can still be reused.

For each Dimension/revision, retain the strict two-physical-table contract: one population table and one wide measure-values table, including rebuilds. Generation tokens isolate candidates inside those tables. Transaction values are resolved, aggregated using each measure's policies, then LEFT JOINED to the population. Transactions do not add missing intersections automatically. No extra staging tables are introduced.

Transactions can create a Dimension from selected grain columns during onboarding, then map themselves automatically. This command saves both definitions (and selected measures) atomically; it no longer builds a population in the editor. Users remain responsible for choosing populations that cover later Transactions.

## APIs and engine boundaries

All routes are revision-scoped under `/api/v1/models/{model}/revisions/{revision}`:

- `/dimensions`: canonical Dimension CRUD. Creation accepts a population definition or a source-member authoring command; both persist a Dimension aggregate.
- `/dimension-members`: embedded member-capability authoring/preview facade. Its responses are projections of Dimension aggregates; there is no member-object repository or collection.
- `POST /dimensions/preview/population`: bounded proposed-population preview, including unbuilt parents.
- `POST /measures/execution/build`: durable Semantic Network command; prepares populations before values.
- `/dimensions/{id}/table` and measure execution status APIs inspect saved materialisations. Per-Dimension build APIs remain engine capabilities, not a required user workflow step.

`DimensionPlanner`, `DimensionCompiler` and `DimensionBuildService` own population analysis, SQL and execution. `SemanticNetworkService`/handler coordinate jobs. Warehouse access remains in repositories/executors; services do not create database clients or issue SQL. Metadata is persisted through shared repositories.

## Migration

`backend/scripts/migrate_dimensions.py` is a one-time command, never a service startup hook. It backs up MongoDB, changes reference names and resource kinds, absorbs standalone member definitions into Dimension aggregates and preserves stable existing grain/column IDs and ClickHouse data. It refuses active jobs and avoids overwriting backups. Historical build fingerprints can become stale; an explicit Build Semantic refreshes them. Do not silently rebuild live values during migration because that could replace manual edits.

The former advanced population draft-generation APIs remain internal compatibility capabilities. The editor uses metadata Save for source-backed Dimensions, a Save-and-build command for derived Dimensions, and query-only Preview. Derived population builds use the existing versioned table storage; Preview allocates no candidate row generations.

## End-to-end evaluation

The executable examples live in `backend/tests/integration/test_unified_dimensions_clickhouse.py` and `application/tests/e2e/unified-dimensions.spec.ts`. They use disposable databases.

- Product is saved as one MongoDB Dimension aggregate with embedded member metadata. No standalone member object or population table is created by Save.
- A Product-only Targets Transaction maps directly to Product. Build Semantic prepares its population and values; Decision View displays P1=120.00 and P2=60.00.
- Forecast and Sales share ProductLocationTime. At Product P1, Forecast=100, Sales=80 and AggregateFirst Error=20. A Product target of 120 initially distributes as 60/60 across its two Base intersections. Editing it to 180 with Weighted by self produces 90/90 and rolls back to 180.
- A Weekly Dimension can preview a distinct projection of an unbuilt Daily Dimension. One Build Semantic command then builds both populations in dependency order and materialises the weekly quantity without double-counting its daily expansion.
- Filters restrict a source Dimension's member population before its grain is built. Stable IDs survive member metadata updates, and dependent Dimensions prevent unsafe deletion.

The same engine still enforces configured aggregation/disaggregation, typed/count restrictions, explicit grain compatibility and one-permanent-table storage. UI display rounding remains two decimals; numeric persistence retains six-decimal policy.

The canonical persisted example is [dimension_aggregate.json](../backend/examples/dimension_aggregate.json). Transaction mapping and preview use the current Dimension query even before its first materialisation; preview never creates a population table. This applies to direct grain mapping, date resolution and multi-grain expansion. Preview checks membership and grain consistency against that same query.

### Transformation controls

Dimension and Transaction forms share the same collapsed-by-default, tabbed Transformations panel. Source-backed Dimensions offer **Filters** then **Pivot Data**; other Dimension populations currently offer **Filters**. Pivot configuration is available directly in its tab, without an additional enable checkbox. Tabs retain drafts while switching and open the relevant panel for invalid fields. The visual tab order does not change execution: Dimension pivot runs before final filters.

### Derived Dimension Save command

`POST /dimensions/save-and-build` accepts the Dimension definition, optional expected version and an idempotency key. It saves through the existing Dimension service, then queues a `dimensionSet` coordinator restricted to that Dimension and its projection ancestors. The worker uses the existing fenced Dimension build jobs and stops before measure materialisation. It works without measures or Base, resumes through the durable job queue, and cancellation also cancels its owned child. Repeated request keys return the same job; an unchanged definition with ready populations is reused without rewriting data.

Metadata and ClickHouse execution have distinct lifecycles: a failed build can leave the definition saved with a missing/stale table. Exact-content retries can finish the build without duplicating definitions; changed concurrent versions remain protected. Existing bindings remain active until guarded activation. The low-level metadata CRUD APIs remain available for metadata-only authoring.

Parent attribute discovery uses `POST /dimensions/schema` with recursive virtual parent inputs, not built-table status. This returns current names, types, stable column IDs and lineage before any parent population exists, including chains of unbuilt projections. Analysis requires managed source schemas but does not create tables or execute output rows. Loading and schema errors are visible in the editor.

**Pivot suggestions:** Pivot Data offers Suggest pivot to fill editable attribute/value pairs, observed output columns and retained/record-key selections from the first 100 managed-input rows. Review and correct the draft, use Discover for additional values, then Save normally. This action does not save or build a Dimension.


Saved objects own independent transformed views. Normal Preview requires saved changes and reads that view; Raw Preview reads the physical source. See [Save, Preview and publication rules](OBJECT_VIEWS.md).
