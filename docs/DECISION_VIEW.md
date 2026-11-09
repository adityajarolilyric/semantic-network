# Decision View: pivot reads and edits

Decision View owns **Build Semantic Network** (incremental), **Full Semantic Refresh** (confirmed source reload, replacing separately materialised edits), the planning pivot and **Preview Materialised Table**. There is no separate Build Semantic navigation step. Build controls appear above the pivot; the table selector and preview appear below it. Only tables with built values are listed, including empty built tables. Selecting a table loads up to 50 saved rows with the total count through the shared Source preview component; it never starts a build. SQL viewing and placement details remain available for the selected table. Build/refresh and editing are mutually disabled while an operation is running. Layout selections survive a rebuild; results/options and an open table preview refresh afterward. Legacy `?tab=materialise` links open Decision View. Backend endpoints and build policies are unchanged.


Decision View sits below Build Semantic in the navigation. Internal component and API names remain `decision-view`. Choose exposed Dimension attributes or explicit Dimension grain/population attributes for Rows/Columns and measures under Values. **Measures render as rows** inside each row-dimension group, never as the column axis. Reorder/remove axis fields; Update pivot sends the layout to the backend. The browser arranges returned cells but performs no aggregation/disaggregation/formula evaluation. Layout selections are browser experience preferences; results remain transient and are always read fresh from the server. Neither is a persisted semantic definition.

Use current saved populations and Build Semantic Network first. Results explain the selected dimension and reuse/recalculation; hide internal IDs and storage details in the pivot; offer explicit SQL inspection separately. Distinguish NULL values, absent intersections and empty members. Scrolling/responsive selectors support large layouts. Empty dimension axes request a global group; automatic subtotals/grand totals, filters and server-saved/shared layouts remain unimplemented.

## Attribute availability

The selector contains the union of exposed output attributes from Transaction-native Dimensions and the selected Base Dimension. Standalone Transactions contribute their declared grain keys. Lookup Dimensions contribute only attributes actually exposed through bindings in those native/Base outputs; unrelated lookup fields and unused projections do not independently enter the selector. For example, Brand appears when PLW or BZM exposes it.

Deduplicate only proven aliases using the relationship analyzer's stable coordinate tokens. Identical names without shared identity stay separate. Options include equivalent Dimension/member/Transaction references as aliases, allowing existing browser selections to restore to the canonical field after deduplication or later Transaction binding.

## Source selection and calculation

`DecisionViewPlanner` resolves requested semantic dimension/column IDs using the shared relationship analyzer. Fields can be existing outputs or reachable through a valid dimension binding. Non-key bindings promise only mapped/selected attributes; matching names alone never prove availability.

Candidates must be current, share a coherent network generation, match captured population/input fingerprints and supply all selected fields/measures or formula inputs. For Base-connected paths, coarser reuse also requires complete, unfiltered distinct-projection ancestry from Base; filtered/independent populations cannot automatically stand in for Base. Prefer exact grain, then smallest row count, key count and stable ID for deterministic ties.

Further rollup from stored coarser values is allowed only when composition is proven safe: involved Time/NonTime methods must agree and be SUM, MIN or MAX. MIN/MAX with Ignore + All-null Zero fall back because an introduced zero changes later results. AVG, COUNT, COUNT DISTINCT and mixed methods fall back to Base when additional aggregation is needed; exact-grain stored values remain reusable. This eligibility test never chooses the measure's aggregation function.

An independent measure may use a conservative native-materialisation fallback if Base lacks it, including direct native→coarser paths. When no single stored table can supply all formula inputs, independently aggregate each available native/Base input to the requested axes before joining; never join raw fact rows. This also supports counts whose arguments are not physically stored at the counting grain: resolve from compatible saved values, without rereading source facts. Unsupported measure combinations fail with a reason rather than inventing joins/partial measures. Additional reuse optimisations need aggregate state or stronger population/dependency proofs.

`DecisionViewCompiler` creates the requested virtual grain and reuses measure compilation:

- CalculateFirst aggregates its stored calculated values by its own policies.
- AggregateFirst reuses only equivalent exact-grain results; otherwise resolve inputs at the requested grain and recalculate recursively.
- Apply configured Time/NonTime order; First/Last uses explicit Time ordering, not label sort.
- Global groups follow the same policy-aware path, not an assumed SUM.

