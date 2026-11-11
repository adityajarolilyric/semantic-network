# Saved Dimension and Transaction views

Each saved Dimension and Transaction owns an independent ClickHouse view. Two objects using the same uploaded table have separate views and may use different transformations. Views store SQL, not copies of rows. Identity managed inputs are metadata references to the physical source; a redundant source wrapper view is not created. Legacy identity wrappers are consolidated into source references during object-view publication and removed only after its metadata commit. Legacy shared managed transformations remain supported.

## Save and Preview

- **Raw Data Preview** reads the imported physical table before all transformations.
- **Save** validates the proposed object and its generated query, creates a candidate view, and publishes the view and metadata through one guarded save operation. Referenced measures created with a Transaction are committed with that Transaction.
- **Preview** reads the saved object view, up to 50 records with a live total count. It does not compile the current form or change a view. Unsaved/new forms ask permission to Save first; declining or a failed Save prevents Preview. Saving for Preview keeps the editor open.
- **View SQL** in the editor shows the saved preview SELECT. Runtime artifacts retain `buildSql` for the full view definition.

A Dimension applies its configured population, enrichment, Pivot/calendar input stages and final Filters. A Transaction applies **Pivot → Date Resolution → native/multi-grain mapping → Filters**. Disabled stages are omitted. Existing duplicate, grain, calendar, ordering and null contracts remain enforced; views retain the compiled checks and execute them when validated/read by the engine. No measure aggregation policy is replaced by an assumed SUM.

Dimension lookups/populations and measure compilation capture these saved views. Already-applied transformations are not applied again. Transaction views preserve internal calendar/assignment columns needed by later measure aggregation; previews expose the public column list. Object views and materialised semantic outputs are different contracts: the latter may aggregate rows and add calculated measures. A separate semantic output is retained where required. The earlier one-view simplification eliminated an identity wrapper, not the distinction between transformed records and aggregated measure values.

## Publication and freshness

`engine/object_views` owns planning, dependency tokens, capture and orchestration. `warehouse/object_views.py` owns ClickHouse DDL/reads. Existing shared clients, repositories, query budgets and engine operators are reused. No service creates a database client or buffers transaction populations in Python.

Saves journal a durable `objectView` run and `storageMode=object_view` artifact. A candidate is checked before an Atomic-database `EXCHANGE TABLES` puts it under the existing stable view name. The object, related measures, active view binding and successful run commit in the same MongoDB transaction. Failed publication reverses the exchange. Original and candidate UUIDs plus ownership comments/aliases protect rollback and cleanup. Only owned temporary views are removed; source tables remain intact. Interrupted saves are recovered under existing artifact recovery leases and must be retried rather than silently committed later.

A revision fence prevents committing a query compiled against changed metadata. API saves and network builds exclude one another; read leases drain before view replacement. Candidate metadata is never made visible merely to generate its SQL. Failed saves leave the prior object definition and view active. Cleanup after successful activation is retryable and cannot undo a committed save.

View bindings carry the authored-definition hash, relevant Dimension versions and managed-input artifact tokens. Dependency changes make affected views stale. Save or Build Semantic Network refreshes those views in dependency order before populations and measures are built. This also upgrades existing metadata without a separate migration. Source data itself stays live through ordinary views; materialised measure outputs still require their normal refresh. Numeric source-backed edits keep the Transaction's Float64 value projections valid when the physical source column widens.

The UI's normal preview endpoints are `GET /dimensions/{id}/preview`, `GET /dimension-members/{catalogId}/preview`, and `GET /transactions/{id}/preview`, under the existing model/revision scope. They support `sql_only=true`. Retained POST draft/stage diagnostic endpoints support existing integrations and internal compilation; the normal Preview buttons no longer call them. Schema discovery remains available while editing, without publishing a draft view.
