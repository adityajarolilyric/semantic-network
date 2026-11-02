# Transactions: direct, multi-grain and date-range resolution

A Transaction references a saved Managed Table and one native planning level. Measures are defined later, so transaction expansion has its own per-value Split/Copy policy. It never guesses a measure aggregation/disaggregation policy. Sources stay unchanged; resolved relations are CTEs shared by Preview and Materialise, with no extra physical table, imported Source or persistent resolved view.

## Direct mapping

Map every native grain key exactly once to the effective source schema. Multiple detail rows per native key are allowed: each referenced measure independently aggregates by **all** mapped keys using its configured policy, then LEFT JOINs the native planning population. Reject unmatched source keys explicitly; a LEFT JOIN alone would silently drop them. Preserve population rows without transactions and apply the measure's missing-value policy.

`mappingMode="direct"` is backward compatible. `expectedRowsPerGrain` defaults to `many` and is hidden; legacy `zero_or_one` declarations remain readable/preserved on edits. Where an explicit source key exists, a zero-or-one declaration must match it. Declarations do not independently verify live data.

## Multi-grain assignment semantics

Use `mappingMode="resolve_multi_grain"` when rows assign values at native or coarser grains. Match the **actual native intersections**, not a Cartesian product of dimension members.

- Fixed-column rules map supplied source fields to native/dimension attributes. All present restrictions must hold.
- Level/value rules map a selector literal (for example id or brand) and member value to a stable semantic attribute.
- Every native axis needs a constraint unless `allowAll` explicitly permits absence. Missing is not an implicit broadcast. Time must be covered too.
- A row is one independent assignment. EAV rows that jointly describe one fact must be pivoted/grouped using fact identity first.

**Explicit source application order wins at any grain.** Later coarse rows can overwrite detail; specificity is not priority. ClickHouse row order, SELECT/UNION order and generated row numbers cannot reconstruct application order. Require a meaningful sequence or timestamp. Overlapping rows tied on the complete configured order fail, rather than choosing an arbitrary winner.

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
        "ruleId": "product_selector",
        "kind": "level_value",
        "selectorColumnId": "col_level",
        "valueColumnId": "col_member",
        "selectors": [
          {"value": "id", "target": {"dimensionId": "Product", "columnId": "product_id"}},
          {"value": "brand", "target": {"dimensionId": "Product", "columnId": "brand"}}
        ],
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

## Selector discovery and APIs

Choosing a Level column automatically discovers distinct nonempty literals from the **saved managed output**, including pivot columns. Display fixed literals; users map each to a planning/dimension attribute. Discover refreshes, preserves surviving mappings and removes disappeared literals; new literals need mapping. No manual selector/add/remove inputs. Reject more than 32 literals or values over 200 characters; NULL/empty literals are not mappings. Discovery fills only unsaved form state.

Reuse `GET /managed-tables/{source}/attribute-values?managedOutput=true`, with paginated values/nextOffset and a captured artifact ID. This path leases/verifies the saved output and rejects changed/expired artifacts; Pivot's default discovery still reads raw source columns.

Revision-scoped paths:

- `GET /transactions/resolution/options?planningLevelId={id}`: reachable targets/labels.
- `POST /transactions/resolution/preview`: unsaved TransactionInput; validate and show at most 100 rows/diagnostics using the Source table component. Requires current native population; saves/creates nothing.
- `GET /transactions/{id}/schema`: effective columns/value IDs. Do not invent native-key types before population build.
- Existing CRUD persists resolution; Build Semantic Network compiles it when loading measures.

Preview captures/renews read leases and rejects revision/input changes; materialisation uses build pins. Both use the same compiler/checks. Measures are not needed to configure or preview resolution.

## Date ranges and native periods

A transaction can provide inclusive Start/End instead of an exact Time key. Resolve actual calendar days, distribute/apply source-order overrides at member×day detail, then map surviving contributions to the native Day/Week/Month key. Period identity comes from explicit Time attributes, including fiscal IDs; do not derive ISO weeks or assume seven days.

Replace one native Time axis's rules with:

```json
{
  "grainColumnId": "week",
  "rules": [],
  "allowAll": false,
  "dateRange": {
    "dimensionId": "Time",
    "calendarKeyColumnId": "fiscal_week_id",
    "startColumnId": "col_start",
    "endColumnId": "col_end"
  }
}
```

The Time dimension must have daily base grain; reuse its date/ordering columns. Its calendar key matches the existing native key and may be String, integer or Date. Selected values add independent `timeMode`, for example `{"sourceColumnId":"col_qty","mode":"split","timeMode":"copy"}`: mode distributes across non-time members; timeMode across eligible calendar days. All four Split/Copy pairs work. Counts use original complete matches before overrides and filtered calendar membership, not arithmetic end-start+1.

Capture the calendar's managed artifact even when it is not otherwise a population dependency. Apply Time filters and build a distinct day→period→order relation. Identical rows deduplicate; conflicting period/order for one day fails. Parse only ISO YYYY-MM-DD, Date/Date32; reject NULL/invalid/reversed endpoints, ranges beyond selected calendar bounds and ranges with no eligible dates. No clipping/timezone guessing. Match actual native intersections and require every eligible day for each selected member to have native coverage.

Range bounds participate in identical-assignment reduction. Winners are selected per complete native key **and resolved date**, so a later two-day assignment changes only those days even inside one Week. Expose canonical native keys, Resolved date, Calendar order and Float64 distributed values; repeated weekly keys are intentional daily contributions.

Fold daily contributions into a coarser native period using the **measure's Time aggregation**, before the native LEFT JOIN. First/Last uses calendar order; ambiguous ordering fails. A proven native daily identity retains ordinary native aggregation; legacy independent series honor configured axis order. Example: 70 split across seven days, three in W1/four in W2, yields 10/day; Time SUM gives 30/40, AVG gives 10/10. NonTime policy does not replace this fold. Subsequent Base/finer propagation uses the measure's disaggregation; it does not automatically reuse original daily distribution as a separate measure.

UI offers Exact value/Date range on the native Time axis, Start/End, daily Time dimension and period attribute. Prefill only uniquely proven mappings. Show the calendar date as explanation, not a redundant selector. Across members/Across dates appear per selected value; saved range choices restore on reopen.

Both endpoints are required (the same column can mean a single day). Exact/range is configured per transaction, not inferred per row. Support one complete native Time key, such as YearWeek; separate partial Year/Week keys fail. A weekly-only calendar needs a daily calendar first; no separate bridge-source feature exists. DateTime endpoints await a timezone policy. Calendar filters define eligible days; missing dates are not synthesized. Existing limits apply to day expansion. Fingerprints use `transaction-resolution-v2-day-range`; rebuild stale values after compiler changes.

## Implementation and regression coverage

Owners: `schemas/transactions.py`, `api/transactions.py`, `services/transaction_resolution.py`, `engine/{transaction_resolution,date_range_resolution,measure_compiler}.py`, and TransactionResolutionEditor/TransactionDateRangeEditor. Measure compilation caches the resolved relation without changing downstream policies.

Tests cover order reversal/ties, NULL winners, Split/Copy/no redistribution, typed selectors, missing/unmatched/all-member constraints, shadowed rows, legacy partitions, expansion limits, native measure loads, sparse/full date ranges, fiscal weeks, Time AVG/First/Last, partial-period overrides, stale leases, saved UI state and two-table preservation. All data-changing fixtures are disposable.
