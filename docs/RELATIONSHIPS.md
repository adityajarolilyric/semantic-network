# Transitive relationships and Drill To

Status: **proposal for review; not implemented**. This adds related-row navigation and filter propagation across Managed Tables. It does not change measure aggregation, allocation, edit eligibility or the current planning/value table limit.

## Recommended simplification: Managed Table PK/FK metadata

Proposal under review: use Managed Table primary/foreign keys as the authoritative structural relationships. `ManagedDefinition` already has `primaryKeyColumnIds`; it has no FK contract yet. Add FK entries referencing ordered local effective column IDs, target logical Managed Table/source ID, target PK column IDs, stable FK identity and business role. Capture composite tuples intact after transformation. Referenced tables must have a valid declared key; unrelated/keyless tables remain supported but cannot be FK targets until keyed. FK target/key/schema changes participate in graph validation and revision lifecycle.

Each FK supplies a child-to-parent many-to-one edge and its parent-to-children traversal. Do not store duplicate generic relationship definitions for the same key link. A normal FK does not imply one-to-one or a general measure rollup/edit path. No DDL constraint/source mutation is implied. Current managed-key checks reject duplicates but do not separately reject NULL keys; full PK NULL/uniqueness and FK unmatched/NULL behavior must be explicitly agreed before implementing stronger data validation.

Keep a small separate navigation definition: name, origin dimension/attribute, target table/level/measures, selected FK path/roles and filter carry/replace rules. Unambiguous structural routes can be suggested; multiple paths require modeller choice. Automatic linked-view propagation remains opt-in. FK links permit record navigation independently of Base, without changing aggregation/allocation/edit policies.

For BOM, both parent_product_id and component_product_id can reference Product.product_id, with distinct Finished Product and Component Product roles. Inventory.product_id also references Product. PK/FK establishes all the joins, but role selection determines that BIKE-100 enters BOM through parent_product_id and leaves through component_product_id. Retain Location/Time only under explicit applicable mappings. This reduces manual join entry to one-time FK setup; a planner still selects a named Drill To action. The earlier generic-relationship proposal below is superseded for ordinary PK/FK links; non-key/temporal relationships remain a separately designed extension.

## Worked planning example and user configuration

This example is illustrative; quantities are not workspace observations. A planner selects Product=BIKE-100, Location=DC1 and Week=W45 in a Forecast planning view, then chooses **Drill To → Component stock**. The BOM Managed Table has (parent_product_id, component_product_id, location_id) rows (BIKE-100,WHEEL-10,DC1) and (BIKE-100,CHAIN-20,DC1). Inventory has Product/Location/Week rows WHEEL-10/DC1/W45=40 and CHAIN-20/DC1/W45=60; unrelated SEAT-30 stock is excluded.

The source filter matches BOM.parent_product_id (and location), yielding the distinct component Product IDs WHEEL-10 and CHAIN-20. The target Inventory/Stock planning view filters Product by those IDs and retains DC1/W45. B is filtered to the relevant BOM rows; C is filtered to their components. The original Forecast view keeps BIKE-100. Do not apply Product=BIKE-100 again on C: the navigation deliberately replaces the finished-product role with the component-product role. Other Product attributes such as finished-product Brand constrain the source member set; they are not blindly reapplied as component Brand filters.

Product may be one existing dimension playing two explicit roles. This navigation is a relationship across members, not proof that components are a finer planning grain or that an edit/rollup is permitted. It navigates to existing component stock; it does not calculate component requirements from forecast or multiply stock by BOM quantities. Initially assume a static single-level BOM with explicit location membership; alternate/effective-dated BOM choice requires more metadata before support.

Proposed modeller flow: **Dimensions → Product → Drill To → Add navigation**. Capture once:

1. Navigation name: Component stock; enabled for the Product ID selection (optionally restricted to selected source measures/contexts).
2. Target: a saved dimension/measure selection showing STOCKQTY, or a Managed Table row view for Inventory. Capture which type; they are separate query contracts.
3. Through Managed Table: BOM. Show saved effective columns, including pivot outputs; physical names/IDs remain internal.
4. Match source selections: Product ID → BOM.parent_product_id and Location ID → BOM.location_id. Support complete composite tuples and many matching rows.
5. Map output: BOM.component_product_id → target Product ID, with role Finished Product → Component Product. Reuse configured relationship steps when present.
6. Filter transfer: replace Product using mapped outputs; carry Location/Week through their proven semantic mappings. Every relevant source filter is mapped, explicitly source-only, or rejected—never silently ignored. Do not pass an unsupported Customer/Time role by guessing names.
7. Preview mapping: select BIKE-100/DC1/W45, show matching BOM rows and WHEEL-10/CHAIN-20 target selections before saving. A valid empty mapping yields an empty target, not an unfiltered target.

Avoid a separate authoritative hasDrillTo flag: enabled navigation definitions determine availability. A modeller configures meaning once; planners choose a named action, rather than selecting SQL keys on every click. Optional linked-view filtering can reuse the same definition, but must be explicitly enabled; do not broadcast all Product filters across every graph edge automatically.

Keep two metadata responsibilities: reusable relationship definitions describe column/key links and roles; a revision-owned navigation definition describes the origin dimension/attribute, target, ordered relationship IDs, carry/replace/source-only filter rules, permitted contexts and enabled state. Both use stable owner-qualified IDs and revision guards/clone/publication semantics. The Dimension UI lists navigations by origin ID rather than embedding a second copy. Relationship/navigation object kinds or equivalent typed records require schema/API/validator work; neither exists today. Selected member values remain request/UI context, not shared persisted definitions. The target reuses current dimensions/measure definitions; no Base path, physical table or measure policy is added merely to enable Drill To.

## Vendor references and limits of the comparison

These are public product patterns, not claims about proprietary storage schemas or exact internal algorithms:

- SAP IBP 2511 documents contextual navigation passing component product ID as product ID, retaining previous-navigation filters and BOM navigation using Product/Location/Component Product. This directly supports the role-remapping example. See [What's New 2511, contextual navigations, printed pages 81–82](https://help.sap.com/doc/45c890e3fae24341bba7f2ee37edd1aa/2511/en-US/ef619e53b0004247b6aca7d94f3e0fc3.pdf#page=82). Its 2502 documentation also describes attribute mapping using master data to obtain target filter values.
- Kinaxis describes mapped business objects/relationships and traceable impact paths in its [Semantic Graph](https://www.kinaxis.com/en/semantic-graph). Its public [author certification objectives](https://www.kinaxis.com/en/knowledge-services/certification) include configuring drill to details and worksheet variables. Public material does not expose the exact configuration schema needed to reproduce its implementation.
- o9 describes an Enterprise Knowledge Graph connecting enterprise systems and products/customers/suppliers/resources in its [IBP overview](https://o9solutions.com/solutions/integrated-business-planning). This supports explicit business relationship modelling; it does not document arbitrary automatic A/B/C filter inference or exact UI fields.

Our proposed modeller-configured relationships plus named navigation/filter mappings adopt those observable patterns while retaining MongoDB metadata and ClickHouse execution. They do not require copying a vendor's database architecture.

## Current capability and missing layer

`LevelRelationshipAnalyzer` proves dimension coordinate compatibility through explicit projection lineage and dimension mappings. It supports roll-up/down, formula availability and edit checks. It is not a general Managed Table relationship/path catalog. Decision View currently accepts rows, columns and measures, without a query-time filter or Drill To contract.

Add a reusable relational relationship analyzer/planner beside the existing grain analyzer. A row-filter path does not prove a valid grain rollup, exact measure contribution or editable path; return those capabilities separately. Drill To need not pass through Base.

## Supply-chain example

Illustrative data, not current workspace configuration:

- A, CustomerOrderLine: orderLineId (key), productId, locationId, customerId, orderDate, orderedQty.
- B, ShipmentLine: shipmentLineId (key), orderLineId, shipmentId, shipDate, shippedQty. A order can have multiple shipments.
- C, FreightChargeLine: chargeLineId (key), shipmentLineId, chargeAmount. A shipment line can have multiple charges.

Declare A.orderLineId = B.orderLineId and B.shipmentLineId = C.shipmentLineId, both one-to-many from left to right. Product dimension filtering reaches A through an explicit/proven Product-to-order mapping. Foreign-key lookup direction (B to A, C to B) differs from this proposed A-to-B-to-C filter direction.

For Product P1, suppose A selects order O1, B has S1/O1 and S2/O1, and C has F1/S1, F2/S2 and F3/S1. A separate P2 order O2, shipment S3 and charge F4/S3 are excluded. Filtering Product=P1 retains O1, S1/S2 and F1/F2/F3 even though C has no productId/orderLineId. A/C are connected by the composition of the declared edges; no direct duplicated Product column or A/C edge is necessary.

## Related rows versus exact contribution

A target row is related when at least one complete allowed path connects it to the original filtered rows. Preserve each qualifying target source row, including legitimate duplicate rows; do not multiply it by the number of matching paths. Use declared record keys for identity/counts where available, and require stable unique target identity before promising record-specific links. Never deduplicate unrelated target records just because their display values match.

Many-to-many bridges can support this existential filter. They do not necessarily make target measures attributable to the selected dimension. If C is instead one charge of 100 per entire shipment covering P1 and P2, a P1 drill can return the charge once as a related record. It cannot label 100 as P1's exact freight contribution. The same record may legitimately appear in a P2 drill, making those subsets overlap. Do not split charges, change the measure's aggregation/disaggregation, or infer an allocation basis. Exact contribution needs finer source detail or an explicit business allocation contract.

Similarly, a preaggregated target cell that mixes selected/unselected records cannot be exactly filtered by retaining its whole value. Select a finer supporting source and apply the filter before policy-aware aggregation/formula evaluation, or report exact filtered values unsupported. AggregateFirst still recalculates at the requested filtered grain. A filter relationship grants no new edit permissions.

## Proposed revision-owned metadata

Represent a relationship as a new typed semantic object kind, using the existing object envelope, revision guards, clone/publication lifecycle and dependency-aware deletion. This is proposed schema work, not an already-supported kind. Capture:

- Stable relationship ID, name/description and business role (for example order fulfilment or shipment charge).
- From/to logical source IDs, resolving their saved Managed Table outputs; no physical database/view name.
- Ordered composite column-ID pairs defining equality. IDs are owner-scoped and type-compatible; matching names do not establish an edge.
- Declared cardinality from left to right, and any referenced unique tuple. Validate claims when operations rely on uniqueness; declarations are not live evidence.
- Explicit allowed filter direction(s), active state and related-row/existential semantics. Default to configured directions, not global bidirectional filtering.
- Explicit NULL/unmatched behavior. Initial equality filtering treats NULL/missing links as nonmatches, never wildcards; expose orphan diagnostics separately.

Use existing dimension/source/transaction/planning lineage only when it proves an equivalent edge, retaining provenance. Otherwise require an explicit relationship. Keep definitions shared across scenarios. Runtime cell selections/filter values, query plans and cached key sets are not persisted semantic definitions. Schema/version validators, CRUD/API classes, clone/reset handling and documentation must all recognize the new kind before it executes.

## Query planning and execution

1. Capture original typed filter predicates and selected pivot-cell coordinates, each with its owner/semantic IDs and role. Preserve complete composite tuples, AND/OR grouping and same-intermediate-row correlation. Separate product/location lists must not create false intersections.
2. Resolve the origin binding and allowed relationship path for each target. Select the one configured/unambiguous semantic route; multiple business routes require a saved preference or an explicit choice. Shortest/cheapest does not determine meaning. Prevent cycles and bound hops/path candidates; cost optimisation applies only after semantic equivalence is established.
3. Capture relationship versions, saved managed outputs, required population/value generations and live schemas under existing transaction/read leases. Never fall back to raw sources or a newer binding midway through execution.
4. Compile an engine-owned Filter/SemiJoin chain into one bounded ClickHouse query per target. In the example: filter A, derive qualifying order IDs, filter B, derive qualifying shipment-line IDs, then filter C. Keep intermediate key sets in server-side CTE/subqueries, not browser ID lists. Existing dimensions' saved filters remain in force.
5. Use membership/SEMI JOIN rather than joining raw A/B/C measures into one flattened fact. It filters the target without fan-out; join-based enrichment of additional attributes needs its own functional-cardinality proof. Preserve matching tuple/row correlation when several predicates share a path. Scope key-set DISTINCT to membership keys, not arbitrary target-row deduplication.
6. Return bounded target records and path/capability explanation. Pagination limits displayed target rows only; it must not truncate upstream keys and silently change matches. Initial API should fail clearly on key-set/query/result bounds. Missing path is unsupported, not zero results; a valid path with no matches is empty.
7. For filtered measures, select a sufficient grain, push predicates before aggregation and use existing Time/NonTime/null/calculation policies. Never reuse a coarser unfiltered cache just because it contains a matching key. More complex mixed-grain resolved transactions need an explicit decision about filtering before or after their assignment overlay; raw related rows alone are not proof of contribution.

ClickHouse supports SEMI JOIN as a matching-key allowlist without join fan-out; verify generated queries on the project's pinned ClickHouse 25.3 during implementation. See [official JOIN reference](https://clickhouse.com/docs/reference/statements/select/join). No graph database, pre-expanded all-pairs row closure, extra planning/value tables or automatic value allocation is required. Cache bounded metadata path/compiled-plan analysis by revision/relationship/schema fingerprint; add runtime key-set caches only with explicit data-generation/invalidation guarantees. Managed views over externally mutable data retain their current nontransactional source-data caveat.

## Filter context and UI

Keep one original semantic filter context and plan each target from it. Do not let a target's filtered result silently feed back into the origin or sibling targets. Reverse filtering is separate permission. Multiple filters must retain their intended same-row versus related-row semantics; do not independently collapse witnesses and then combine incompatible sets.

Drill To should offer only supported targets, explain the path and carry the selected cell plus existing filters. Example labels: related shipment lines, related freight charges, exact filtered measure supported, or related records only (shared charge). Choosing Product Brand resolves Brand to the proper Product members before propagating—not a guessed join on Brand text.

Time roles stay explicit: filtering Order date on A selects orders and their related shipments; it does not also mean Ship date on B or Charge date on C. Users may add those filters independently. Effective-dated/BOM/range relationships need an explicit date/role/context contract and are outside initial equality paths.

For an aggregated or edited planning cell, source navigation is related-data exploration unless a supported provenance/contribution plan proves reconciliation. Manual values have no durable edit journal; do not promise transaction rows that explain every edited total. Multi-grain replacement/Split/Copy and date-range contributions likewise require the existing resolution engine for exact contribution drill, not raw-source sums.

## Suggested delivery order

1. Agree relationship ownership, endpoint mappings/directions and related-row versus contribution semantics using the example.
2. Add versioned relationship metadata and shared path analysis with clear ambiguity/unsupported reasons.
3. Implement read-only Drill To across equality paths with server-side membership filtering, paging, leases and resource limits. Start with related records and preserve all existing measure/edit behavior.
4. Add Decision View semantic filters and exact-filter capability checks/source selection; support contributions only where policies/provenance establish them. Bridges, alternative paths, temporal edges and allocations expand only through explicit contracts.

Acceptance cases: direct/two-/three-hop paths, multiple children/charges without target fan-out, composite keys, NULL/orphans, duplicate target values with distinct identities, many-to-many overlapping subsets, ambiguous/cyclic/disallowed reverse paths, shared-charge contribution rejection, same-row filter correlation, Order-date versus Ship-date roles, incomplete coarse caches, stale definitions/leases, paging without upstream truncation and existing current-table/edit-permission invariants.