Example: AggregateFirst Error at Brand = ABS(Brand FQTY − Brand SQTY). CalculateFirst instead rolls up the native cell errors. Unequal-group averages require the correctness fallback rather than averaging stored averages again.

## Read API, consistency and limits

A Semantic Network build blocks new pivot queries, edits and materialised-values previews at the backend, drains existing reads, and unlocks only after success. Status/metadata remain readable. A failed in-place write keeps the network unavailable until a successful recovery build; it must not serve a partially updated network.

Revision-scoped endpoints:

- `GET /decision-view/options`: exposed dimension fields and measures.
- `POST /decision-view/query`: rows/columns of `{dimensionId,columnId}` plus ordered measure IDs. Returns grouped cells/labels, source-level explanation, reuse/recalculation flags and size. Also returns per-measure editable/editReason/writeLevelId/writeLevelName/writesToBase, and editContext with revisionVersion/generationId.

Reject duplicate axis fields/measures and oversized layouts. Limits: eight attributes, twelve measures, `METADATA_DECISION_VIEW_MAX_GROUPS=5000` and `METADATA_DECISION_VIEW_MAX_CELLS=50000`. Fail clearly instead of computing totals from truncated data. SQL/node/memory/time limits apply; `METADATA_CLICKHOUSE_MAX_THREADS=4` bounds query parallelism. Report memory/timeout failures accurately rather than as connection outages.

Capture coherent inputs and acquire `engine_read_leases` in the same MongoDB transaction that checks/touches artifacts. Leases last 180 seconds, renew every 15 seconds, release on completion/failure and expire through TTL after crashes. Include dimension artifacts. Collector/legacy reconciliation respect them; reset drains requests through the maintenance gate. Read explicit columns through stable views while the read barrier excludes table exchanges. Final verification rejects changed revisions or expired leases. Metadata hashes do not freeze/detect arbitrary external source-row changes.

## Edit eligibility and write target

Only `editable=true` **referenced** measures can be edited, at native or proven coarser **displayed grain**, including ad hoc/global groups. Determine permission from semantic coordinates, not the physical table serving the pivot. Finer cells and calculated measures are read-only; absent intersections cannot be edited. Published revisions are read-only; definitions/populations/values must be current.

Write to Base when it has a proven finer/equivalent path to both native and displayed grain. Otherwise write to **native**, then update compatible coarser representations. Base connectivity is not required for defining/editing independent measures. For example, PLT quantity can be edited at Product even if Base is PLC and cannot provide Time. Unrelated Base/finer representations remain unchanged.

The entered number is an absolute semantic cell value, not universally an additive total/delta. Resolve the complete eligible write population from typed member coordinates; visible pagination/UI scope must not reduce it.

## Edit distribution and recomputation

Use the measure's configured policy at the selected write level:

- None/null repeats to every eligible row.
- Equal divides across eligible rows.
- Weighted uses captured basis values; missing weights/zero denominator fails.
- Weighted by self uses captured existing values **only in populated non-NULL cells**, preserving NULL cells. Zero-valued cells stay zero when populated total is nonzero. All-missing or zero-total patterns fail without fallback.

Sparse self-weighting is intentional: a promotion may populate only a few dates in a full PLT horizon. Unpopulated dates must not prevent an edit to its existing distribution. Interactive edits retain strict empty/zero-total checks. Source materialisation ignores edit weights: SUM axes split equally and other axes repeat, in reverse aggregation order. Always freeze weights before writing, never use partially updated rows.

Preserve unaffected cells/measures. Reaggregate **complete** groups, including unchanged members, into every compatible coarser level using saved Time/NonTime/order. Recompute dependent AggregateFirst at each target grain and CalculateFirst at native before propagation. Unsupported dependent recalculation fails activation. Changing a basis measure does not immediately reallocate unrelated referenced measures; it affects subsequent allocations. Repeat+SUM can increase an aggregate; do not impose conservation or substitute AVG.

### Cell-scoped dependency refresh

Edits now carry transient SQL key sets through the dependency graph rather than recalculating each dependent column over its entire population:

