# Measures and semantic-network materialisation

Each measure has one native planning level. Referenced measures read a Transaction value; calculated measures use a typed formula. Precompute compatible representations at Base and other defined levels when paths exist. A measure on an independent level may remain native and roll directly to supported coarser levels; **Base connectivity is not a definition requirement**.

## Policies are authoritative

Never infer SUM, AVG, equal allocation, deduplication, conservation or a fallback from names/business meaning. Use the saved NonTime aggregation, Time aggregation, axis order, disaggregation and NULL policies at each stage. Lookup availability and edit permission are distinct: a finer repeated value can be readable yet not editable.

`allocation.mode` retains its API name; the UI calls it Dis-Aggregation policies:

- None/null/omitted = **Repeat** to each eligible finer row.
- Equal divides by eligible row count.
- Weighted uses a configured basis measure's captured compatible representation.
- Weighted by self uses an immutable previous representation, not an AST dependency on itself or partially updated values. No basisMeasureId is used.

Repeat 100 across three Base rows followed by AVG gives 100; SUM gives 300. Both are valid configured results, including when returning to native. Do not count the native value only once, substitute AVG, redistribute a remainder or impose sum conservation. The SUM used to normalize weights is a denominator, not a hardcoded measure aggregate.

Disaggregation policies are allowed on Base-native measures too: dormant on initial native load, needed for coarser edits. Initial self-weighted expansion requires compatible previous values; missing weights/zero denominator fails without Equal/AVG fallback. The populated-only self-weight rule for **edits** is documented in [Planning View](PLANNING_VIEW.md#edit-distribution-and-recomputation).

## Native loading and propagation

1. Independently aggregate each transaction by all mapped native keys using the measure's NonTime policy. Date-range contributions to a coarser native period use the explicit Time-fold rules in [Transactions](TRANSACTIONS.md#date-ranges-and-native-periods).
2. LEFT JOIN each aggregated input to the native population. Never join raw transaction details/multiple raw inputs first: that multiplies other measures. Check unmatched transaction keys separately; preserve missing native groups according to missingTransactionValue.
3. Keep this as a private native seed. If native→Base is proven, propagate by configured disaggregation, then derive compatible defined-level representations from Base—including the native representation. Do not silently substitute its private seed for this roundtrip result.
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
- **CalculateFirst:** evaluate at native grain, then propagate using its own result aggregation and optional allocation (omitted/null = Repeat). During edits, recalculate native results before supported rollups.

Example: daily FQTY=100,80 and SQTY=90,100 give Error=10,20. At the combined grain, AggregateFirst `ABS(FQTY-SQTY)` gives `ABS(180-190)=10`; CalculateFirst with SUM gives 30. Null operands propagate unless an explicit formula such as COALESCE changes that behavior. AggregateFirst omits output aggregation rules; CalculateFirst requires them.

## NULL policies

NULL means missing/unknown, not zero. Apply policies in this order:

1. **Source null policy** applies to an existing transaction value before native aggregation. Reject (default) fails on NULL; Preserve passes it through. Nullable schema alone does not prove a NULL exists.
2. **Null input policy** applies to aggregation groups: Ignore aggregates non-NULL values; Propagate returns NULL if any input is NULL. For [10,NULL,20], Ignore SUM=30 and AVG=15; Propagate produces NULL.
3. **All-null result policy** applies when Ignore has no non-NULL inputs: return NULL or zero as configured. Propagate wins over an All-null Zero fallback. Empty/all-NULL data is not universally zero.
4. **Missing transaction value** handles an absent native transaction group *after* the LEFT JOIN, independently of a present row with a NULL value; choose NULL or zero.

These policies apply again at later aggregation stages. Nonnullable result definitions reject remaining NULLs. Existing unit/alias metadata stays internal: Unit/Aliases controls are hidden, new unknown units remain null, known source units may be inherited and contradictory known units still fail validation. Do not guess EA/dimensionless.

## Numeric policy

Referenced measures always use semantic `resultType.type="number"`, compiled as Float64 with policy-derived nullability. Convert integer/decimal/float source values **before** aggregation/disaggregation; splitting integer 1 over two rows can yield 0.5 each. Source schemas do not change. Backend create/update enforces this even if clients omit/override resultType. Nonnumeric sources and invalid declared decimal parameters still fail.

New calculated measures also default to Float64; legacy/new integer calculation declarations normalize to Float64. Explicit Decimal calculations retain precision/scale checks and bankers rounding. All data-type/precision/scale controls are hidden; calculated nullable-result remains configurable. Reject non-finite Float64 and invalid/overflowing results; no universal exact-total guarantee or rounding remainder redistribution is added.

Float64 is approximate (roughly 15–17 significant digits): decimal sources lose exact precision and sufficiently large integers may round. Exact-decimal requirements need an explicit policy, not an implicit change to all references.

`normalize_measure` interprets legacy referenced integer/decimal metadata as Float64 on reads/resolution/compilation without rewriting immutable records. Saving a draft persists normalization; clones/new builds use it. Fingerprints include `float64-referenced-v2`, with compiler `measure-v2-float64-sqlglot-27.28.1`; old generations become Needs rebuild. Rebuild drafts through Materialise, or create a draft from a published revision. No destructive numeric migration or auto-materialisation runs at startup. Frozen previous weights are converted appropriately.

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

Tests cover all aggregate methods/axis orders, independent native grouping, Repeat roundtrips, NULL ordering, Base-native policies, independent native/coarser paths, AggregateFirst recalculation, attribute hierarchies, weighted snapshots, fractional integer-source distribution, Decimal guards, failed-generation isolation and atomic multi-level activation.
