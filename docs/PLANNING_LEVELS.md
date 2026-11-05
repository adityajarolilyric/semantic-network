# Planning levels

A planning level defines its population, mandatory unique grain and optional descriptive attributes. Build one row per non-NULL grain tuple. Transaction/measure values use a separate values table; selecting an attribute never makes it a grain key. Dimensions enrich or validate existing populations rather than implicitly creating intersections.

## Population metadata

Supported methods:

- Single managed intersection: preserve its actual selected combinations.
- Cartesian factors: CROSS JOIN entire selected factor relations, such as valid Product/Location pairs × Time. Equal column names never imply equality joins; do not split a valid intersection into independent domains.
- `distinct_projection`: select parent grain keys and/or parent output attributes as new grain, then take distinct selected intersections. Ignore every unselected parent column. The captured parent table must be current/ready; build parents first—no automatic recursive build.

`grain.columns`/`grain.keyIds` retain stable identities. `definition.populationAttributes` (default `[]`) holds selected non-key outputs with stable columnId, inherited name and populationRef. Source refs identify column; Cartesian refs additionally identify factor; parent attributes use `sourceOutputColumnId`, while legacy projected keys use `sourceGrainColumnId`. A column cannot be selected as both key and ordinary attribute. Factor selectedColumnIds cover both roles. Existing keys are never automatically demoted.

Normalized output columns record ID, inherited name, type/nullability, key/attribute role, owner and complete lineage. Keys precede population/dimension attributes. Dimension outputs use deterministic `da_` identities. The first occurrence of an output name keeps its plain name; grain and population selections precede dimension enrichment. Only later collisions receive deterministic owner prefixes and, if needed, stable suffixes. All plain names are reserved before generating aliases so a generated prefix cannot capture another real column name. Store semanticName separately from the unique output alias to prevent prefix accumulation in descendants; legacy dimension-origin attributes recover their names from dimension metadata. Selected columns retain their IDs and are never silently dropped/merged; UI uses semantic labels and hides those aliases. Grain names are inherited, not user-renamable.

Every population strategy applies SELECT DISTINCT to the final selected output, after filters and dimension enrichment. Identical output rows collapse, regardless of differences in unselected source columns. For example, Product/Location × daily Time selecting Week creates one Product/Location/Week intersection. Internal lookup flags and unselected Day columns do not participate in DISTINCT. Non-NULL grain validation remains mandatory: extra carried attributes must have one combination per resulting grain or fail with conflicting-output details—no MAX/FIRST choice. Filters operate on assembled logical IDs; pushdown is allowed only when equivalent.

## Optional dimension membership and enrichment

Zero dimension bindings is valid. Bindings may map any selected population key/attribute to any compatible column of the selected dimension, not only primary/exposed columns. Composite tuples are supported. Each mapping uses exactly one left reference (`grainColumnId` or `populationColumnId`) plus `dimensionColumnId`; empty/duplicate/incompatible mappings fail.

Apply dimension filters, then reduce the relation to DISTINCT(mapping tuple, requested attributes):

- No selected attributes: membership only. Multiple raw dimension members can share the mapped tuple.
- Selected attributes: each referenced tuple must determine one distinct combination. Duplicate raw members with the same combination are valid; conflicting combinations fail `ambiguous_dimension_lookup`.
- Missing members fail `unmatched_dimension_member`. Check conflicts only for tuples referenced by the population.

LEFT JOIN enrichment preserves population and uses explicit match flags, not ClickHouse zero/empty defaults. Never use ANY JOIN, arbitrary aggregates or filters that silently prune rows. Existing `lookupCardinality="many_to_one"` describes the reduced output relation, not raw member count.

Example: PLC has Product/Location/Customer grain plus Brand, Zone and Sold To. A child selecting those three attributes has exactly their distinct combinations. An optional Brand → Product.Brand binding merely checks membership. Adding Category is valid if each Brand determines one Category; adding Product ID fails when Brand contains multiple products. Selected enrichment does not change the child's three-column grain.

## Cross-level relationship analysis

`LevelRelationshipAnalyzer` is shared by validation, measure planning, Planning View and edit eligibility. It returns equivalent/roll-up/roll-down/incompatible direction, owner-qualified mappings, time/non-time axes and actionable missing/ambiguous-path reasons. Structural path availability is separate from a measure's policy/dependency/data readiness.

