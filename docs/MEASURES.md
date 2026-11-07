# Measures and semantic-network materialisation

Each measure has one native dimension. Referenced measures read a Transaction value; calculated measures use a typed formula. Precompute compatible representations at Base and at native Dimensions used by Transactions when rollup paths exist. Lookup-only Dimensions do not automatically receive measure values. A measure on an independent level may remain native and roll directly to supported coarser levels; **Base connectivity is not a definition requirement**.

Calculated-measure Output grain choices are limited to Dimensions bound to Transactions and standalone Transaction grains. Lookup-only Dimensions remain available as formula attributes through valid lineage, but are not offered as output grains. This is an authoring restriction; saved formulas are not silently reassigned.

Referenced measures can also be created from the optional **Measures** grid during Transaction creation. Select supported output columns and their type-appropriate policies; names and references are derived. Definitions save atomically with the Transaction, with the same validation and defaults described in [Transactions](TRANSACTIONS.md#create-measures-with-a-transaction). Materialise remains a separate action.

## Typed measures and direct attribute formulas

Measures support Number, String, Date and Boolean. Reference types are derived from effective Transaction columns; formula types are inferred and validated from the AST. Numeric references remain Float64. Supported physical nonnumeric inputs are String/FixedString, Bool and Date/Date32 (including nullable/low-cardinality wrappers); DateTime/timezone semantics are not included yet. The UI shows the inferred nonnumeric type without a type override.

Transaction columns must first become referenced measures so native source grouping is explicit. Dimension and Dimension attributes can be inserted directly as `[Product.Brand]` or `[PLT.product_id]`; no intermediate measure or extra Transaction attribute column is created. Persist `attributeRef` with exactly one dimensionId/dimensionId plus columnId. Resolve attributes through explicit key/dimension lineage, not matching labels. They must be uniquely determined by the calculation grain; a Product key may determine Brand, but Brand cannot determine an individual Product. Lookup ambiguity/coverage checks remain active. AggregateFirst attribute formulas can become unavailable when a requested grouping no longer determines their inputs; use CalculateFirst plus a supported result aggregation to roll calculated labels/flags upward.

Allowed Time/Non-time policies are type-specific:

- String: disabled **None** in the UI (`single_value` internally).
- Date: Same value only, Earliest (`min`), Latest (`max`).
- Boolean: Same value only, Any true (`any_true`), All true (`all_true`).
- The engine retains legacy/API support for First/Last on Time with explicit ordering; these differ from earliest/latest date values. The simplified typed-measure UI offers one shared Non-Time-based policy instead.
- Numeric methods are unchanged. COUNT/COUNT DISTINCT do not change a String/Date/Boolean measure into a number; use a separate numeric calculation/measure.

String/Date/Boolean results are read-only and have no allocation policy or automatic finer/Base distribution. Referenced and CalculateFirst values stay native and roll to proven compatible coarser grains. AggregateFirst recomputes from independently available inputs at the requested grain; it never distributes a cached categorical/date/Boolean result. Missing/all-null values remain NULL, never an empty string, False or an artificial date. Ignore/Propagate null policies still apply. Typed numeric results (for example DAYS_BETWEEN) retain numeric policies.

Same value only returns the common populated value. Conflicting populated values produce a separate **Mixed** state, not a stored text sentinel or ordinary missing value. Internal state codes are 0 unavailable, 1 complete and 2 mixed; hidden `mixedFor` columns preserve conflicts alongside values in the same existing values table. Mixed propagates through combining rollups and dependent formulas, preventing a later grouping/COALESCE from turning an ambiguous label into an apparently valid value. Ordered First/Last carries the selected cell’s value and state; an earlier Mixed cell does not invalidate a later known Last value. NULL and Mixed remain distinguishable in Decision View through `cellStates`; strings such as `001` retain their text, Booleans display True/False, Dates display ISO dates and numeric formatting stays at two decimals.

Formula additions: double-quoted String and TRUE/FALSE literals; `=`, `!=`, `<>`, `<`, `<=`, `>`, `>=`; IF, AND, OR, NOT, IS_NULL, CONCAT, UPPER, LOWER, TRIM, DATE, ADD_DAYS and DAYS_BETWEEN. IF requires a Boolean condition and matching branch types; unknown conditions stay NULL. Arithmetic remains numeric-only. DATE takes an ISO string; ADD_DAYS requires an integral day count; DAYS_BETWEEN(start,end) returns end minus start in days. Unsupported implicit conversions, raw SQL and invalid function signatures fail validation.

Examples: `IF([Projected Stock] < 0, "At Risk", "Healthy")`, `ADD_DAYS([Dispatch Date], 2)`, `CONCAT([Product.Brand], " / ", [PLT.product_id])`. AggregateFirst shortage compares aggregated Supply and Demand; CalculateFirst with Any true answers whether any native intersection has a shortage. These are distinct business questions.

`GET /measures/formula/attributes?dimensionId=...` returns qualified labels, stable attribute references and semantic types from effective metadata. The transaction Measures grid also accepts supported typed columns; for multi-grain Transactions, numeric resolution values and explicitly retained typed columns are eligible. Typed columns are never Split/Copy numeric values. Existing formulas, numeric edits and the one-permanent-table storage invariant remain intact. Typed evaluation fingerprints distinguish the new semantics. Unchanged legacy numeric fingerprints and baselines remain compatible, allowing incremental builds to retain existing numeric edits while adding typed measures; no Full Refresh is required merely for this extension. Deployment does not rebuild data or replace manual edits.

## Shared materialisation targets

The engine derives storage targets from saved metadata; there is no `materialise measures` property:

- Always store each measure at its own native/output grain.
- Base is a shared target when selected. Compatible numeric measures use the established Base visibility policies; incompatible and typed/count restrictions remain unchanged.
- Every Transaction-native Dimension (including standalone Transaction grain contexts) is a shared target. A measure can arrive by rollup from its native grain or from a compatible Base representation. Do not distribute into an arbitrary finer Transaction-native grain.
- A calculated measure's output grain is always retained for that measure, even when no Transaction uses it. It becomes a shared target only if a Transaction uses it or it is Base.
- A lookup-only Dimension or unused projection gets no new values representation. Creating it alone cannot expand the measures × Dimension storage matrix.

For example, Forecast native to Product–Location–Week can roll up to Brand–Zone–Month when Sales uses that native Dimension and mappings prove the path. Brand alone remains a reporting attribute/lookup until explicitly used as a Transaction grain. AggregateFirst recomputes dependencies at a target; CalculateFirst aggregates calculated native values. Aggregation always comes from measure metadata.

Decision View exposes only attributes present at Transaction-native or Base Dimensions. It can still group those attributes more coarsely on demand. Existing obsolete values bindings are removed when a new generation activates; retained artifacts remain subject to normal read-lease/retention protections. Incremental baselines are reconstructed from captured inputs across the policy upgrade so unchanged values and manual edits can be reused. Older queued jobs must be resubmitted rather than mixing placement policies.

## Source materialisation versus edits

A source build and a user edit use different disaggregation rules. Never infer SUM/AVG from measure names or business meaning; use the saved aggregation and NULL policies.

**Materialisation of new/invalidated measures and Full Refresh:** initialise the supported Base representation from its native seed using the aggregation of the axis being expanded:

- SUM: divide equally across eligible finer combinations for that stage.
- Other supported methods: repeat the value.
- Time expansion uses Time aggregation; non-time expansion uses NonTime aggregation.
- Expand axes in reverse aggregation.order. Counts use actual distinct intermediate combinations in the target population, within each parent and previously expanded coordinates. Irregular intersections therefore receive stage-wise equal shares, which need not be globally equal across all Base rows.
- Do not consult allocation, previous values, or another weighting measure. Missing edit weights cannot block or leave holes in source initialisation.
- Base-native values load normally. AggregateFirst recomputes dependencies at each requested grain; its calculated result is not split.

For SUM/AVG/MIN/MAX and supported Time value aggregations, this is designed to reconstruct the parent subject to rounding. COUNT/COUNT_DISTINCT still count intermediate values during aggregation: repeating a count is not an inverse of counting. Their configured semantics are preserved rather than replaced by SUM or AVG.

**User edits:** allocation.mode remains the edit disaggregation policy. New measures default to Weighted by self. Explicit None/null repeats; legacy saved definitions with an omitted policy retain Repeat; Equal divides across eligible rows; Weighted uses the captured basis measure; Weighted by self uses captured populated values of the same measure. Always freeze weights before writing. Missing/zero-total edit patterns retain their existing validation. The policy may be stored on Base-native measures for coarser edits and does not need a Base path for independent native/coarser edit paths.

After an edit, use the edited Base-or-native write representation to aggregate compatible levels and recalculate dependents. Do not rerun source initialisation over edited values. Dependent edit-time propagation retains configured edit policies. Repeat+SUM can increase a total; no conservation rule, remainder redistribution or inferred AVG is imposed. See [edit distribution](DECISION_VIEW.md#edit-distribution-and-recomputation).

Coverage flags remain available for legacy/edit-derived incomplete representations: they are independent of source NULL policies, suppress partial formulas/totals, and can recover a compatible native representation. Source initialisation no longer becomes incomplete because allocation weights are missing. Internal flags stay in the existing values table and are hidden from previews.

## Native loading and propagation

1. Independently aggregate each transaction by all mapped native keys using the measure's NonTime policy. Date-range contributions to a coarser native period use the explicit Time-fold rules in [Transactions](TRANSACTIONS.md#date-ranges-and-native-periods).
2. LEFT JOIN each aggregated input to the native population. Never join raw transaction details/multiple raw inputs first: that multiplies other measures. Check unmatched transaction keys separately; preserve missing native groups according to missingTransactionValue.
3. Keep this as a private native seed. If native→Base is proven, initialise finer values from axis aggregation as above, then derive compatible defined-level representations from Base—including the native representation. Do not silently substitute its private seed for this roundtrip result.
4. If Base is unreachable, preserve native and supported direct coarser representations, even when that coarser level is also related to Base. A missing Base measure must not hide a valid native path.
5. Omit unsupported representations with actionable reasons. Failure at a measure's own native level is fatal, not a partial-success definition.

Paths come from the shared relationship analyzer using explicit projection/dimension/time lineage. Brand×Zone×Channel follows finer Product/Location/Customer coordinates; names or key counts never establish coarseness. An attribute need not be exposed as an output if a primary-key binding can resolve it; non-key bindings promise only mapped/selected attributes.

## Time and NonTime aggregation

NonTime supports sum, avg, min, max, count and count_distinct; Time additionally supports first/last with explicit Time ordering. `aggregation.order` is `nonTimeThenTime` (default) or `timeThenNonTime`. Stage one preserves the other axis's fine keys; stage two uses its configured method. Do not sort Time labels or infer its policy from NonTime.

Methods operate on the defined intermediate measure values. AVG of averages or summed distinct counts cannot be assumed equivalent to aggregating original facts. Preserve explicit stage semantics and let Decision View fall back to a finer representation when further composition is unsafe. Never replace the configured method with an additive shortcut.

For String, Date and Boolean measures, the UI presents one **Aggregation** setting and writes the same method into the existing NonTime and Time metadata slots. There is no separate Time selector or aggregation-order selector for these types. String shows disabled **None** (`single_value` internally): one distinct populated value is returned, conflicting values remain Mixed. Date offers single value/earliest/latest; Boolean offers single value/any true/all true. Numeric measures retain separate axis policies. Existing saved/API-authored axis-specific typed policies remain executable; authoring through the simplified UI applies the shared policy when saved.

## Calculations and stored expressions

Persist a versioned **semantic AST** with stable measure/grain IDs, plus descriptive display text. It is not SQL or a serialized SQLGlot object. Reusing it avoids reparsing/binding formulas, but target-grain planning and SQL generation still happen at execution. Translate the validated AST to runtime SQLGlot/ClickHouse expressions; never execute display text.

The authoring language is `planning-v1` (semantic AST version 2). Lark is the authoritative backend parser for arithmetic, comparisons, typed literals, bracketed references and the function registry. `[TableName.ColumnName]` is the single authoring syntax for Dimension and Dimension columns, including grain keys. For example, `[Product.Brand]` or `[PLT.product_id]`. Any named dimension may be referenced when its column is uniquely determined at the evaluation grain; it is not restricted to Base. Dimension columns accept their UI display name or semantic column name. Duplicate qualified names are rejected; stable owner/column IDs are persisted. Legacy ATTR/GRAIN functions and grainRef ASTs remain readable but are not offered in function discovery or inserted by the editor. SQLGlot lowers resolved expressions to ClickHouse ASTs. The browser requests debounced validation and function discovery; save always reparses versioned source and resolves stable IDs again, ignoring any advisory client AST. Legacy AST-only definitions continue to execute without reparsing historical display text.

- **AggregateFirst:** resolve inputs at the requested grain, then evaluate the formula there. Recalculate for finer/coarser/ad hoc grains; do not aggregate or disaggregate a cached formula result. A stored equivalent-grain result may be reused. Target-specific caches track grain and dependencies.
- **CalculateFirst:** evaluate at native grain, then initialise finer values from its result aggregation; allocation is an edit policy. During edits, recalculate native results before supported rollups.

Example: daily FQTY=100,80 and SQTY=90,100 give Error=10,20. At the combined grain, AggregateFirst `ABS(FQTY-SQTY)` gives `ABS(180-190)=10`; CalculateFirst with SUM gives 30. Null operands propagate unless an explicit formula such as COALESCE changes that behavior. AggregateFirst omits output aggregation rules; CalculateFirst requires them.

## Calculated measure dependencies

Referenced and calculated measures use the same `[Measure Name]` syntax. For example, `Profit = [Revenue] - [Cost]` and `MarginPct = 100 * [Profit] / [Revenue]`. Dependencies are derived from stable `measureRef` IDs in the saved formula AST; no second dependency list is stored in MongoDB.

Every dependency must be evaluable at the grain where its consumer needs it. AggregateFirst resolves children at the requested grain and recalculates. CalculateFirst resolves children at its own Output Dimension, calculates there, and propagates its result with its own policies. Both types can reference either type, including CalculateFirst → AggregateFirst when the dependency is available at the parent's output. Never flatten a chain across these boundaries: a sum of native squared values differs from a square of an aggregated value. Matching labels or pairwise compatibility do not establish a common evaluation grain.

`MeasureDependencies` derives a DAG and validates the requested transitive closure in dependency-first order. Compilation caches nodes by measure/grain; shared children are compiled once per context. A child need not have a separately materialised column at every evaluation grain. Saved snapshots may be reused only under existing semantic/grain/fingerprint checks. Incremental fingerprints include the full formula closure; changing a child invalidates its consumers, while unrelated values and edits remain reusable. Editing a referenced input refreshes its transitive formula dependants. Calculated measures remain read-only.

Self/indirect cycles, missing measures, incompatible grains/types and unsupported typed/count propagation fail. Errors show measure names and dependency paths. The formula library includes calculated measures; `GET /measures/formula/measures?nativeGrainId=...&objectId=...` returns grain availability and cycle explanations for each saved measure. Incompatible suggestions are disabled, while typed formula validation and Save remain authoritative. Keyboard insertion skips disabled suggestions.

Existing bounds remain: formula syntax nesting 32, dependency depth 128, and configured CTE/SQL/query resource limits. A dependency DAG/cache does not make ClickHouse CTEs physical intermediate tables; highly branching full-refresh queries can still reach the configured memory limit. No staging tables, additional permanent tables, metadata migration, Base-policy change or edit journal is introduced.

## NULL policies

NULL means missing/unknown, not zero. Apply policies in this order:

1. **Source NULL handling** is always Preserve: NULL transaction values pass through to the configured aggregation policies. The UI has no source-null selector. Existing `reject` metadata is interpreted as `preserve`; this does not override aggregation null policies or result nullability.
2. **Null input policy** applies to aggregation groups: Ignore aggregates non-NULL values; Propagate returns NULL if any input is NULL. For [10,NULL,20], Ignore SUM=30 and AVG=15; Propagate produces NULL.
3. **All-null result policy** applies when Ignore has no non-NULL inputs: return NULL or zero as configured. Propagate wins over an All-null Zero fallback. Empty/all-NULL data is not universally zero.
4. **Missing transaction value** handles an absent native transaction group *after* the LEFT JOIN, independently of a present row with a NULL value; choose NULL or zero.

These policies apply again at later aggregation stages. Nonnullable result definitions reject remaining NULLs. Existing unit/alias metadata stays internal: Unit/Aliases controls are hidden, new unknown units remain null, known source units may be inherited and contradictory known units still fail validation. Do not guess EA/dimensionless.

## Numeric policy

Numeric referenced measures always use semantic `resultType.type="number"`, compiled as Float64 with policy-derived nullability. Convert integer/decimal/float source values **before** aggregation/disaggregation; splitting integer 1 over two rows can yield 0.5 each. Source schemas do not change. Backend create/update enforces this even if clients omit/override resultType. Unsupported physical types and invalid declared decimal parameters fail; supported nonnumeric references use their derived semantic type.

New calculated measures also default to Float64; legacy/new integer calculation declarations normalize to Float64. Explicit Decimal calculations retain precision/scale checks and bankers rounding. All data-type/precision/scale controls are hidden; calculated nullable-result remains configurable. Reject non-finite Float64 and invalid/overflowing results; no universal exact-total guarantee or rounding remainder redistribution is added.

Float64 is approximate (roughly 15–17 significant digits): decimal sources lose exact precision and sufficiently large integers may round. Exact-decimal requirements need an explicit policy, not an implicit change to all references.

`normalize_measure` interprets legacy referenced integer/decimal metadata as Float64 on reads/resolution/compilation without rewriting immutable records. Saving a draft persists normalization; clones/new builds use it. Fingerprints include `float64-referenced-v3-preserve-source-nulls`, with current compiler `measure-v4-aggregation-derived-round6-sqlglot-27.28.1`; old generations become Needs rebuild. Rebuild drafts through Materialise, or create a draft from a published revision. No destructive numeric migration or auto-materialisation runs at startup. Frozen previous weights are converted appropriately.

## Materialise and consistency

Materialise is its own navigation item below Measures. It has **Build Semantic Network** (incremental) and a separate confirmed **Full Refresh**, visually separate from the dimension selector that automatically previews up to 50 rows. “What gets materialised at …” explains native placement, configured policies, derivation/recalculation and unavailable reasons. The single default model/revision is implicit; no lifecycle controls are exposed.

Build current managed views and planning populations first. The network build captures semantic objects and input artifacts, pins them, compiles a dependency DAG and inserts candidate values generations into the existing values table for each level. Validate source NULLs, source/population coverage, lookups, child coverage, grain/cardinality, types and limits. Formula dependency/basis failures cannot activate stale/partial results.

One final MongoDB transaction rechecks revision/fence/input fingerprint and swaps **all** level bindings to the same generationId. Failure/cancellation/staleness preserves every prior binding. Metadata/population changes stale values. Published revisions can read but cannot rebuild; clones build their own artifacts. Publication requires managed/population readiness, not a measure generation.

Artifacts record generation, population identity, key/measure maps, compiler/numeric policy, derivations and unavailable reasons. Snapshots remain separate bounded documents. The [shared storage protocol](ENGINE_AND_STORAGE.md) preserves the strict population+values pair, including rebuilds/removing measures. Interactive edits use this same coordinated generation lifecycle, with no journal.

## APIs, modules and verification

Revision-scoped APIs:

- `POST /measures/execution/build`: revision If-Match and Idempotency-Key; HTTP 202/run.
- `GET /measures/execution/levels/{levelId}?preview=true`: state, schema, generation, row count, unavailable measures and at most 50 rows; omit preview for status only.
- `GET /measures/{measureId}/capabilities`: structural paths, mappings, via-Base availability and native/coarser edit eligibility. Weights, dependencies or data checks may still prevent execution.

`MeasureCompiler` builds SQLGlot-rendered ClickHouse CTEs from typed metadata; `MeasureBuildService` handles snapshots, pins, durable runs and coordinated activation; MeasureAPI owns HTTP. Limits: `METADATA_MEASURE_MAX_NODES=1024`, `METADATA_MEASURE_MAX_SQL_BYTES=4194304`, plus warehouse row/memory/time/thread bounds. Queries use join_use_nulls=1 and http_write_exception_in_output_format=0 to retain reliable domain errors after result headers.

Tests cover all aggregate methods/axis orders, irregular staged initialisation, time-only expansion, independence from edit weights, six-place storage/edit propagation, independent native grouping, edit Repeat roundtrips, NULL ordering, Base-native policies, independent native/coarser paths, AggregateFirst recalculation, attribute hierarchies, weighted snapshots, fractional integer-source distribution, Decimal guards, failed-generation isolation and atomic multi-level activation.

## Numeric storage and display

Store measure representations rounded to six decimal places using bankers rounding (legacy Decimal scales smaller than six remain smaller). Round at representation boundaries before downstream calculations/rollups, so coarser representations aggregate the actual six-place Base/write values. The shared materialisation fingerprint includes aggregation-derived-disaggregation-v1-round6; older networks require an explicit rebuild. Float64 remains the referenced measure type. No rounding is applied to imported raw source data.

Pivot measure cells and Float/Decimal preview cells display two decimal places; identifiers and integer source columns keep their representation. The formatter works on decimal strings to avoid losing large Decimal precision. Edit inputs use the original stored value, not the two-place display. Rounding may leave small residual differences from source totals; do not silently balance a final child or change aggregation. Incremental builds preserve unchanged manual values. Full Refresh replaces edits from sources; source/semantic changes replace affected measures. There is no durable edit journal.

Measure authoring uses a visible **Reference / Calculated** switch. **Output Dimension** is highlighted: Reference inherits it from the transaction; Calculated selects it explicitly. This label represents the existing native dimension, with no semantic contract change. Editable measure appears before Missing transaction value.

At the end of the measure form, **Base Level Visibility policy** appears when Base is defined and differs from Output Dimension. The read-only summary uses the backend's proven grain path, shows SUM split / other aggregation repeat only for expanding axes, and follows reverse aggregation order. Unsaved aggregation choices update the explanation immediately. Unreachable paths, equivalent grains, rollups and AggregateFirst recalculation are described separately. `GET /measures/execution/base-visibility?nativeDimensionId=...` returns the current Base, relationship and expanding axes without building data.

Calculated measures at the Base output dimension hide Dis-Aggregation policies. CalculateFirst at a different output level retains the controls; AggregateFirst has no allocation controls at any level. Reference measures retain edit disaggregation controls even at Base for coarser edits. This is a UI visibility rule; saved metadata is preserved.

Formula measure buttons insert at the current caret or replace the selected text. Focus returns to the formula with a collapsed caret immediately after the inserted reference, so typing can continue; mouse and keyboard activation follow the same behaviour.

## Incremental build and Full Refresh

New/changed measures and downstream formula dependencies are computed only where their representation inputs changed. Unchanged values (including edits) are read from the captured active generation. A no-change build returns `reused=true` and preserves its generation/version. New referenced measures initialise independently from their own source; new calculated measures can use edited existing dependencies. Changed/removed output columns activate coherently in the existing values tables, with no third/staging table.

`POST /measures/execution/build` accepts `{ "mode": "incremental" }` or `{ "mode": "full" }`; the UI sends an explicit mode. An omitted body retains legacy full-build behavior for API compatibility; an object with omitted mode defaults to incremental. Full Refresh requires one UI confirmation because it replaces manual edits. Direct external source-data mutations require Full Refresh (or an explicit managed-source refresh that advances its artifact token); metadata equality alone does not detect arbitrary ClickHouse row changes. See [engine reuse and freshness](ENGINE_AND_STORAGE.md#schema-reuse-and-incremental-materialisation-direction).

## Formula language architecture

`backend/app/formula_language` owns the cached LALR grammar, function registry, symbol resolver, validation facade and scalar SQLGlot lowering. The semantic AST is dialect-independent; SQLGlot is a runtime representation, not the persisted language. The engine owns population/measure alignment, aggregate counting and grain paths. No parser or compiler creates a database client.

`GET /measures/formula/language` discovers versions/functions. `POST /measures/formula/validate` accepts text, language version, Output Dimension, returning resolved expression, inferred result type/unit and diagnostics without writing metadata. Syntax/reference diagnostics identify line/column. Metadata save repeats resolution against the current revision and enforces graph contracts. Parsing is limited to 4,000 characters and semantic nesting to 32 levels; arbitrary SQL and unregistered functions are rejected.

## Calculated counts

The measure’s **Output Dimension is its counting grain**. There is no separate counting-grain setting or metadata field. The measure can be visualised at that level or a proven coarser grain; counts are recomputed from Output Dimension inputs. Count formulas are read-only AggregateFirst calculations with no aggregation or disaggregation of the cached result.

- `COUNT(expr)`: count non-NULL values; zero and False count.
- `COUNT_IF(condition)`: count true conditions evaluated at the measure’s Output Dimension, before grouping.
- `COUNT_DISTINCT(expr)`: exact distinct non-NULL values, recomputed across the requested grouping.
- `COUNT_ROWS()`: count intersections in the saved, filtered planning population, including rows without transaction values.

For quantities `[100, 0, NULL, -5]`, these give COUNT=3, COUNT_IF(Qty>0)=1 and COUNT_ROWS=4. Counts refer to planning intersections, not raw transaction records already combined by native aggregation. Qualified column references inside a count must be resolvable at the measure’s Output Dimension. Scalar expressions can combine counts; nested count expressions are rejected. A group with no matching counting rows yields zero. Unknown/Mixed argument cells produce unknown/Mixed output rather than silently undercounting.

Decision View always recalculates these counts from captured materialised counting-grain inputs, preserving manual edits. It never sums cached distinct counts or counts cached count numbers. Compatible counting populations contribute their projected axis members to the view domain. Incremental manifests include the measure’s Output Dimension and dependencies; edits trigger downstream count recalculation. Existing numeric/typed fingerprints remain compatible, preserving unrelated edited values. Legacy COUNT aggregation policies remain supported with their prior meaning.

Temporal expressions require execution semantics beyond syntax: declared evaluation grain, explicit Time ordering/partitioning, calendar-period versus previous-row behavior, missing periods, opening seeds and history before the displayed horizon. Windows cover operations such as lagged source measures and rolling totals. SOD/EOD carry-forward is a recurrence: simple opening balance plus cumulative net flow can become a window calculation, while constrained/nonlinear balances may require ordered evaluation. Future temporal dependency edges must distinguish time-shifted, well-founded dependencies from ordinary invalid cycles. Do not promise that arbitrary recurrences transpile into one SQL window.

Calculation type is a switcher beside Measure type for calculated measures; Quick Create keeps it in advanced settings. It remains meaningful for typed results: quantities 100 and -20 give AggregateFirst `Qty < 0` = False at the combined grain, whereas CalculateFirst with Any true = True. Count formulas only permit AggregateFirst.

Legacy count definitions with a separate `countingDimensionId` are interpreted with that level as their Output Dimension. Historical captured records remain immutable. `backend/scripts/migrate_count_output_levels.py` captures an API metadata journal before deployment and commits normalized definitions afterward with version checks. Legacy grain references outside count expressions retain their original owner. Follow migration with an **incremental** network build; unchanged representation fingerprints preserve unrelated manual edits. Evaluation hashes retain the former count-field shape only for cache compatibility; no new definition persists that field. Never use Full Refresh for this migration.

Formula authoring uses the shared `FormulaBuilder` UI: inline editor, searchable Measures/Columns/Functions library, function syntax/examples and backend validation/type feedback. Native function/attribute selects are removed; no additional modal is introduced. Insertion preserves/replaces the current selection and restores editor focus. Keyboard navigation and compact Quick Create use the same insertion behaviour; parsing, typing and grain validation remain backend-owned.

### Bound Transaction labels in build previews

A bound Transaction contributes its connected Dimension as a build/preview grain, under the Dimension name only. It must not add a second Transaction-grain entry or replace the Dimension label when several Transactions share it. Standalone Transactions retain their own grain entries. The values-preview catalog contains only actual storage targets: Transaction-native grains, selected Base and calculated measures' own output grains. An unused population can be built without having a measure-values representation; these are distinct statuses. Existing Transaction-owned formulas keep their ownership while the selector displays the connected Dimension.
