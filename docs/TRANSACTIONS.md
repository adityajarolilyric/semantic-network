# Transactions: direct, multi-grain and date-range resolution

A Transaction references a saved Managed Table and one native dimension. Measures may be created with the Transaction or later. Transaction expansion has its own per-value Split/Copy policy. It never guesses a measure aggregation/disaggregation policy. Sources stay unchanged; resolved relations are CTEs shared by Preview and Materialise, with no extra physical table, imported Source or persistent resolved view.

## Transaction editor layout

**Transformations** starts collapsed and contains tabs in this order: **Filters**, **Pivot Data**, **Date Resolution**. Pivot Data and Date Resolution show configuration directly, without enable checkboxes. Opening Date Resolution alone does not enable it; changing a field starts the optional configuration, and Clear date resolution removes it. An untouched empty configuration applies no Pivot; editing starts the configuration, and Clear pivot (or removing the last pair) removes it. Configured stages are marked; switching tabs preserves drafts and keeps schema discovery active. Tab order is presentation only: engine processing remains Pivot → Date resolution → grain mapping/resolution → Filters. **Grain Selection** is mandatory and always visible outside the panel; it offers No Dimension or Map to Dimension. The latter contains the native-level selector and a Resolve multi-grain data checkbox: unchecked shows direct key mappings; checked shows the multi-grain configuration. Existing saved multi-grain Transactions reopen with the checkbox checked. Stored mapping modes and engine behavior are unchanged. The optional Measures grid follows it. No backend or stored-metadata contract changes are needed for this layout.

## Start from a table without an existing dimension

New Transaction authoring defaults to **Create Dimension**. Configure optional Pivot and Date resolution, choose grain columns from that effective output, and review the suggested dimension name. The shortcut creates ordinary metadata: a source-populated Dimension with selected keys, no Dimension bindings and no population attributes, plus a directly mapped Transaction. Existing final DISTINCT compilation supplies the observed intersections; fact rows remain intact for configured measure aggregation. No Dimensions, new persistent mapping mode, auto-generation flags or population contributor metadata are introduced.

Pivot and Date resolution are available before a native level exists. They execute **Pivot → Date resolution → DISTINCT selected grain keys** for the generated population. The Transaction keeps the same input stages and maps their output IDs directly to those grain keys. Date resolution uses an explicitly configured daily Time Dimension; the calendar catalog does not require a dimension. No Time Dimension is inferred from column names.

To make population rebuilds self-contained and avoid a population → Transaction → population cycle, source populations may store `inputTransform` with `objectId` (a stable output-ID namespace, not a reference), `pivot` and `calendarResolution`. On creation this is an exact snapshot of the Transaction input configuration. The population and Transaction subsequently remain independent; editing Transaction transformations does not silently redefine the population. Selecting a different population source explicitly replaces its input pipeline while retaining logical grain IDs through remapping. Dimension editors/schema APIs read the effective transformed columns, including generated calendar fields. The shared operators run their existing duplicate-cell/calendar checks and expansion bounds; previews of these populations also run input checks. No additional physical table or shared Managed Table mutation is introduced.

Filters also work without an existing dimension: they filter Transaction rows after input transformations while the generated population keeps all observed intersections. Multi-grain assignment requires an existing target level. Adding Sales to an existing level never expands its population automatically; unmatched intersections still fail, and the user changes the population explicitly.

- `POST /transactions/from-grain`: creation command with object identity/name/description, sourceId, grainColumnIds and optional dimensionName, pivot, calendarResolution, filters and measures. In one MongoDB transaction, validate/create the dimension, Transaction and any selected Measures and select Base only if this is the first dimension and Base is unset. Existing Base is preserved. Generated IDs make exact retries idempotent; changed requests or edited objects conflict rather than overwrite.
- `POST /transactions/from-grain/preview`: same command; without filters, preview the distinct proposed population. With filters, preview the transformed, filtered Transaction rows (preserving detail records), with count and at most 50 rows. Neither path creates definitions, changes versions or selects Base.
- Save commits the Transaction, selected Measures and its independent transformed view atomically, without copying source data or automatically creating a Dimension. Build Semantic later prepares populations and values in dependency order.