Bindings are needed when the proof uses dimension attributes, such as Product ID → Brand. Explicit parent projection lineage can prove a path without bindings; no bindings does not universally prohibit traversal. Names/key counts alone prove nothing. A primary-key-covering binding can resolve that dimension's attributes; a non-key binding supplies only mapped coordinates and explicitly selected enrichment. Binding Brand cannot implicitly make Product ID available. The inverse of a validated finer→coarser mapping expands into actual finer intersections, not all dimension members.

The revision designates Base and treats it as finest among proven related levels. Reject a selected Base with an actual strictly finer related level; allow equivalent grains and independent levels. Measures may remain native on independent levels when Base is unreachable. See [measure propagation](MEASURES.md).

## Editor: Cancel, Preview, Save

The list toolbar places Base planning level on the left and Reload metadata, Build all planning levels and Create planning level on the right, aligned at the control bottoms. Narrow layouts stack them. Validate metadata remains on Sources; publication is not exposed in the single-workspace product.

Show exactly these three actions at bottom right; use the shared Source table for at most 50 preview rows without pagination/refresh controls. Grain selection is mandatory before Preview/Save. Grain and population attributes appear side by side on wide screens. For projection, Retain parent keys is left and Attributes from parent level right; hide measures, duplicate key choices and internal collision fields from attribute choices.

Dimension configuration uses Add dimension and tabs, initially empty. Preserve mappings/selections/tab order while switching; Remove binding sits bottom right inside its tab. Dimension-side selectors list only that dimension's columns. Attribute Select all/Deselect all affects only its binding.

Projection can inherit a parent binding only when its complete mapped tuple survives; remap identities explicitly, including composite/non-primary mappings. Preserve choices—including an empty selection—on surviving bindings. Remove inherited bindings when required columns disappear; preserve valid manually added attribute bindings. Merely opening an existing definition must not rewrite it.

Preview builds a **complete validated candidate generation**, then displays a bounded sample. It does not save metadata, change the object/revision versions or replace active bindings, even for a new unsaved level. Existing views/network keep using saved generations. Repeated Preview of the same current configuration can reuse its candidate. Cancel closes without committing.

Save builds if needed, then atomically commits the exact definition and compatible candidate binding. It reuses an already-built Preview without another INSERT. If metadata is unchanged and the saved table is current, Save/Preview reuse it with no version bump/build. An unchanged definition with missing/stale data can build/activate replacement without changing its object version. Dependencies still must be current. Published levels can preview their saved table but cannot build changes/save.

Build failure preserves saved metadata and active binding. Commit rechecks original object/revision versions, definition hash, dependency fingerprint and worker fence; stale commits conflict, leaving an unbound candidate. Completed commit replay is idempotent. Abandoned previews follow the 24-hour retirement/pin rules inside the same physical table.

## APIs and readiness

On the Planning Levels list, **Reload metadata** fetches saved model/revision/object definitions and managed readiness from the API; it performs no ClickHouse build. **Build all planning levels**, beside it, rebuilds all saved populations in the current draft, including already-built levels. It loads current saved definitions, validates parent dependencies, then submits existing saved-level build APIs sequentially with each object's expected version, parents before projections. Unsaved editor inputs are excluded. Progress identifies the current level; the first failure stops further submissions and reports completed count. Previously successful builds remain committed, and the UI reloads metadata after success or partial failure. Published/empty/loading/busy states disable this action. Measure values are not rebuilt; use Materialise afterward.

Bulk orchestration is in `application/src/metadata/planningLevelBuilds.ts`; no new backend endpoint is needed. Each submitted build is durable, but the list page must stay open to submit remaining levels; this is not a durable batch job or an all-or-nothing population transaction. Existing per-level validation, fences, stable table reuse and previous-generation protection still apply.

All paths are relative to `/api/v1/models/{model}/revisions/{revision}`:

- `POST /planning-levels/editor/draft/build`: proposed definition, nullable expectedVersion, Idempotency-Key; enqueue staged build.
- `GET /planning-levels/editor/draft/{runId}/preview`: schema and at most 50 built candidate rows under a short read lease.
- `POST /planning-levels/editor/draft/commit`: exact definition + runId; commit metadata/binding atomically.
- Compatibility: `POST /planning-levels/{id}/build` builds saved metadata with If-Match/Idempotency-Key; `GET /planning-levels/{id}/table?preview=true` returns status/schema and optional 50-row bound preview; `POST /planning-levels/preview/population` gives a proposed bounded query without full candidate activation. The current editor uses the staged flow above.
- Generic engine run polling/cancellation handles planning and planning-preview targets.

