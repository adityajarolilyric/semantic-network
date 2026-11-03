# Measures and semantic-network materialisation

Each measure has one native planning level. Referenced measures read a Transaction value; calculated measures use a typed formula. Precompute compatible representations at Base and other defined levels when paths exist. A measure on an independent level may remain native and roll directly to supported coarser levels; **Base connectivity is not a definition requirement**.

## Source materialisation versus edits

A source build and a user edit use different disaggregation rules. Never infer SUM/AVG from measure names or business meaning; use the saved aggregation and NULL policies.

**Build Semantic Network (including source rebuilds):** initialise every supported finer/Base representation from its native seed using the aggregation of the axis being expanded:

- SUM: divide equally across eligible finer combinations for that stage.
- Other supported methods: repeat the value.
- Time expansion uses Time aggregation; non-time expansion uses NonTime aggregation.
- Expand axes in reverse aggregation.order. Counts use actual distinct intermediate combinations in the target population, within each parent and previously expanded coordinates. Irregular intersections therefore receive stage-wise equal shares, which need not be globally equal across all Base rows.
- Do not consult allocation, previous values, or another weighting measure. Missing edit weights cannot block or leave holes in source initialisation.
- Base-native values load normally. AggregateFirst recomputes dependencies at each requested grain; its calculated result is not split.

For SUM/AVG/MIN/MAX and supported Time value aggregations, this is designed to reconstruct the parent subject to rounding. COUNT/COUNT_DISTINCT still count intermediate values during aggregation: repeating a count is not an inverse of counting. Their configured semantics are preserved rather than replaced by SUM or AVG.

**User edits:** allocation.mode remains the edit disaggregation policy. New measures default to Weighted by self. Explicit None/null repeats; legacy saved definitions with an omitted policy retain Repeat; Equal divides across eligible rows; Weighted uses the captured basis measure; Weighted by self uses captured populated values of the same measure. Always freeze weights before writing. Missing/zero-total edit patterns retain their existing validation. The policy may be stored on Base-native measures for coarser edits and does not need a Base path for independent native/coarser edit paths.