Nullable/invalid keys and resource bounds keep their existing build checks. A declared time-like column does not create a Time Dimension or infer temporal ordering. Decision View supports explicit dimension grain/population-attribute axes so an unbound generated level is usable without Dimensions. See [Decision View](DECISION_VIEW.md).

## Changing the connected Dimension

A Transaction can move to another Dimension when its complete key mappings and other semantic contracts remain valid. On Save, the backend updates `nativeDimensionId` on every referenced Measure owned by that Transaction and re-derives its value-column type using the ordinary Measure preparation rules. IDs, names, formulas elsewhere, aggregation, disaggregation, missing-value and edit policies are preserved. Unrelated Measures remain unchanged.

For a direct remap that preserves the source, Pivot/Calendar stages and the same mapped source-key columns, numeric scalar calculations defined at the old Dimension also follow when **all** of their measure dependencies follow. This includes chains of such calculations. For example, Sales quantity and value move from an unbound Sales grain to BrandZoneWeek; their `price` formula moves with them without changing its expression. A dependency queue handles this in graph order, without inferring relationships from matching names.

Calculations at a different output Dimension, mixed dependencies, nonnumeric results, counts, attributes and grain references are not relocated automatically. A changed set of source keys or a multi-grain remap likewise does not infer a new calculation grain. These retain normal compatibility checks. **Reason:** a pure calculation can retain its meaning at the same explicitly mapped business grain, while contextual expressions or another grain may mean something different. Auto-created grains have no implied relationship to later business Dimensions.

The Transaction, all relocated Measures and revision version commit in one MongoDB transaction with version checks. Measure versions advance so stale editors cannot overwrite the change and incremental materialisation detects the affected dependencies. Any invalid mapping, dependent formula or write conflict rolls the whole change back. Save validates the proposed Measure references and publishes the transformed Transaction view with the metadata. Normal Preview and View SQL read that saved view; the next Build Semantic refreshes the affected values using the existing incremental dependency analysis.

## Create measures with a Transaction

During creation, the optional **Measures** grid lists supported Number, String, Date and Boolean columns from the effective Pivot/Date output. For multi-grain mapping, configured numeric resolution values and explicitly retained typed columns are eligible. Select columns, Editable, Non-time aggregation, Time aggregation and Dis-Aggregation policy. Aggregations require explicit choices; Dis-Aggregation defaults to Weighted by self. Weighted by measure additionally requires an existing basis measure. Unavailable columns must be deselected after transformation changes.

Nonnumeric selections are read-only, have no Dis-Aggregation and offer only type-compatible aggregation policies. Names come from output column names; Transaction, value-column ID and output dimension are derived. Numeric result defaults are Float64; nonnumeric types are derived. Other defaults are missing transaction value NULL, source null preservation, Non-time then Time aggregation order, ignored NULL inputs and NULL all-null results. These are ordinary referenced Measures and remain editable through Measures afterward. No measure values are built until Materialise.

Both creation commands accept an optional `measures` array (up to 128 unique columns), each with `valueColumnId`, `editable`, `aggregation` and `allocation`. This is command input, never stored on the Transaction. A shared app-scoped metadata factory reuses normal Measure normalization; graph validation and all inserts run within the same MongoDB transaction. Invalid columns, policies, references or object limits roll back the entire creation. Generated measure IDs are stable per Transaction/value-column pair. API request shapes remain unchanged; previews do not persist Measures. Multi-grain Split/Copy remains independent of measure aggregation and edit Dis-Aggregation.

## Direct mapping

Map every native grain key exactly once to the effective source schema. Multiple detail rows per native key are allowed: each referenced measure independently aggregates by **all** mapped keys using its configured policy, then LEFT JOINs the native planning population. Reject unmatched source keys explicitly; a LEFT JOIN alone would silently drop them. Preserve population rows without transactions and apply the measure's missing-value policy.

