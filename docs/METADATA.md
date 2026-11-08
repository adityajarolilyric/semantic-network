# Metadata model

Population authoring now lives inside **Transactions**, while **Dimensions** manages individual member catalogs. A reusable population still persists as an ordinary `dimension` aggregate (population strategy, grain, attributes and optional member bindings); consuming Transactions reference its `dimensionId`. The revision's `baseDimensionId` selects Base once. UI labels do not introduce duplicate population objects, per-Transaction Base flags or a migration. The atomic authoring command and reuse rules are described in [Transactions](TRANSACTIONS.md). Existing direct CRUD and standalone Transactions remain compatible.

MongoDB is authoritative for semantic meaning, grain, relationships, formulas, policies and evidence. Keep bounded column lists inside their owning object; keep growing object sets, revisions and execution history in separate documents. Metadata CRUD does not alter source tables. Execution and CSV ingestion have explicit, separate APIs.

## Collections and identity

- `semantic_models`: identity, names/description, one active draft pointer, published pointer, revision counter, optimistic version and timestamps. Published history may contain multiple revisions.
- `semantic_revisions`: model/revision IDs, number, parent, contract version, rationale, draft/published state, optimistic version and `baseDimensionId`.
- `semantic_objects`: one typed `source`, `dimension`, `transaction` or `measure` per `(modelId, revisionId, objectId)`, regardless of kind. IDs remain stable across revisions.
- `semantic_evidence`: referenced definitions, rule, observed result/time, source fingerprint, scope and bounded diagnostics. Evidence can change after publication without changing a definition. Supplied evidence is not independently verified by CRUD; a declared key is not proof of live uniqueness.
- `semantic_examples`: questions, referenced objects, small fixtures, expected grain/rows and rationale. Examples freeze on publication and copy into child drafts; evidence does not copy as newly verified evidence.
- `semantic_contracts`: versioned documentation/schema vocabulary for agents. Records cannot enable operators, replace validators or change execution.
- `semantic_managed_tables`: revision/source-scoped managed definitions, described in [Sources and Managed Tables](SOURCES_AND_MANAGED_TABLES.md).

Objects carry `modelId`, `revisionId`, `objectId`, `kind`, `schemaVersion`, name/description, a typed `definition`, and server-managed `version`/timestamps. MongoDB `_id` is not a semantic reference. Request bodies omit persistence IDs, URL-supplied scope and server-managed fields. Connections, credentials, database names, `physicalBinding`, tenant IDs and scenario overrides do not belong in these definitions.

## Stable column identity across renames

A column is identified by its owner plus `columnId`, not by its name. Source-local IDs such as `col_1` may occur in many sources. A source column's `name` records its physical name; a dimension column's `displayName` supplies the UI label. For example:

```json
{
  "sourceColumn": {"columnId": "col_1", "name": "product_id", "physicalType": "String", "nullable": false},
  "dimensionColumn": {"columnId": "product", "sourceColumnId": "col_1", "name": "product_id", "displayName": "Product ID"}
}
```

A future supported `product_id` → `sku_id` rename preserves `col_1`, so downstream references survive. Registration initially assigns source-local IDs from discovered order; refresh/reconciliation must preserve existing IDs when columns move, assign new IDs only to new logical columns and never recycle dropped IDs. Cloning preserves identities too.

Stable IDs do not detect external renames or schema drift. Registered source names/table names/column definitions are currently immutable. A future explicit reconciliation workflow must preserve identity, validate dependencies and respect published revisions; historical metadata alone cannot restore an old warehouse schema. Normal UI hides IDs and generated physical names, while diagnostic request IDs remain visible.

## Sources and dimensions

Sources capture original names/types/nullability, optional declared keys, scope and description; no source/root column `displayName` is accepted. Descriptions and supported key/scope metadata can change if dependencies remain valid; contract-2 availability changes use the managed lifecycle.

A dimension references a managed source and declares semantic columns, nonnullable composite keys, exposed attributes, optional member-label column and filters. `labelColumnId` identifies member labels such as a product name; it is not the heading label for Product ID.

