# User flow

Current simplified flow: **Sources → Dimensions → Transactions → Measures → Decision View**. A Transaction can create its Dimension from selected keys, so defining Dimensions separately is optional. Select one Base Dimension. Source-backed Dimension Save stores metadata; derived Dimension Save also builds its table and any required ancestors. Preview up to 50 rows with a total count. Build Semantic prepares Dimension populations and then LEFT JOINs policy-aggregated measures. Context Graph explains which selections can be plotted together. See [Dimensions](DIMENSIONS.md) for the current lifecycle; older dated notes below describe prior editor details.


Turn source tables into a reusable planning network, then explore and edit measures at supported grains. This walkthrough follows the supplied demand-planning demo and the current repository contracts. It uses the current navigation and includes recovery paths that the demo did not cover.

Read [Product requirements](PRD.md) for the complete rule catalogue, rationale, acceptance criteria and engineering constraints. Rule identifiers in this walkthrough refer to that catalogue. The demo's counts—nine source products, eight active products, 576 intersections and 154 promotion combinations—describe its fixture; they are not universal expected results.

Calculated measures can return Number, String, Date or Boolean. First define referenced measures for Transaction inputs, including dates/statuses/flags; select Dimension/Dimension attributes directly from formula suggestions. The engine infers types and restricts aggregations. Nonnumeric outputs are read-only, are not distributed to finer/Base grains, and distinguish conflicting **Mixed** values from missing data. See [typed measures](MEASURES.md#typed-measures-and-direct-attribute-formulas).

## 1. Understand the journey

A semantic modeller can start from a Transaction table or prepare explicit Dimensions and Dimensions before defining Transactions and Measures. An operator or modeller builds the network in Decision View. A planner consumes it in Decision View and edits eligible measures. These are user responsibilities, not implemented permission roles.

The normal navigation is **Sources → Dimensions → Transactions → Measures → Decision View**. Managed inputs remain part of the architecture, but Managed Tables is no longer a left-navigation item.

Three different activities must remain distinct:

- **Define:** save meaning, mappings, formulas and policies in metadata.
- **Build:** generate validated planning populations or measure representations from saved intent.
- **Consume:** query server-calculated values and submit eligible edits.

Saving a Dimension, Transaction or Measure commits metadata only. Dimension and Transaction previews query the configured populations without creating physical tables. Build Semantic prepares populations and then materialises measures. See P01–P08 and E01–E07.

## 2. Start the workspace and choose context

**Entry:** the user has the repository or its supplied archive and Docker with the supported Compose version installed.

1. Follow [full-stack startup](OPERATIONS.md#full-stack-startup) from the repository root.
2. Open the application at localhost port 5174 and check warehouse connection status.
3. On a fresh installation, use the default Planning Model and its first draft. There is no published revision initially.
4. Continue directly to Sources. One default Semantic Model and Revision are kept in MongoDB; selection, creation and publication are not exposed. All pages use this context.

**Checkpoint:** the default editable context is ready. Base has not yet been selected.

**Why:** default scope removes setup steps while retaining model/revision IDs for future lifecycle support. Publication is disabled because it would freeze the only editable revision. Deployment promotion remains a separate future workflow. See P01–P04.

## 3. Load and register source tables

The demo uses Product, long-format Location, a daily Time calendar, valid Product/Location intersections, long-format Forecast, Brand/Zone Sales History, and mixed-grain Promotions with start/end dates.

Check the intended role of each input before configuring it; actual column labels can differ:

- **Product:** Product ID, descriptive Product/Brand/Category attributes and an active flag.
- **Location:** Location ID plus separate attribute/value rows for City, Store Format and Zone.
- **Time:** Date, Week ID, Month, Week Start/End and an ordering field. Include any other fields required to make period identity unambiguous.
- **Product/Location intersection:** permitted Product ID/Location ID pairs; this is business validity, not observed demand.
- **Forecast:** Product and Location attribute/value pairs on the same record, plus Week ID, Month and Forecast Quantity.
- **Sales History:** uniformly coarse Brand, Zone, Week ID, Month and Sales Quantity.
- **Promotions:** promotion identity/context, Product and Location selectors at mixed grains, Start/End dates, promotion type and Discount Percentage.

Uploads now create editable defaults: tables with any numeric column become Transactions with non-numeric grain keys and numeric measures (Sum, non-editable); tables without numeric columns become Dimensions keyed by all columns. Review these defaults against business meaning. An observed fact table is not automatically the complete shared population.

1. Select up to ten CSV files and review their proposed table names.
2. Upload. Files are processed one at a time; each successful file creates a new ClickHouse table.
3. Inspect raw data and inferred types. Preserve identifiers such as leading-zero product codes.
4. Uploaded tables are automatically registered and scoped, and their managed views are built. Review the generated Dimension or Transaction and measures. Pre-existing warehouse tables can still be scoped manually.
5. Resolve setup warnings, if any. Numeric-only tables require manual grain selection. Retry incomplete setup with the original upload request; it reuses the stored CSV. Build Semantic when ready to materialise measure values.

**Checkpoint:** required tables are available in the current revision, with the intended original column names and types. Preview shows at most 50 records and the complete count, such as 50/159.

**Recovery:** correct malformed headers/records and retry failed files. Successful imports are not uploaded again. A table-name collision does not overwrite the existing table. Keep the upload page open until the browser-owned queue finishes. If import succeeds but refreshing the catalog fails, refresh the display rather than assuming the import failed.

**Why:** a physical source and its semantic availability have different lifecycles. Raw data stays unchanged; downstream objects use managed outputs consistently. The 5,000-row import limit applies per file, not to every pre-existing warehouse table or planning result. See S01–S06.

## 3a. Alternative: start from a single table

When the user has Forecast with Product ID, Location ID, Time ID and Quantity but no Dimensions or population, go directly to Transactions after source registration.

The Transaction editor groups optional steps in a collapsed **Transformations** panel with **Filters**, **Pivot Data** and **Date Resolution** tabs. **Grain Selection** stays visible because every Transaction needs a grain; visual tab order does not change engine execution order.

1. Create Forecast and select its Managed Table. For every new Transaction, the mapping defaults to Create Dimension. Configure Pivot and Date resolution if needed; then choose grain keys from the transformed columns.
2. Select Product ID, Location ID and Time ID as grain columns; review the suggested level name.
3. Preview the proposed distinct planning population. No metadata is saved by this preview.
4. Save metadata. The system creates a normal source-populated level and directly mapped Transaction, without building physical tables. Build Semantic prepares it later. The first transaction-created Dimension becomes Base when unset and no independent compound Dimension exists.
5. Optionally select Quantity in the Transaction’s Measures grid before saving, choosing Editable, both aggregation policies and Dis-Aggregation. Otherwise define the referenced measure later in Measures. Build the Semantic Network and plot dimension columns in Decision View. Dimensions are optional; none are generated.
6. When Sales arrives, choose Use Existing Dimension for that existing level and map every key. The user owns any missing-intersection correction; registration does not union the inputs automatically.
7. To replace the population with an authoritative intersection input later, edit Dimensions, choose the new source and explicitly map the existing fields. Stable key identities preserve Transaction mappings, even when source column order differs.

**Checkpoint:** repeated Forecast facts remain available for aggregation, while its observed key combinations appear once in the population. This population does not establish every possible valid business intersection. If a build fails after creation, the saved objects remain; correct/build them rather than recreating them. Time-like column names do not infer a Time Dimension.

This shortcut uses saved Managed Table columns and no new persistent metadata shape. For Transaction-owned Pivot/date-resolution transformations, use the existing target-level workflow. See X10 and V01 in the [PRD](PRD.md).

## 4. Define Product and filter active members

1. Create a Dimension named Product and select its managed input.
2. Use Raw Data Preview to inspect the imported nine-product example.
3. Mark product_id as the dimension key. All attributes start exposed; keys remain exposed.
4. In Filters, choose Add filter and define is_active equals True.
5. Preview the transformed Dimension. The supplied example now has eight members.
6. Deselect exposure of is_active if consumers should not see that attribute. Keep the filter.
7. Adjust column display names, such as Product ID, without changing source column names. Save metadata.

**Checkpoint:** the active-member restriction is saved, and filtering still works when its supporting non-key attribute is not exposed.

**Why:** filter evaluation and attribute exposure are separate concerns. A user should be able to use a technical validity flag without making it a business-facing axis. Metadata Save records a key declaration; it does not by itself prove live data uniqueness. See D01–D04 and F01–F04.

**Important branch:** filtering Product does not automatically delete invalid Product/Location source intersections. If a population later references a removed product, dimension membership validation can fail. Correct or explicitly filter the population using available fields; do not expect an implicit inner join to hide those intersections.

## 5. Define Location from separate attribute rows

The long Location input has location_id plus attribute/value records for City, Store Format and Zone.

1. Create Location and preview raw data.
2. Enable its independent Pivot transformation. Retain location_id.
3. Select the attribute column and value column, then Discover the available outputs. Select the required columns.
4. Because several records describe one location, select location_id under Record keys for separate attribute rows.
5. Preview the pivot, then mark location_id as the Dimension key, choose exposed columns and save.

**Checkpoint:** each logical location has a usable wide representation. The raw input and other objects using it remain unchanged.

**Why:** record keys tell the engine which separate records belong together. Without them, the engine does not guess how to merge entity attributes. Multiple records for one selected pivot cell fail even if their values are identical; the modeller fixes the input rather than accepting an arbitrary SUM/MAX. See T01–T04.

The Dimension key, pivot record keys and optional managed-output key are different declarations. Selecting a Dimension key does not retroactively define pivot grouping.

## 6. Define the Time calendar

1. Create Time from the calendar input and select its nonnullable member key, such as Date.
2. Mark it as the Time Dimension.
3. Set the calendar's actual base grain: Day in this example.
4. Identify the Date column and Ordering column explicitly.
5. Expose the calendar period attributes needed later: Week ID, Month, Week Start and Week End; include Year or other fields when they are needed to identify periods correctly.
6. Preview and save metadata.

**Checkpoint:** the system can resolve dates and order Time operations using the declared calendar.

**Why:** calendar grain and a measure's planning grain are different. A daily calendar can support weekly planning. Calendar values establish fiscal period membership; the engine must not infer weeks from names. Quarter/year analysis requires suitable calendar attributes and paths; the flag does not invent them. Only one Time Dimension is allowed in a revision. See D05–D06.

## 7. Build the Product Location Week planning population

1. Create a Dimension, named Product Location Week for this walkthrough.
2. Choose Cartesian population and add the valid Product/Location intersection input and Time input as factors.
3. Select Product ID and Location ID as grain columns from the intersection. Select Week ID from Time.
4. Inspect period identity. In the demo calendar, Week ID alone does not identify the intended period: promote Month into the grain too. Add Year or a globally unique period key instead if that is what the actual calendar requires.
5. Select Week Start/Week End and other useful non-key population attributes.
6. Add Product and Location bindings through the dimension tabs. Map Product ID and Location ID to compatible Dimension attributes. Select the descriptive attributes needed for analysis, such as Brand, Category, City and Zone.
7. Add a Time binding when its lineage is needed; map the complete period tuple, such as Week ID plus Month.
8. Add any final population filters, then Preview.
9. Inspect the complete grain, row count, enrichment and diagnostics. Save to commit the definition and its built population.
10. Select this level as the revision's Base dimension.

**Checkpoint:** the example has a valid Product/Location/Week/Month population with descriptive attributes. Its recorded 576 combinations depend on the actual input membership and selected filters.

This is still three business axes—Product, Location and Time—even though Time uses two key columns in this fixture. The native mappings must cover all four columns.

**Why:** transaction absence does not mean an intersection is invalid. A valid intersection input establishes business coverage independently of Forecast/Sales sparsity. Crossing entire Product and Location domains would create combinations not necessarily authorised by the sourcing matrix. Final DISTINCT removes identical public output rows, including daily repetitions of selected weekly fields; it cannot repair genuinely different attributes at the same declared grain. See L01–L09.

**Failure branches:** fix missing grain keys, ambiguous lookup attributes or missing dimension members. If the same Week ID has different Month values and Month is an ordinary attribute, add the missing identity field or remove the attribute only when business meaning permits. Never select an arbitrary Month to force success.

## 8. Register Forecast and normalise same-record pairs

1. Create a Forecast Transaction and choose its managed input and Product Location Week native dimension.
2. Preview raw input. Enable Pivot for the Product and Location attribute/value pairs.
3. Retain Week ID, Month and forecast_qty. Discover outputs from both pairs.
4. Leave separate-record keys empty when Product/Location pairs already belong to the same source record. Retain an occurrence identifier if otherwise identical records must remain distinct.
5. Preview the pivot output. Map every native key: Product ID, Location ID, Week ID and Month.
6. Add final Transaction filters if needed, inspect normal Preview and Save metadata.

**Checkpoint:** Forecast has a complete direct mapping. It is ready for a referenced measure, but no network measure values have yet been built.

**Why:** same-record pairs and separate Location attribute records require different explicit grouping. The final transaction will later be aggregated independently by all native keys and LEFT JOINed to the native population. Multiple direct detail rows are allowed. See T01–T05 and X01–X03.

## 9. Create a coarser level and register Sales History

Sales History is uniformly at Brand/Zone/Week/Month rather than Product/Location detail.

1. Return to Dimensions and create Brand Zone Week using Distinct projection of an existing level.
2. Choose Product Location Week as parent.
3. Retain Week ID and Month. Promote Brand and Zone from parent attributes into grain keys. Retain Week Start/End as ordinary attributes if they are determined by that grain.
4. Optionally bind Brand to Product.Brand, Zone to Location.Zone and the complete Time tuple to Time. These mappings establish reusable semantic paths or membership; selecting no additional attributes does not expand the grain.
5. Preview and Save.
6. Create Sales from Sales History at Brand Zone Week and directly map Brand, Zone, Week ID and Month. Save metadata.

**Checkpoint:** Forecast and Sales have different explicitly declared native grains, with a proven route between their related dimensions.

**Why:** uniform coarser data can keep its native business authority. Mixed-grain resolution is for rows that need explicit assignment/expansion, not a reason to pretend every source was originally at Base. A reporting pivot can use an ad hoc grouping without creating a persistent dimension for every report. See L03–L09 and X01.

**Alternative branch:** a standalone dimension can be built without dimension bindings and can host a measure without any Base path. Supply mappings or parent lineage wherever cross-level movement is required; names alone do not create that path.

## 10. Define Forecast, Sales and Error measures

1. Create Reference measure FQTY from Forecast.forecast_qty. Its highlighted Output Dimension is inherited from the Transaction. Keep it non-editable for this demo.
2. Set the intended Time and NonTime aggregation methods and their order. Choose missing transactions as NULL, Ignore NULL inputs and an All-null result of NULL for this walkthrough.
3. Create Reference measure SQTY from Sales.sales_qty. Make it editable and choose Weighted by self for edits.
4. Review Base Level Visibility: on an expanding SUM axis, the native coarse amount is split equally over eligible finer combinations during a source build. Other supported axis methods repeat the value. This is not the edit policy.
5. Create calculated Error at a supported output level, using ABS(FQTY − SQTY) and AggregateFirst. Allow a nullable result where business meaning requires it.
6. Save each definition.

**Checkpoint:** measure type, authoritative native grain, calculation timing, NULL handling and edit behaviour are explicit.

**Why:** initial source loading, transaction Split/Copy and interactive edit allocation are three different decisions. A label such as Quantity or Discount does not determine aggregation. AggregateFirst Error evaluates the aggregated operands at the requested grain, rather than summing previously calculated absolute errors. See M01–M12 and C01–C04.

## 11. Build the semantic network

1. Ensure managed views and saved planning populations are current. Build all dimensions from their list when necessary; parents are submitted before projections.
2. Open Decision View and choose Build Semantic Network.
3. Monitor completion. Use the independent dimension selector to inspect each available measure representation.
4. Review explanations for what is materialised, recalculated or unavailable at each level.

**Checkpoint:** Forecast is available at its native detail and supported coarser levels; Sales is available at its native level and, when connected, its derived Base representation. Error is calculated at the correct grain.

**Why:** compatible precomputation reduces runtime work without overriding measure policies. The storage invariant is a population/value pair per revision and dimension, not two tables for an entire model. Failure preserves the previous active network. Full Refresh replaces manual measure edits; it is not an edit replay. See B01–B04 and E03–E07.

## 12. Explore and edit in Decision View

1. Select exposed Dimension attributes for Rows/Columns and choose Values. Reorder attributes and measures, then Update pivot.
2. Inspect the reported source dimension and reuse/recalculation explanation. Measures appear as rows, even when Time is placed on columns.
3. At Brand/Week, FQTY is read-only in this demo; SQTY may be editable because the displayed grain is native or coarser and a path exists.
4. Switch to Product ID detail. SQTY remains visible if a representation exists, but becomes read-only below its authoritative Brand/Zone grain.
5. Return to an allowed grain and edit SQTY. Enter an absolute value and save. Wait for activation and automatic refresh.
6. Observe Error recomputation: for an illustrative group with Forecast 758 and Sales 721, Error is 37. Changing Sales to 750 makes Error 8.

**Checkpoint:** edits reach the correct write representation and dependent measures refresh coherently; the display does not imply finer-grain edit permission.

**Why:** the native business grain determines edit authority, independently of the physical table serving the pivot. Writes go to Base when a compatible path exists; otherwise they go to native and supported coarser levels. Weighted-by-self edits use only populated cells, preserve NULLs and fail on an empty/zero-total pattern. See V01–V05 and W01–W07.

Row/column/measure selections survive normal navigation and refresh in this browser. Clear selections removes them; successful network builds preserve valid selections and query fresh values. Browser memory is not saved semantic metadata or a shared report.

## 13. Resolve mixed-grain Promotions with date ranges

1. Create Promotions at the chosen native Product/Location/Week/Month level.
2. Pivot Product and Location pairs first. Retain promo_id, promo_name, start_date, end_date, promo_type and the numeric discount value as needed. Discover Product ID/Brand/Category and Location ID/Zone outputs.
3. Resolve the date range using the daily Time calendar. Map start/end and bring in Week ID and Month. Inspect the intermediate calendar preview.
4. Choose Resolve multi grain under Dimension mapping. For every native axis, add Column equals attribute rules: Product ID/Brand/Category against the relevant Product coordinates, Location ID/Zone against Location, and derived Week/Month against the matching period coordinates.
5. Retain any contextual columns needed in resolved output or final filters. Choose explicit application order. A numeric sequence is safer than assuming unpadded text promo IDs have numeric order.
6. Configure discount_pct as a value with Copy across members and Copy across dates for the demo's “15% applies everywhere eligible” meaning.
7. Add final filters. Inspect the complete resolution diagnostics and bounded sample, then Save metadata.

**Checkpoint:** the Transaction resolves mixed assignments to actual native intersections, with explicit temporal contributions and precedence. The demo's 154 resolved combinations are data-specific; date expansion can retain several daily contributions per native weekly key until measure aggregation.

**Why:** the calendar stage supplies period identity but does not allocate values. Multi-grain expansion has its own per-value policy because Measures do not exist yet. All present restrictions must match; an absent axis is not permission to broadcast unless explicitly allowed. Later application order wins at any grain, including coarse over detailed. Equal-order overlaps fail; NULL winners do not fall back. Split uses the original match counts and never redistributes overwritten shares. See X04–X09 and R01–R05.

If a final filter removes a winning promotion, the older assignment is not resurrected. Retained promo_type stays in the Transaction output; it does not automatically become a Decision View axis or get copied into the planning population.

## 14. Model promotion effects and edit percentages

1. Create editable Reference measure Promo Discount Percentage from Promotions.discount_pct.
2. Choose AVG for the relevant Time and NonTime aggregations if the intended percentage should remain constant across copied days/members. Select edit disaggregation None/Repeat when an edited coarse percentage must be copied to every eligible finer row.
3. Create CalculateFirst measure Forecast Discount Quantity at Product Location Week: FQTY × Promo Discount Percentage ÷ 100.
4. Create CalculateFirst Final Forecast Quantity: FQTY − Forecast Discount Quantity. Calculations may reference other calculated measures when the dependency graph is valid.
5. Build Semantic Network again (unchanged values are reused), return to Decision View; the saved layout is restored against fresh options and values.
6. Edit the percentage at an eligible coarse grain and verify dependent values. For illustrative Forecast 1,150 and repeated percentage 10, the reduction is 115 and Final Forecast is 1,035.

**Checkpoint:** percentage values repeat as intended, quantities roll up by their configured policies, and the calculated dependency chain refreshes after edits.

**Why:** Weighted by self is a default edit policy, not a universal business choice. It apportions a value; Repeat assigns the same percentage to each row. The demo's demand-reduction formula is a modelling choice, not a built-in rule that price promotions always reduce demand. See M02–M09, C01–C04 and W01–W07.

At the end of the walkthrough, the handoff should contain:

- Saved Product, Location and Time Dimensions, with their intended filters, keys and exposures.
- Current Product Location Week and Brand Zone Week populations, with the intended Base and semantic paths.
- Saved Forecast, Sales and Promotions Transactions with complete native mappings and explicit transformation/precedence policies.
- Reference measures FQTY, SQTY and Promo Discount Percentage; calculated Error, Forecast Discount Quantity and Final Forecast Quantity, with explicit policies and valid dependencies.
- A successfully built network, an explained Decision View and a demonstrated edit that refreshes the dependent results.

These are modelling outcomes, not fixed row counts. Retained transaction context is not automatically a reporting axis; add an appropriate semantic definition when that separate capability is needed.

## 15. Validate, recover and reset deliberately

- Use Validate metadata on Sources to review definition errors. The default revision remains editable; creating, publishing or selecting other models/revisions is not currently exposed. The stored scope and historical lifecycle rules remain available for future support.
- On stale-write or stale-build errors, reload current metadata, reconcile the preserved form and retry explicitly. Do not assume the system silently reapplied changes to a newer version.
- Reload metadata does not build data. Planning Build all does not rebuild measures. Incremental Build Semantic Network preserves unchanged edits; Full Refresh reloads sources and replaces edits.
- Use Nuke everything only for an intentional whole-workspace reset. After one confirmation it removes all application data and configured-database tables/views, including other models and published history, then recreates only the default model and draft. It is not a model-scoped undo or a recovery shortcut.

See P03–P08, B01–B04 and O01–O03.

## 16. Follow-up journeys outside the current demo

Master-data row editing, development-to-production promotion, query-time Decision View filters, server-shared saved layouts and transitive Drill To are separate work. In particular, structural dimension roll-up/down does not mean A → B → C record navigation is implemented. [The Drill To proposal](RELATIONSHIPS.md) requires explicit relationships, roles, navigation targets and filter transfer rules before implementation.

## Authoring controls

- Transformation panels start collapsed, even when configured; their summaries retain enabled/configured status. Dimension **Preview** includes Pivot and final Filters, so there is no separate Pivot preview action. **Why:** one clear final preview avoids confusion over intermediate and final data.
- Planning population uses a switch (**From a Table**, **From Multiple Tables**, **From Existing Dimension**). For parent projections show **Select keys from Parent keys**, **Make Parent attribute as a grain key**, and **Select attributes from Parent** alongside each other. Dimension mappings sit alongside selected Dimension attributes; both layouts stack on smaller screens. **Why:** grain choices and descriptive attributes remain distinct without excessive scrolling.
- Base Level Visibility shows derived Time/Non-time policy selectors with **Equal** or **None – Repeat Values**. SUM selects Equal; other aggregation methods select Repeat. These selectors are informational; edit Dis-Aggregation is configured separately. **Why:** initial materialisation preserves the configured aggregation semantics while edits retain their own distribution policy.
- Decision View retains ordered selections across successful network rebuilds. Fresh catalog validation drops removed references and fresh queries replace displayed values; only explicit Clear selections/browser cleanup resets the layout. **Why:** rebuilding data should not force users to repeat analysis setup.

Decision View offers incremental **Build Semantic Network** and explicit **Full Semantic Refresh** above its pivot. A Materialised table selector below the pivot previews saved tables without building. There is no separate Build Semantic step. Build reuses unchanged values/edits, computes changed measures and dependent formulas, and reports Already up to date when its generation can be retained. Full Refresh confirms once and reloads all source values, replacing edits. Registered source/population/semantic changes invalidate affected representations; direct external ClickHouse row changes need explicit refresh. Existing input snapshots establish pre-upgrade baselines only when provable; otherwise the user receives a safe Full Refresh requirement. Decision View keeps row-dimension and Measure columns pinned during horizontal scrolling.

### Create a count formula

In Measures (or Decision View → Create Measure), enter a formula such as `COUNT_IF([Qty] > 0)`. Choose **Output Dimension** as the grain whose intersections should be counted. The editor validates with the backend and offers the registered functions and available attributes. Output Dimension defines the individual intersections evaluated before grouping. Select coarser row/column attributes in Decision View to see higher-level counts. Save, then run an incremental Semantic Network build. Pivot counts recompute from saved counting-grain values, including edits; raw transaction record counts and distributing counts to finer-than-counting grains are not supported.

Formula columns use one syntax: `[DimensionName.ColumnName]` or `[DimensionName.ColumnName]`, including keys. Measures remain `[MeasureName]`. Attribute discovery lists grain-safe inputs; references must resolve uniquely at Output Dimension. Legacy ATTR/GRAIN formulas remain compatible. Transaction columns first become referenced measures, preserving configured source aggregation.

For String, Date and Boolean referenced/CalculateFirst measures, choose one Aggregation policy, shared across Time and Non-Time. String requires no choice: **None** is selected and disabled, preserving single-value/Mixed semantics. Transaction-side measure creation uses the same controls. Numeric measures retain separate Non-Time and Time policies.

### Inline formula builder

Calculated measures use an inline editor beside a searchable input library, rather than function/attribute dropdowns or a new modal. Switch between **Measures**, **Columns** (grouped by Dimension or Dimension) and **Functions**. Click an input to insert it at the current caret or replace selected text; focus returns to the formula. Functions insert parentheses with the caret inside, while zero-argument functions leave it after the closing parenthesis. Function help shows syntax, explanation and an example. Validation and inferred result type come from the backend.

Use Ctrl+Space to focus search, Enter to insert the first match, Down to focus results, Escape to return to the editor, and arrow keys to move between library tabs. Operator buttons are available in the full editor. The existing Decision View Create Measure modal reuses a compact single-line version; advanced settings stay below the library.

For calculated measures, the full editor places Measure type, Calculation type and Output Dimension in that order on one row, stacking responsively on smaller screens. For references, Output Dimension remains read-only beneath Transaction and Value column selection. Formula input and the input-library panel share their top and bottom borders; operators and validation remain beneath the formula input. Quick Create retains its compact layout.

The Measures library includes referenced and calculated measures. Compatible calculated inputs show a Calculated label. Unrelated inputs and measures that would introduce a dependency cycle are disabled with an explanation. A formula can use a calculated input directly; the engine resolves its dependencies first and honours every calculation's grain and calculation type. An input does not need to be separately built at each displayed grain. Save and live formula validation check the complete chain.

Formula validation errors identify measures and output dimensions by their user-facing names. Invalid formulas show red diagnostic text and a red input border; the error styling clears when validation succeeds. Stable IDs remain in structured API diagnostic metadata for internal references.