1. Resolve the complete eligible write population from the selected row/column member coordinates and the captured, filtered Dimension population. Transaction filters continue to govern source values; an edit does not replay source loading. Pivot pagination is not an edit boundary; there is currently no separate Decision View filter contract.
2. Apply the edited measure's configured allocation with frozen weights, round to stored precision, and compare with captured values/coverage. Only changed write-grain keys enter the patch. NULL comparisons are explicit, so clearing a value is a real change. A same-value edit has no dependent row writes.
3. Freeze those changed keys/new values once in an owned, fenced ClickHouse staging table. Python receives no cells. This prevents the patch/weight query from expanding repeatedly inside dependent SQL. The table is transient, removed after success/failure, and recoverable after a crash; it is not an edit history or permanent measure store.
4. Project each measure's affected keys through proven Dimension lineage. AggregateFirst unions affected input cells at the requested grain. CalculateFirst first maps changed inputs to its native grain, recalculates there, then derives affected finer/coarser representations. A coarser native calculation can therefore affect siblings outside the initially edited cell.
5. Limit formula evaluation to affected cells. For rollups/counts, read **all** contributors to each affected group, including unchanged members. For allocations, use **all** eligible children of each changed parent. This preserves AVG/MIN/MAX, ordering, NULL/Mixed states, COUNT_DISTINCT, and configured weight denominators; summing only edited deltas would be incorrect.
6. Clone the existing table, preserve unrelated columns/rows, and mutate only the selected measure/state columns where the staged key lookup contains the row. Measures with different footprints are written separately. Completed private grain candidates checkpoint subsequent grain computations. Validate uniqueness, key coverage and persisted values before normal atomic publication.

Operational `buildTimingsMs.groups` entries identify `row_update`, measure IDs and `updatedRows`, making the actual write footprint inspectable. Fences, stale-generation checks and all-or-nothing multi-table activation remain unchanged. Normal builds retain their existing full/incremental behavior.

Related totals must refresh even when they lie outside the visible pivot slice. This is dependency propagation, not a request to refresh unrelated dimensions or matching column names. Future time-window/recurrence functions will need explicit wider invalidation ranges before they can use this optimisation. ClickHouse can still scan contributing rows and rewrite storage parts containing changed cells; cell-scoped logical updates do not promise cell-sized physical I/O.

Build edited candidates in isolated temporary tables, retaining unaffected content. One Mongo transaction activates all new bindings, succeeds the run, advances revision version and removes transient edit payload. Read admission blocks overlapping builds/edits; activation exchanges validated tables and commits the coherent network. Check displayed generation/revision, input/population hashes, leases/fences; a stale candidate cannot overwrite a newer commit even if candidates computed concurrently. Failure restores exchanged tables when metadata activation has not committed.

## Edit API and UI

`POST /decision-view/edits` requires Idempotency-Key and accepts:

```json
{
  "view": {"rows": [{"dimensionId": "Product", "columnId": "product_id"}], "columns": [], "measures": ["PromoQty"]},
  "measureId": "PromoQty",
  "memberValues": ["P1"],
  "value": 1500,
  "generationId": "run_current",
  "revisionVersion": 57
}
```

Member values follow exact rows-then-columns order and retain raw typed representations; JSON null is not the display string `(empty)`. Accept finite Float64, or null only when allowed. The backend independently revalidates eligibility and returns an asynchronous run; generic engine polling reports progress/errors.

Eligible cells show a pencil. Save/Enter submits; Cancel/Escape discards only the unsent input. Disable controls while pending, refresh after activation and retain layout while advancing revision context. If committed data cannot refresh, report **committed, refresh failed** rather than implying the write failed. Missing-pattern and zero-total self-weight errors remain distinct.

## No journal and rebuild behavior

There is **no durable edit journal, replay or separate override store**. Pending/running jobs temporarily hold `__edit` input for recovery; remove it on success/failure under the same fence, so an expired worker cannot delete replacement context. Ordinary run hashes/state/artifact lineage remain operational records, without retaining a business edit journal.

Active edited values survive restarts. **Build Semantic Network is incremental:** unchanged values and manual edits are reused, and a no-change build retains its generation. **Full Semantic Refresh** recomputes all measures from sources and replaces manual edits after confirmation. Source, population or semantic changes invalidate affected representations and can replace edits for those measures. Source/network builds initialise finer visibility from configured aggregation (SUM splits, other supported methods repeat); they do not consult previous self values or edit weights. Self-weighting applies to interactive edits, using the captured populated write representation. Sources and semantic definitions are never mutated by an edit.

