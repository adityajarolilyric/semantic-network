# Planning View: pivot reads and edits

Planning View sits below Materialise. Choose exposed dimension attributes for Rows/Columns and measures under Values. **Measures render as rows** inside each row-dimension group, never as the column axis. Reorder/remove axis fields; Update pivot sends the layout to the backend. The browser arranges returned cells but performs no aggregation/disaggregation/formula evaluation. Layout selections are browser experience preferences; results remain transient and are always read fresh from the server. Neither is a persisted semantic definition.

Use current saved populations and Build Semantic Network first. Results explain the selected planning level and reuse/recalculation; hide internal IDs, SQL, physical names and generation tokens. Distinguish NULL values, absent intersections and empty members. Scrolling/responsive selectors support large layouts. Empty dimension axes request a global group; automatic subtotals/grand totals, filters and server-saved/shared layouts remain unimplemented.

## Source selection and calculation

`PlanningViewPlanner` resolves requested semantic dimension/column IDs using the shared relationship analyzer. Fields can be existing outputs or reachable through a valid dimension binding. Non-key bindings promise only mapped/selected attributes; matching names alone never prove availability.

Candidates must be current, share a coherent network generation, match captured population/input fingerprints and supply all selected fields/measures or formula inputs. For Base-connected paths, coarser reuse also requires complete, unfiltered distinct-projection ancestry from Base; filtered/independent populations cannot automatically stand in for Base. Prefer exact grain, then smallest row count, key count and stable ID for deterministic ties.

Further rollup from stored coarser values is allowed only when composition is proven safe: involved Time/NonTime methods must agree and be SUM, MIN or MAX. MIN/MAX with Ignore + All-null Zero fall back because an introduced zero changes later results. AVG, COUNT, COUNT DISTINCT and mixed methods fall back to Base when additional aggregation is needed; exact-grain stored values remain reusable. This eligibility test never chooses the measure's aggregation function.

An independent measure may use a conservative native-materialisation fallback if Base lacks it, including direct native→coarser paths. Unsupported measure combinations fail with a reason rather than inventing joins/partial measures. Additional reuse optimisations need aggregate state or stronger population/dependency proofs.

`PlanningViewCompiler` creates the requested virtual grain and reuses measure compilation:

- CalculateFirst aggregates its stored calculated values by its own policies.
- AggregateFirst reuses only equivalent exact-grain results; otherwise resolve inputs at the requested grain and recalculate recursively.
- Apply configured Time/NonTime order; First/Last uses explicit Time ordering, not label sort.
- Global groups follow the same policy-aware path, not an assumed SUM.

Example: AggregateFirst Error at Brand = ABS(Brand FQTY − Brand SQTY). CalculateFirst instead rolls up the native cell errors. Unequal-group averages require the correctness fallback rather than averaging stored averages again.

## Read API, consistency and limits

Revision-scoped endpoints:

- `GET /planning-view/options`: exposed dimension fields and measures.
- `POST /planning-view/query`: rows/columns of `{dimensionId,columnId}` plus ordered measure IDs. Returns grouped cells/labels, source-level explanation, reuse/recalculation flags and size. Also returns per-measure editable/editReason/writeLevelId/writeLevelName/writesToBase, and editContext with revisionVersion/generationId.

Reject duplicate axis fields/measures and oversized layouts. Limits: eight attributes, twelve measures, `METADATA_PLANNING_VIEW_MAX_GROUPS=5000` and `METADATA_PLANNING_VIEW_MAX_CELLS=50000`. Fail clearly instead of computing totals from truncated data. SQL/node/memory/time limits apply; `METADATA_CLICKHOUSE_MAX_THREADS=4` bounds query parallelism. Report memory/timeout failures accurately rather than as connection outages.

Capture coherent inputs and acquire `engine_read_leases` in the same MongoDB transaction that checks/touches artifacts. Leases last 180 seconds, renew every 15 seconds, release on completion/failure and expire through TTL after crashes. Include dimension artifacts. Collector/legacy reconciliation respect them; reset drains requests through the maintenance gate. Read explicit columns at captured row-generation tokens. Final verification rejects changed revisions or expired leases. Metadata hashes do not freeze/detect arbitrary external source-row changes.

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

Insert candidates into existing values tables, retaining full unaffected content. One Mongo transaction activates all new bindings, succeeds the run, advances revision version and removes transient edit payload. Readers see the previous coherent generation until then. Check displayed generation/revision, input/population hashes, leases/fences; a stale candidate cannot overwrite a newer commit even if candidates computed concurrently. Failure leaves prior bindings intact.

## Edit API and UI

`POST /planning-view/edits` requires Idempotency-Key and accepts:

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

Active edited values survive restarts. **Build Semantic Network recomputes from sources and replaces manual edits**; required rebuilds after metadata/population changes have the same consequence. Normal self-weight builds may use current proportions as frozen weights; no journal preserves a pristine pre-edit pattern. Sources and semantic definitions are never mutated by an edit.

## Implementation and tests

`api/planning_view.py` owns HTTP; `schemas/planning_view.py` bounds typed layouts; `services/planning_view.py` captures/leases/executes reads; `engine/planning_view.py` plans/compiles; `engine/measure_edits.py` and `services/measure_builds.py` coordinate writeback. UI lives in PlanningViewPanel/PlanningEditCell.

Coverage includes exact/coarser reuse, AVG/NULL fallback, formulas at ad hoc grains, axis order, global groups, stale reads/leases, group/cell bounds, actual displayed-grain permissions, independent native fallback, sparse self-weighting, zero/missing patterns, idempotency, dependent recomputation, atomic rollback and the two-table limit.

## Stored precision and incomplete representations

Numeric measure cells display two decimal places. Inputs retain the six-place stored value for editing; only the displayed text is rounded to two places. Edit writes are rounded to six places before coarser propagation, using the same values stored at the write grain.

Legacy/edit-derived incomplete distributions carry coverage flags in the same values table and generation. Planning View suppresses partial totals/formulas and can recover compatible native values. Warnings identify affected measures. Ordinary source NULL policies are separate. Initial source materialisation now derives distribution from aggregation and does not require edit weights. Native artifacts used for recovery are pinned during reads.

## Browser layout memory

Row/column order and selected measure IDs persist in localStorage, scoped by model/revision, across navigation and refresh. Labels are resolved from current metadata; unavailable fields/measures are pruned. Reopening automatically queries fresh values. No results, cell edits, edit-generation tokens or semantic definitions are cached. If browser storage is blocked, an in-memory fallback preserves selections during navigation.

`GET /planning-view/options` also returns `buildId`: the latest successful non-edit Semantic Network run, or null before any build. A changed build clears saved selections; failed/in-progress builds, metadata reloads and cell edits do not. Check the token on re-entry, browser focus and before Update pivot. The engine-runs index supports the scoped latest-build lookup. **Clear selections** removes this layout and current results without changing metadata or data. Clearing browser storage also removes the preferences.

Selected measures can be reordered using the same move-up controls as row/column attributes. This order is submitted to the query and retained with browser layout preferences.