`mappingMode="direct"` is backward compatible. `expectedRowsPerGrain` defaults to `many` and is hidden; legacy `zero_or_one` declarations remain readable/preserved on edits. Where an explicit source key exists, a zero-or-one declaration must match it. Declarations do not independently verify live data.

## Multi-grain assignment semantics

Use `mappingMode="resolve_multi_grain"` when rows assign values at native or coarser grains. Match the **actual native intersections**, not a Cartesian product of dimension members.

Internal draft/stage diagnostic endpoints can compile the current native Dimension population virtually, without requiring a built table. The normal editor Preview instead requires Save and reads the saved Transaction view. Dimension SQL exposes public column names; the preview boundary aliases these names back to stable column IDs before matching. Persisted populations use the same logical IDs, so changing a column's display/output name does not change its mapping. Native population and transaction validation still run; missing members are not silently discarded.

- Fixed-column rules map supplied source fields to native/dimension attributes. All present restrictions must hold.
- New rules use **Column equals attribute** only. Pivot long attribute/value pairs into explicit columns first. Saved legacy Level/Value rules remain executable and unchanged rules can be saved; new or modified selector rules are rejected by CRUD.
- Every native axis needs a constraint unless `allowAll` explicitly permits absence. Missing is not an implicit broadcast. Time must be covered too.
- A row is one independent assignment. EAV rows that jointly describe one fact must be pivoted/grouped using fact identity first.

**Explicit source application order wins at any grain.** Later coarse rows can overwrite detail; specificity is not priority. ClickHouse row order, SELECT/UNION order and generated row numbers cannot reconstruct application order. Choose a meaningful order column (sequence, timestamp or explicitly ordered text). Overlapping rows tied on the complete configured order fail, rather than choosing an arbitrary winner.

A winner carries its entire payload, including NULL. No fallback to an earlier non-NULL value occurs. For each selected value column, Copy retains the value; Split divides by that assignment's original full matching-native-key count. Counts are independent of duplicate assignments and later overrides. Overwritten amounts are not redistributed; no residual/conservation rule is inferred. Numeric IDs and other numeric columns are not automatically treated as allocatable values.

Example: Brand A/Zone North matches P1/L1 and P2/L1. Quantity 100 at order 10 with Split gives 50 each; P1/L1=30 at order 20 yields **30,50**, not 30,70. Copy on the first assignment yields 30,100. If the coarse assignment comes later, Split yields 50,50.

## Resolution metadata

An abbreviated definition (add an axis for every native key):

```json
{
  "mappingMode": "resolve_multi_grain",
  "keyMappings": [],
  "resolution": {
    "axes": [{
      "grainColumnId": "product",
      "allowAll": false,
      "rules": [{
        "ruleId": "product_brand",
        "kind": "column",
        "sourceColumnId": "pv_brand",
        "target": {"dimensionId": "Product", "columnId": "brand"},
        "blankIsAbsent": true
      }]
    }],
    "values": [{"sourceColumnId": "col_qty", "mode": "split"}],
    "orderColumnIds": ["col_sequence"],
    "partitionColumnIds": [],
    "passthroughColumnIds": [],
    "overlapPolicy": "last_applied_wins"
  }
}
```

Fixed rules use `kind="column"`, sourceColumnId and target. Omitting target.dimensionId selects a native output by ID. A dynamic pair is absent only when both selector/member are absent under its rule. `allowAll` permits absent restrictions but never ignores present ones. Preserve typed codes such as `001`; no global trimming/quote removal/String coercion.

The first order field is integer or Date/DateTime/DateTime64; legacy additional tie-breakers may be String. Higher lexicographic tuples apply later; NULL order fields fail. Legacy API partitions produce independent assignment series, later aggregated by the measure. The current UI exposes only one order field and no partition/tie-breaker controls: opening older definitions normalizes the unsaved draft to the first order field and empty partitions; Save commits, Cancel preserves the original.