## Implementation and tests

`api/decision_view.py` owns HTTP; `schemas/decision_view.py` bounds typed layouts; `services/decision_view.py` captures/leases/executes reads; `engine/decision/compiler.py` plans/compiles; `engine/decision/edits.py` and `services/measure_builds.py` coordinate writeback. UI lives in DecisionViewPanel/PlanningEditCell.

Coverage includes exact/coarser reuse, AVG/NULL fallback, formulas at ad hoc grains, axis order, global groups, stale reads/leases, group/cell bounds, actual displayed-grain permissions, independent native fallback, sparse self-weighting, zero/missing patterns, idempotency, dependent recomputation, atomic rollback and the one-permanent-table limit.

## Stored precision and incomplete representations

Numeric measure cells display two decimal places. Inputs retain the six-place stored value for editing; only the displayed text is rounded to two places. Edit writes are rounded to six places before coarser propagation, using the same values stored at the write grain.

Legacy/edit-derived incomplete distributions carry coverage flags in the same values table and generation. Decision View suppresses partial totals/formulas and can recover compatible native values. Warnings identify affected measures. Ordinary source NULL policies are separate. Initial source materialisation now derives distribution from aggregation and does not require edit weights. Native artifacts used for recovery are pinned during reads.

## Browser layout memory

Row/column order and selected measure IDs persist in localStorage, scoped by model/revision, across navigation and refresh. Labels are resolved from current metadata; unavailable fields/measures are pruned. Reopening automatically queries fresh values. No results, cell edits, edit-generation tokens or semantic definitions are cached. If browser storage is blocked, an in-memory fallback preserves selections during navigation.

`GET /decision-view/options` also returns `buildId`: the latest successful non-edit Semantic Network run, or null before any build. A changed build refreshes options and results while preserving valid ordered selections. References to removed fields/measures are dropped; failed/in-progress builds, metadata reloads and cell edits do not clear the layout. Check the token on re-entry, browser focus and before Update pivot. The engine-runs index supports the scoped latest-build lookup. **Clear selections** removes this layout and current results without changing metadata or data. Clearing browser storage also removes the preferences.

Selected measures can be reordered using the same move-up controls as row/column attributes. This order is submitted to the query and retained with browser layout preferences.

## Axes without Dimensions

`GET /decision-view/options` also returns dimensions with named public grain/population-attribute columns. A field request identifies exactly one owner: dimensionId or dimensionId, plus columnId. Old Dimension requests/serialization remain compatible; new references are query/layout data, not extra semantic metadata. Unknown owners, nonpublic columns, duplicate fields and conflicting owner types are rejected.

The planner obtains the level field's existing semantic coordinate/lineage and uses the same source selection, calculation and edit-eligibility logic. Member casts for edits use captured population types. Browser preferences preserve both owner forms and resolve current labels after reopening. No Dimension objects or time semantics are inferred; Time-specific policies still need explicit Time lineage. Both fields and measures can therefore be plotted from a Transaction-generated level with zero Dimensions. Selecting another unbound level with similar column names does not create a cross-level relationship.

Row-dimension columns (including the global Rows column) and Measure are pinned during horizontal pivot scrolling. Left offsets use actual header widths, update on resize/content changes, and apply to row-spanning body headers as well as the header row. Data/value columns continue to scroll; headers remain pinned at the top.

### Quick calculated measures

Draft revisions with a Base dimension offer **Create Measure** in Decision View. The compact modal asks for a single-line named Formula (`Name = expression`), using the shared formula builder, input insertion, caret handling and backend semantic validation from the Measures editor. Output Dimension defaults to Base and calculation type to AggregateFirst: inputs align and aggregate before evaluation at the requested grain. Advanced settings sit below the searchable input library and reveal the output level, calculation type and applicable policies; collapsing them preserves changes. Measures remain noneditable with nullable Float64 results by default.

Saving creates an ordinary measure definition and runs incremental materialisation, preserving unaffected values and the current pivot layout. The measure then appears under Values for selection. If saving succeeds but the build fails, the definition is retained and the UI directs the user to retry Build Semantic Network from Materialise. Published revisions cannot create measures. No additional metadata or API is introduced.


## Context Graph: saved model lineage