The editor uses one column grid: read-only source column, editable display name, Key checkbox and Exposed checkbox. New dimensions expose all columns; reopening preserves deselections. Keys are always exposed and cannot be deselected. At least one key is required. Dimension name is sufficient: the compatibility root `displayName` follows `name`, with no separate UI control. Member label and description are under More settings.

Time identity is the root flag `isTimeDim`. At most one Time dimension exists per revision, and `memberCatalog.time` exists exactly when that flag is true for a calendar-backed Dimension. It records base grain (day/week/month), date column and ordering column. Explicit calendar attributes support period mappings; range resolution requires a daily calendar. Advanced effective-dated hierarchies and timezone interpretation need separate contracts.

## Base role and canonical example

The revision alone stores `baseDimensionId`; do not persist `isBase` flags on levels. This avoids conflicting selections and allows another revision to choose a different Base. Derive `isBase` for presentation. Drafts can temporarily omit Base; publication requires an existing level in the same revision. A proven strictly finer related level cannot sit above the selected Base; equivalent and independent levels are permitted.

[The authoring fixture](../backend/examples/plt_pt.json) has six sources (Product, Location, Time, SourcingMatrix, Forecast, Sales), five Dimensions (Product, Location, Time, PLT and PT), two transactions and FQTY/SQTY/Error measures:

- Base PLT = valid Product/Location pairs from SourcingMatrix × Time dates. Preserve the matrix's valid pairs; do not cross entire Product and Location dimensions independently.
- PT = distinct Product/Date projection from PLT, with explicit lineage. Population projection alone does not aggregate facts.
- Forecast/FQTY is native to PLT; Sales/SQTY is native to PT. Error is native to PT, formula `ABS(FQTY-SQTY)`, AggregateFirst.
- Fixture policies are explicit assumptions, not defaults for all models: daily Time, non-NULL source values, compatible units, missing transactions as NULL, read-only measures and configured SUM aggregation. Repeat disaggregation can change a subsequent SUM, including the native representation.

The fixture contains API authoring commands, not a MongoDB dump: member commands are absorbed into Dimension aggregates.

Sales and forecast must also describe compatible business populations; matching key shapes alone cannot establish that scope. Detailed population and measure rules are in [Dimensions](DIMENSIONS.md) and [Measures](MEASURES.md).

## Revision lifecycle and CRUD

The current product exposes only one default draft. Model/revision creation, deletion and publication endpoints are removed; scoped metadata, services and historical immutability remain for future support. The retained lifecycle contract is: published definitions are immutable; a child draft can be created internally by copying objects/examples and managed definitions, preserving IDs and resetting document versions. Do not share physical artifact bindings or manufacture fresh evidence. Full-copy revisions favor straightforward retrieval/history; consider structural sharing only if measured costs justify it.

Every draft-definition mutation writes the revision guard inside a MongoDB transaction. Publication validates the graph and updates revision state/model pointers in that transaction, preventing competing edits from publishing a mixed snapshot. Resource version, revision version, revision number and schema/contract version are distinct.

MongoDB must be a replica set or sharded deployment; standalone mode is rejected. Persistence uses snapshot transactions and majority writes. Deletes are explicit, dependency-aware and non-cascading. Public model/revision deletion is disabled; the retained internal lifecycle can remove empty drafts and models without revisions. Change/clear a draft's Base selection before deleting its selected level.

Metadata APIs use `/api/v1`; generated OpenAPI describes payloads. PUT replaces editable fields. Read/create/update return version and ETag; PUT, DELETE and publish require `If-Match`: missing → 428, malformed → 400, stale → 409. Revision creation and publication also require the expected model version because pointers change. Lists use bounded stable-ID cursors; clients must exhaust pages.

Validation layers are typed Pydantic schemas rejecting unknown fields, MongoDB structural validators/unique indexes, revision graph checks, then operation-specific warehouse checks during execution. CRUD does not prove live uniqueness, coverage or absence of drift. Defaults: 1 MiB metadata requests, 100 items per list page, 5,000 objects per revision and 5,000 records per evidence/example category.