Resolved transactions intentionally have empty manual keyMappings; the engine generates stable canonical native-key IDs. List summaries show axis/value counts, not “0 mapped keys”. Selected source value IDs survive resolution and become Float64; measure selectors expose those configured numeric outputs. Supported passthrough fields are carried separately.

## Compiler pipeline and resource bounds

1. Capture current managed/native schemas and artifacts. Validate typed targets/selectors, required axes and source order. Supported input grains are native or coarser; unsupported finer matching fails explicitly.
2. Reduce identical complete restriction tuples (plus legacy partition) to their latest NULL-preserving payload. Split counts depend on matching population, so earlier identical assignments can be eliminated safely.
3. Discover observed selector profiles and compile selective equality-join branches. Enrich only required native attributes through proven mappings. Do not enumerate all attribute combinations or unconditionally cross every assignment with every native row.
4. Deduplicate restriction→complete native-key matches using actual typed equality; hashes do not replace tuple comparisons. Count full match sets. Unmatched assignments fail with bounded diagnostics.
5. Choose the highest-order matching assignment per native key/partition, reject overlap ties and preserve NULL using a tuple payload (rather than a scalar argMax that could skip it). Apply Split/Copy using the original counts.
6. Expose the effective transaction schema to the normal measure pipeline. Same-grain multi-grain-mode rows follow replacement semantics; ordinary Direct detail rows still contribute through configured aggregation.

Profiles changing between discovery and execution fail instead of losing assignments. A CTE is a logical boundary, not a promise that ClickHouse caches/materialises it. Selective joins, redundant-pattern reduction and bounded execution provide scalability; persistent caches would need explicit dependency retention.

Defaults: `METADATA_RESOLUTION_MAX_PROFILES=64` (max 256), `METADATA_RESOLUTION_MAX_ROWS=1000000` expanded matches (max 10,000,000). Typed contracts also bound 16 rules total (eight per axis), 32 native axes, 32 values and four order fields. SQL-node/byte, memory/time/thread limits apply. Expansion checks themselves are resource-bounded queries, not advance cardinality estimates; large legitimate broadcasts may fail clearly. Never truncate an active result. Diagnostics include input rows, profiles, matches before overlay, winners, overwritten matches and replaced identical assignments.

## Pivot discovery and APIs

Authoring order is **optional object Pivot → optional Date resolution → optional Multi-grain resolution**. Each stage reads the preceding effective schema. Pivot configuration belongs to this Transaction (`definition.pivot`), independently of other objects using its Managed Table. Discover enumerates actual attribute literals from the saved managed input; users choose output columns, then map those columns to native/dimension attributes. All present column restrictions apply together; absent values follow the explicit blank/allow-all rules.

Raw Data Preview reads the imported physical source. Normal **Preview** executes the configured stages in order, validates the full input, and displays at most 50 transformed rows. Intermediate Pivot and Calendar previews are also available before native mapping is complete. A final transaction Preview requires a current built native population; it does not apply measure aggregation, which is configured later.

Revision-scoped paths:

- `GET /transactions/resolution/options?dimensionId={id}`: reachable targets/labels.
- `POST /transactions/resolution/preview`: unsaved TransactionInput; validate and show at most 50 rows/diagnostics using the Source table component. Requires current native population; saves/creates nothing.
- `GET /transactions/{id}/schema`: effective columns/value IDs. Do not invent native-key types before population build.
- `POST /transactions/input/schema` and `/transactions/input/preview`: `{objectId, sourceId, pivot}` → object-owned pivot schema/sample, independent of native mapping.
- Existing CRUD persists pivot/calendar/resolution metadata; Build Semantic Network compiles the same stages when loading measures.

Preview captures/renews read leases and rejects revision/input changes; materialisation uses build pins. Both use the same compiler/checks. Measures are not needed to configure or preview resolution.

Additional columns to retain appears directly above Application order. Retained columns remain visible/selectable even when used for ordering. Application order accepts text, numeric and date scalar columns; text is ordered alphabetically (for example `P10` before `P2`), numbers/dates naturally. Higher values apply later; NULL and equal-order overlapping assignments fail. Distributed value columns cannot also control order.