Status is Not built, Building, Ready, Needs rebuild or Failed. Metadata/dependency changes and parent rebuilds stale dependent drafts. A failed rebuild may preserve an older snapshot, clearly marked with its stale state/error. Contract-2 publication requires every declared planning level to have a current owned/readable population; measure values are not required. Contract-1 metadata-only publication remains compatible. Clones copy definitions/IDs without bindings; initialize managed views, then build parents before children. Published population snapshots cannot use view-style repair against changed sources; restore the original snapshot or create a draft.

## Execution, bounds and maintenance

`PlanningLevelPlanner` resolves typed scans/projections/CrossJoin/DistinctProject/LookupJoin/filters; `PlanningLevelCompiler` generates SQLGlot/ClickHouse queries and lookup assertions. `PlanningLevelExecutor` derives actual schema, checks population estimates, inserts isolated candidate rows and validates complete grain/null/lookup contracts. `PlanningBuildService` captures dependencies/pins, submits durable runs and activates bindings. `PlanningLevelAPI` owns the HTTP context. No SQL belongs in React or service handlers.

Factor counts remain diagnostic. The population estimate counts the final distinct public projection after filters/enrichment, capped at the configured row limit plus one; repeated daily rows do not inflate the weekly estimate. Defaults: `METADATA_PLANNING_MAX_ROWS=1000000`, `METADATA_PLANNING_MAX_BYTES=536870912` (query memory and whole-table storage), plus ClickHouse timeout. A bounded sentinel detects unexpected growth; never activate truncated output. The row limit applies to distinct output rows; execution time and memory limits still bound large intermediate Cartesian products. No additional range/completeness checks are implied.

Planning previews use resourceKind `planningLevelPreview`, while their artifacts belong to the planningLevel resource. Lease recovery gets a fresh row token in that same table. Activation checks target/revision versions and captured artifact identities; unrelated revision edits can conservatively supersede a build. Deletion releases bindings for later collection. See [shared activation and strict storage](ENGINE_AND_STORAGE.md).

Regression coverage includes parent-first bulk ordering, waiting for parent activation, dependency-cycle/missing-parent rejection, partial failure refresh, read-only/empty/busy actions, optional/composite/attribute bindings, membership versus functional enrichment, Cartesian name collisions, projection grain/ambiguous attributes, NULL/duplicate keys, full validation with 50-row samples, build limits, staged Preview without Save, reuse, conflicts, clone/publication and previous-generation preservation.

Final-output filters use the shared typed engine module and nested AND/OR editor. See [Filters](FILTERS.md) for stage placement, scalar operators, NULL semantics, schema APIs and compatibility.

## Replacing a single-source population

For an existing source-populated level, choosing another Population table opens explicit mappings for existing grain and population-attribute fields. Map each field to a compatible new source column, including reordered/differently named columns. Preserve logical grain/attribute IDs, Transaction references, filters, projection references and Dimension bindings; inherit current source names for display. Preview/Save use the existing candidate/build/commit checks and reject incompatible types or invalid combinations. New source grain selections also preserve existing IDs when the same selected field survives. No automatic union, inference from equal names or remapping of Transaction input columns occurs.

Transaction-first onboarding creates exactly this ordinary source population through [the creation shortcut](TRANSACTIONS.md#start-from-a-table-without-an-existing-planning-level). It can be expanded or replaced manually when later Transactions need more intersections.

### Authoring layout

Population method uses a three-option switch: **From a Table**, **From Multiple Tables**, **From Existing Planning Level**. Parent key retention, promotion of parent attributes to grain keys, and retained parent attributes appear in three adjacent panels (stacked on narrow screens). Dimension mapping and selected attributes appear alongside each other. These labels/layouts do not change population semantics or logical column IDs. Transformation panels start collapsed, including configured filters.

Transaction-first populations can have a captured input pipeline (Pivot → daily-calendar resolution) before DISTINCT selection. Rebuilds use the same shared engine operators and validations as the Transaction, capture the calendar Dimension/source dependencies, and retain the strict population/value table pair. Reopening the level shows transformed fields for grain/attribute selection. The stored namespace preserves generated column IDs without referencing the Transaction or forming a dependency cycle.