Adding executable kinds/AST operations requires schemas, service/API wiring, graph and storage validation, tests and docs. An entry in `semantic_contracts` is insufficient. Database clients are opened through application lifecycle/injection, never at module import.

## Shared UI and field help

`POST /api/v1/workspace/bootstrap` transactionally creates `default_model` (Planning Model) and draft `revision_1` on an empty installation. It is explicit/authenticated, idempotent and retries competing first inserts; existing user models remain. New installations/UI drafts use contract 2.

There are no model/revision selectors or creation/publication controls. The application automatically uses the default workspace; Base selection remains on Dimensions. Validate metadata stays on Sources. GET/list and scoped update endpoints remain for inspection, Base selection and future compatibility; additional model/revision POSTs, lifecycle DELETEs and revision publish are absent from production OpenAPI. URL `model`, `revision`, `tab` parameters preserve navigation. The browser holds fetched snapshots, unsaved inputs, layout/loading/error state only—no semantic definitions/source rows in localStorage, sessionStorage or IndexedDB. Obsolete requests are aborted/ignored; success appears only after server confirmation.

Published editors are read-only. Failed/stale writes preserve the form and diagnostics. Reload/reopen/reapply after conflicts; do not silently retry a replacement against a newer version. Deletes/publication require their explicit UI confirmation. Save/build workflows vary by resource and are documented with that resource.

Contextual help is UI-owned: `application/src/metadata/fieldHelp.ts` supplies short explanations/examples; `components/FieldHelp.tsx` supplies the accessible popup. Add help to non-obvious policies/mappings, not Name/Description or every checkbox. Shared Field/Check/Checks controls and grid headings avoid repeated icons. Hover/focus/click opens; Escape/blur/outside click closes; Enter/Space works even in read-only fieldsets without submitting forms or toggling adjacent controls. Portal placement follows scrolling/resizing and stays inside the viewport. Keep wording aligned with current execution rules; no backend call, metadata field or persisted tooltip state is needed.

Transaction `definition.calendarResolution` records inclusive Start/End and selected daily Time attributes independently of native mappings. Derived fields belong to the transaction effective schema; raw/managed sources remain unchanged. See [Transactions](TRANSACTIONS.md#date-ranges-and-native-periods) for IDs, execution, compatibility and preview contracts.

Dimension and Transaction definitions may contain an optional typed `pivot` stage, owned independently of their `sourceId` Managed Table. Persist group/record keys, pair identities and selected literals/output aliases; generated column IDs are verified by the engine. Transaction stages execute Pivot → Calendar → Multi-grain. New multi-grain authoring uses Column equals attribute only; unchanged saved Level/Value rules retain backward compatibility. See [input pivots](SOURCES_AND_MANAGED_TABLES.md#independent-dimension-and-transaction-pivots).

Final-output filters use the shared typed engine module and nested AND/OR editor. See [Filters](FILTERS.md) for stage placement, scalar operators, NULL semantics, schema APIs and compatibility.

Transaction-first onboarding creates only existing dimension and transaction definitions. The request's grain-column list is a creation command, not another saved mapping mode or metadata collection. See [Transaction-first authoring](TRANSACTIONS.md#start-from-a-table-without-an-existing-dimension).

Source-populated Dimensions may carry an optional typed `population.inputTransform` (`objectId` namespace, Pivot, Calendar resolution). Transaction-first onboarding copies its input stages there so DISTINCT grain population rebuilds reproduce the transformed schema without depending on the Transaction. The namespace is an identity seed, not a semantic reference. The normal Transaction persists the same Pivot/Calendar stages; future edits are independent. Absent inputTransform is omitted on serialization so existing metadata hashes remain compatible. Calendar Dimension/source versions join the population capture and readiness checks.

Incremental materialisation adds engine-owned manifests to `engine_artifacts` and captured jobs: policy/version, evaluation fingerprint per measure/target and layout hash. These derive from semantic definitions and supported source/population artifact versions; no new user-authored semantic metadata is required. Incremental jobs pin current value snapshots and retain their generation on no-op; compatible historical captures can establish a baseline, while missing proof requires explicit Full Refresh.