## Date ranges and native periods

Calendar resolution is an optional **transaction stage before native-key/multi-grain mapping**. Raw Source and Managed Table schemas stay unchanged. Select Start/End, a daily Time dimension, and exposed calendar attributes to bring into the transaction's effective input:

```json
"calendarResolution": {
  "dimensionId": "Time",
  "startColumnId": "col_start",
  "endColumnId": "col_end",
  "selectedColumnIds": ["week_id", "month_id"]
}
```

The daily Time dimension supplies its configured date and ordering columns. Each input record expands to its eligible calendar days, inclusively; original fields and values are copied unchanged. Selected calendar attributes have stable transaction-specific `txcal_...` IDs and retain their types/lineage. A name collision adds `Calendar_`/a suffix. Internal day/order fields retain stable IDs. No physical staging table or managed-source mutation is needed.

Example: a promotion from Jan 30 through Feb 2 maps via four calendar rows. If all days belong to Week W1, the resolved input still distinguishes `(W1, January)` and `(W1, February)`. Both Week and Month can map to native grain keys. Use ordinary direct key mappings or multi-grain Column-equals-attribute rules for calendar fields; Product/Location level-value rules remain independent. Period IDs come from calendar data, including fiscal calendars; never infer ISO weeks from names.

The calendar stage does not allocate values. In multi-grain resolution, per-value `mode` distributes across matching non-time members and `timeMode` across eligible calendar days: Split divides by the original matching count; Copy repeats. The new UI defaults Across dates to Copy and lets the user change it. Source-order winners are selected per full native key **and day** before folding, so a later partial-period assignment changes only its own days. Overwritten shares are not redistributed. Direct transactions retain every source record and apply ordinary transaction/Time aggregation without assignment overlays.

Before the native LEFT JOIN, the measure's configured Time policy combines daily contributions; NonTime and Time stages honor aggregation order. First/Last uses calendar order and rejects ambiguous ordering. Example: a copied daily value of 10 over three days in W1/four in W2 gives 30/40 under Time SUM, or 10/10 under AVG. No aggregation is hard-coded. Further source materialisation follows aggregation-derived split/repeat; interactive edits follow measure disaggregation policies.

Readiness/validation: capture and lease the calendar's managed artifact even if it is not a planning population dependency. Apply calendar filters; identical day/attribute rows deduplicate, conflicting day/attribute/order tuples fail. Accept Date/Date32 or ISO YYYY-MM-DD strings; reject missing/invalid/reversed endpoints, out-of-bounds ranges, no eligible dates, unmatched native mappings and expansion beyond configured limits. Sparse calendars define eligible days; missing days are not synthesized. A weekly-only calendar requires a daily calendar first. DateTime needs a future timezone contract.

UI flow: enable **Resolve a date range using a calendar**, select endpoints/calendar/attributes, optionally preview calendar data, then map derived fields. The effective schema is obtained from the backend; configuration persists in MongoDB. Preview uses the shared table component (50 rows), saves nothing, and does not require a built native dimension.

Additional endpoints (under the revision):

- `POST /transactions/calendar/schema`: `{transactionId, sourceId, calendarResolution}` → raw plus derived columns, before native mappings are complete.
- `POST /transactions/calendar/preview`: same payload → expanded input and row count, independently of native mapping/build.
- `POST /transactions/resolution/preview`: full transaction → mapped/resolved output; requires a current native planning population. Supports direct calendar transactions too.
- `GET /transactions/{id}/schema`: saved effective transaction columns for measure selection.

Compatibility: saved axis-level `resolution.axes[].dateRange` remains supported with its original single native Time key and Split/Copy semantics. The UI restores that legacy configuration; new range configurations use the independent stage. Do not mix both contracts. Absent `calendarResolution` is omitted from serialized definitions to preserve old metadata hashes. Engine fingerprints use `transaction-resolution-v3-calendar-stage`; rebuild stale materialisations after compiler changes.