Context Graph is an interactive node-and-edge view of the saved semantic model. React Flow provides pan, zoom, dragging, selection and a minimap; Dagre lays out arbitrary parent/child depth from left to right. The graph and its libraries load only when this navigation item opens. Positions, search and focus are UI state; moving a node never changes metadata or creates a relationship.

- Source tables feed Dimensions and Transactions through their saved managed views. There is one source node, without duplicate internal managed-view/member-catalog nodes.
- Population edges show a source table, every Cartesian factor, or a distinct projection from a parent Dimension. Explicit Dimension bindings also link lookup Dimensions to the enriched/intersection Dimension. Time-resolution calendars link to their Transactions.
- Dimension-to-Transaction edges show the Transaction's configured native grain mapping. Standalone Transactions have an explicitly labelled Transaction grain, with no invented Dimension or name-based relationship.
- Solid **Output grain** edges connect each measure to its resolved native/output Dimension (or standalone Transaction grain). **Dotted Value source** edges additionally connect a referenced measure to its Transaction. The dotted link explains the origin of the value; it does not give the measure a second output grain.
- Optional dashed **Formula input** edges show calculated-measure dependencies. These express dependency, not a guarantee that two measures can be plotted together.

Select a node or edge for readable names, grain keys, source/value column, formula and relationship explanations. Find an object centres it; Focus on this lineage isolates its ancestors and descendants without including unrelated sibling branches. Source-table and formula-dependency toggles reduce clutter. Reset layout restores automatic placement. Graph construction uses the existing complete metadata snapshot: no extra endpoints, ClickHouse queries, builds or semantic writes are needed. Deleted/missing endpoints are not drawn as dangling edges; the inspector reports unavailable context instead of displaying internal IDs.

### Plotting compatibility

The original whole-selection compatibility checker remains under **Check plotting compatibility**, collapsed until requested. It is also available through Explore measure compatibility in the formula editor and Check compatibility in Decision View. Dimension hubs show usable contexts for measures. Formula checks use the selected Output Dimension; pivot checks use the current row/column fields. Select several measures to evaluate the entire selection. Pairwise compatibility does not imply that three or more measures share a usable source.

- **Ready:** the complete selection has a current, coherent materialised source accepted by the pivot planner.
- **Needs materialisation:** definitions permit the context, but current build inputs/values are missing or stale.
- **Unavailable:** semantic paths, formula inputs, typed/count restrictions, pivot limits or a common source prevent the selection. The graph explains the reason using names.

The graph shares `MeasureCompatibilityAnalyzer` with validation and pivot planning, then checks the actual `DecisionViewPlanner` against the same build snapshot used by reads. It does not infer joins from matching names, create relationships, or build tables. Availability is contextual, not a permanent measure-to-measure edge. A later edit/build can change readiness; execution recaptures inputs and acquires its own leases.

`POST /models/{model}/revisions/{revision}/compatibility` accepts selected measures plus either a dimension or pivot fields. Metadata reads only: no ClickHouse queries. Responses are paged to 24 measures and eight contexts, with a twelve-measure selection limit. Revision/binding fingerprints invalidate bounded snapshot caches; analyzers and their recursive availability caches are bounded too.

## Build Semantic screen

The navigation label is **Build Semantic**; the build action remains **Build Semantic Network**. Preview starts collapsed and requests no rows until Show preview is selected. Building does not open it. Preview remains an independent action and displays the selected saved materialised table; build errors remain visible while preview is closed.

### Query inspection

Materialised previews offer separate View Output SQL and View Build SQL buttons. The former reads the current view (50 rows); the latter shows the recorded build run, including writes and publication. The pivot offers View Output SQL for the actual displayed result. Changing selections clears the result/SQL; inspecting SQL never reruns the pivot.

### Source-backed independent outputs

For an unbound, untransformed Transaction, the existing source is writable measure storage and the output is an owned aggregation view. Native/coarser edit permissions still apply; physical duplicate records are updated under the measure's allocation/aggregation policies. Referenced aliases over the same source column and view-calculated dependents see the changed source values. COUNT/COUNT_DISTINCT and grain-key columns cannot be edited through this path. Read-only/no-op builds create no data copy; edits publish through an owned temporary source clone with rollback on failure. Successful edits replace source values permanently, so Full Refresh retains them. Source-backed previews count current view rows. [Details and upgrade boundary](ENGINE_AND_STORAGE.md#independent-transactions-source-backed-storage).