After an edit, use the edited Base-or-native write representation to aggregate compatible levels and recalculate dependents. Do not rerun source initialisation over edited values. Dependent edit-time propagation retains configured edit policies. Repeat+SUM can increase a total; no conservation rule, remainder redistribution or inferred AVG is imposed. See [edit distribution](PLANNING_VIEW.md#edit-distribution-and-recomputation).

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

Methods operate on the defined intermediate measure values. AVG of averages or summed distinct counts cannot be assumed equivalent to aggregating original facts. Preserve explicit stage semantics and let Planning View fall back to a finer representation when further composition is unsafe. Never replace the configured method with an additive shortcut.

## Calculations and stored expressions

Persist a versioned **semantic AST** with stable measure/grain IDs, plus descriptive display text. It is not SQL or a serialized SQLGlot object. Reusing it avoids reparsing/binding formulas, but target-grain planning and SQL generation still happen at execution. Translate the validated AST to runtime SQLGlot/ClickHouse expressions; never execute display text.

The UI parser supports numeric literals, unary minus, arithmetic, parentheses and ABS/ROUND/COALESCE/MIN/MAX. It resolves current measure names to IDs; bracket names containing spaces. Ambiguous names require a unique name. Persisted formulas reconstruct text from AST/current names; numeric GRAIN references retain stable identity. Backend validation owns arity, allowlists, types/units, depth, missing references, dependency cycles and availability.

- **AggregateFirst:** resolve inputs at the requested grain, then evaluate the formula there. Recalculate for finer/coarser/ad hoc grains; do not aggregate or disaggregate a cached formula result. A stored equivalent-grain result may be reused. Target-specific caches track grain and dependencies.
- **CalculateFirst:** evaluate at native grain, then initialise finer values from its result aggregation; allocation is an edit policy. During edits, recalculate native results before supported rollups.

Example: daily FQTY=100,80 and SQTY=90,100 give Error=10,20. At the combined grain, AggregateFirst `ABS(FQTY-SQTY)` gives `ABS(180-190)=10`; CalculateFirst with SUM gives 30. Null operands propagate unless an explicit formula such as COALESCE changes that behavior. AggregateFirst omits output aggregation rules; CalculateFirst requires them.

## NULL policies

NULL means missing/unknown, not zero. Apply policies in this order:

1. **Source NULL handling** is always Preserve: NULL transaction values pass through to the configured aggregation policies. The UI has no source-null selector. Existing `reject` metadata is interpreted as `preserve`; this does not override aggregation null policies or result nullability.
2. **Null input policy** applies to aggregation groups: Ignore aggregates non-NULL values; Propagate returns NULL if any input is NULL. For [10,NULL,20], Ignore SUM=30 and AVG=15; Propagate produces NULL.
3. **All-null result policy** applies when Ignore has no non-NULL inputs: return NULL or zero as configured. Propagate wins over an All-null Zero fallback. Empty/all-NULL data is not universally zero.
4. **Missing transaction value** handles an absent native transaction group *after* the LEFT JOIN, independently of a present row with a NULL value; choose NULL or zero.

These policies apply again at later aggregation stages. Nonnullable result definitions reject remaining NULLs. Existing unit/alias metadata stays internal: Unit/Aliases controls are hidden, new unknown units remain null, known source units may be inherited and contradictory known units still fail validation. Do not guess EA/dimensionless.

## Numeric policy

Referenced measures always use semantic `resultType.type="number"`, compiled as Float64 with policy-derived nullability. Convert integer/decimal/float source values **before** aggregation/disaggregation; splitting integer 1 over two rows can yield 0.5 each. Source schemas do not change. Backend create/update enforces this even if clients omit/override resultType. Nonnumeric sources and invalid declared decimal parameters still fail.

New calculated measures also default to Float64; legacy/new integer calculation declarations normalize to Float64. Explicit Decimal calculations retain precision/scale checks and bankers rounding. All data-type/precision/scale controls are hidden; calculated nullable-result remains configurable. Reject non-finite Float64 and invalid/overflowing results; no universal exact-total guarantee or rounding remainder redistribution is added.

Float64 is approximate (roughly 15–17 significant digits): decimal sources lose exact precision and sufficiently large integers may round. Exact-decimal requirements need an explicit policy, not an implicit change to all references.

`normalize_measure` interprets legacy referenced integer/decimal metadata as Float64 on reads/resolution/compilation without rewriting immutable records. Saving a draft persists normalization; clones/new builds use it. Fingerprints include `float64-referenced-v3-preserve-source-nulls`, with current compiler `measure-v4-aggregation-derived-round6-sqlglot-27.28.1`; old generations become Needs rebuild. Rebuild drafts through Materialise, or create a draft from a published revision. No destructive numeric migration or auto-materialisation runs at startup. Frozen previous weights are converted appropriately.

## Materialise and consistency

Materialise is its own navigation item below Measures. It has one global **Build Semantic Network** action, visually separate from the planning-level selector that automatically previews up to 100 rows. “What gets materialised at …” explains native placement, configured policies, derivation/recalculation and unavailable reasons. Model/revision controls remain on Sources.

Build current managed views and planning populations first. The network build captures semantic objects and input artifacts, pins them, compiles a dependency DAG and inserts candidate values generations into the existing values table for each level. Validate source NULLs, source/population coverage, lookups, child coverage, grain/cardinality, types and limits. Formula dependency/basis failures cannot activate stale/partial results.

One final MongoDB transaction rechecks revision/fence/input fingerprint and swaps **all** level bindings to the same generationId. Failure/cancellation/staleness preserves every prior binding. Metadata/population changes stale values. Published revisions can read but cannot rebuild; clones build their own artifacts. Publication requires managed/population readiness, not a measure generation.

Artifacts record generation, population identity, key/measure maps, compiler/numeric policy, derivations and unavailable reasons. Snapshots remain separate bounded documents. The [shared storage protocol](ENGINE_AND_STORAGE.md) preserves the strict population+values pair, including rebuilds/removing measures. Interactive edits use this same coordinated generation lifecycle, with no journal.

## APIs, modules and verification

Revision-scoped APIs:

- `POST /measures/execution/build`: revision If-Match and Idempotency-Key; HTTP 202/run.
- `GET /measures/execution/levels/{levelId}?preview=true`: state, schema, generation, row count, unavailable measures and at most 100 rows; omit preview for status only.
- `GET /measures/{measureId}/capabilities`: structural paths, mappings, via-Base availability and native/coarser edit eligibility. Weights, dependencies or data checks may still prevent execution.

`MeasureCompiler` builds SQLGlot-rendered ClickHouse CTEs from typed metadata; `MeasureBuildService` handles snapshots, pins, durable runs and coordinated activation; MeasureAPI owns HTTP. Limits: `METADATA_MEASURE_MAX_NODES=1024`, `METADATA_MEASURE_MAX_SQL_BYTES=4194304`, plus warehouse row/memory/time/thread bounds. Queries use join_use_nulls=1 and http_write_exception_in_output_format=0 to retain reliable domain errors after result headers.

Tests cover all aggregate methods/axis orders, irregular staged initialisation, time-only expansion, independence from edit weights, six-place storage/edit propagation, independent native grouping, edit Repeat roundtrips, NULL ordering, Base-native policies, independent native/coarser paths, AggregateFirst recalculation, attribute hierarchies, weighted snapshots, fractional integer-source distribution, Decimal guards, failed-generation isolation and atomic multi-level activation.

## Numeric storage and display

Store measure representations rounded to six decimal places using bankers rounding (legacy Decimal scales smaller than six remain smaller). Round at representation boundaries before downstream calculations/rollups, so coarser representations aggregate the actual six-place Base/write values. The shared materialisation fingerprint includes aggregation-derived-disaggregation-v1-round6; older networks require an explicit rebuild. Float64 remains the referenced measure type. No rounding is applied to imported raw source data.

Pivot measure cells and Float/Decimal preview cells display two decimal places; identifiers and integer source columns keep their representation. The formatter works on decimal strings to avoid losing large Decimal precision. Edit inputs use the original stored value, not the two-place display. Rounding may leave small residual differences from source totals; do not silently balance a final child or change aggregation. Build Semantic Network still replaces manual edits from sources; there is no durable edit journal.

Measure authoring uses a visible **Reference / Calculated** switch. **Output Planning Level** is highlighted: Reference inherits it from the transaction; Calculated selects it explicitly. This label represents the existing native planning level, with no semantic contract change. Editable measure appears before Missing transaction value.

At the end of the measure form, **Base Level Visibility policy** appears when Base is defined and differs from Output Planning Level. The read-only summary uses the backend's proven grain path, shows SUM split / other aggregation repeat only for expanding axes, and follows reverse aggregation order. Unsaved aggregation choices update the explanation immediately. Unreachable paths, equivalent grains, rollups and AggregateFirst recalculation are described separately. `GET /measures/execution/base-visibility?nativePlanningLevelId=...` returns the current Base, relationship and expanding axes without building data.

Calculated measures at the Base output planning level hide Dis-Aggregation policies. CalculateFirst at a different output level retains the controls; AggregateFirst has no allocation controls at any level. Reference measures retain edit disaggregation controls even at Base for coarser edits. This is a UI visibility rule; saved metadata is preserved.