## Implementation and regression coverage

Owners: `schemas/transactions.py`, `api/transactions.py`, `services/transaction_resolution.py`, `engine/{calendar_resolution,transaction_resolution,date_range_resolution,measure_compiler}.py`, and TransactionCalendarEditor/TransactionResolutionEditor/TransactionDateRangeEditor. Measure compilation caches the resolved relation without changing downstream policies.

Tests cover order reversal/ties, NULL winners, Split/Copy/no redistribution, typed selectors, missing/unmatched/all-member constraints, shadowed rows, legacy partitions, expansion limits, native measure loads, sparse/full date ranges, fiscal weeks, Time AVG/First/Last, partial-period overrides, stale leases, saved UI state and current-table preservation. All data-changing fixtures are disposable.

A schema/preview request never changes a shared Managed Table. Pivot output IDs include owner object, stage, pair and literal, preserving identity across renames/revisions while isolating owners. Column discovery precedes semantic mapping; no automatic dimension matching by display name is inferred. Transaction schema endpoints include generated pivot/calendar columns, so referenced measures can select numeric pivot outputs.

Final-output filters use the shared typed engine module and nested AND/OR editor. See [Filters](FILTERS.md) for stage placement, scalar operators, NULL semantics, schema APIs and compatibility.

Transformation panels start collapsed. Dimension Preview runs the complete configured pipeline, including Pivot and final Filters; the Dimension pivot editor has no separate intermediate preview button. Base Level Visibility is a read-only derived policy per expanding Time/Non-time axis: **Equal** for SUM and **None – Repeat Values** otherwise. It uses the same policy selector presentation as edit Dis-aggregation, but does not override edit policies or persist a second source allocation setting.

Transaction mapping is a two-option switch: **Create Dimension** (default for new Transactions, creates and maps a distinct grain population) and **Use Existing Dimension**. Under the latter, **Resolve multi-grain data** switches between direct key mapping and multi-grain configuration. Saved Transactions retain their existing mapping when reopened. No Dimension is an authoring shortcut, not a persisted unmapped Transaction; it is unavailable when editing an existing Transaction. Filters are available for all mapping workflows. For No Dimension, choose fields from the Pivot/Date output; they persist on the generated directly mapped Transaction and run after mapping, while the generated planning population remains independent of Transaction filters.

Direct key-mapping selectors use **{Native Dimension name}.{key}** so users can identify the target level and key before selecting the Transaction field.

Direct key mappings render as a vertical list: each target key is on the left with its Managed Table field selector on the right, stacking on narrow screens. Labels and mapping identity remain unchanged.


SQL inspection: **View SQL** beside Preview compiles the current Transaction pipeline (including enabled Pivot, Date and multi-grain stages and final filters) without executing the output query. Intermediate Pivot/Date previews also expose their stage SQL. [Details](ENGINE_AND_STORAGE.md#exposing-final-sql).

### Suggested Pivot configuration

Pivot Data's **Suggest pivot** button calls the scoped managed-table suggestion API with the Source ID. It profiles at most 100 rows before object-local transformations and fills an editable draft; it does not save the Transaction or modify its shared input. Review sampled outputs and record keys, and use Discover to include other selector values. Existing Pivot → Date Resolution → mapping/resolution → final Filters execution order and validation remain unchanged. See [Pivot suggestions](SOURCES_AND_MANAGED_TABLES.md#pivot-suggestions).

**Match native attribute** lists only the selected native Dimension's actual output fields: grain keys, population attributes and explicitly selected relationship attributes. Reachable but unselected fields from other Dimensions are excluded; add them to the native Dimension before configuring a new match. The options API preserves saved catalog-qualified references as aliases of the corresponding native output, without listing duplicate choices or rewriting saved metadata. Saved matches with no native output show a prompt to select/add the required attribute. Calendar-resolution choices remain independent.


Saved objects own independent transformed views. Normal Preview requires saved changes and reads that view; Raw Preview reads the physical source. See [Save, Preview and publication rules](OBJECT_VIEWS.md).
