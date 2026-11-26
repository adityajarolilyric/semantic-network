# Final-output filters

Dimensions, Dimensions and Transactions optionally store `definition.filters`. This is revision-owned metadata and uses stable output column IDs. The shared engine module is `backend/app/engine/transformations/filters.py`; `FilterCompiler` validates typed predicates and generates SQL from allowlisted operators and quoted literals. The reusable UI is `FilterEditor`; schema loading is separate from authoring and never builds or saves data.

## Placement

- Dimension: managed input → optional owned Pivot → semantic column aliases → Filters → exposure/consumption.
- Dimension: population (source, Cartesian or parent projection) → dimension enrichment → Filters → final public DISTINCT output. Enriched attributes can be filtered too.
- Transaction: managed input → optional owned Pivot → optional Date resolution → direct/multi-grain mapping and winning assignments → Filters → measure aggregation/LEFT JOIN.

Filters wrap the preceding relation with `SELECT * FROM (...) WHERE ...`. Do not push transaction predicates ahead of splitting or assignment precedence: filtering a winning row must not resurrect an earlier assignment, and numeric predicates see distributed values. Full-input transformation/data checks still run. Filters do not modify raw or shared managed inputs, allocation policies, measure editing rules or physical table counts. Intermediate Pivot/Calendar previews exclude final filters; normal Preview includes them, shows at most 50 rows and reports the filtered total.

## Predicates

- String: equals/not equals, contains/does not contain, starts/does not start with, ends/does not end with, in/not in, blank/not blank (empty string). Matching is case-sensitive; `%` and `_` are ordinary characters, not wildcards.
- Numeric: equals/not equals, greater/less than and inclusive variants, between/not between, in/not in. Numeric literals are validated as finite decimals; retain string input precision rather than converting through JavaScript Float64.
- Date/DateTime: equality, before/after and inclusive variants, inclusive between/not between, in/not in. Dates require ISO dates. Datetimes without offsets use the column timezone; explicit offsets identify instants. Relative-date functions are outside this version.
- Boolean: true/false equality or inequality.
- All: is NULL/is not NULL. Empty strings are distinct from NULL. Ordinary predicates, including negative predicates, exclude NULL; include missing rows using an explicit OR/is-NULL condition.

Groups combine children with AND or OR; nesting represents parentheses. Limits: 16 levels, 100 children per group, 1,000 total nodes and 100 values per list. Missing/stale column IDs, invalid operator/type combinations and malformed values fail with semantic errors; no arbitrary SQL is accepted.

```json
{
  "root": {
    "type": "group", "combinator": "and", "children": [
      {"type": "condition", "columnId": "brand", "operator": "in", "values": ["A", "B"]},
      {"type": "group", "combinator": "or", "children": [
        {"type": "condition", "columnId": "qty", "operator": "gte", "values": ["10.5"]},
        {"type": "condition", "columnId": "qty", "operator": "is_null", "values": []}
      ]}
    ]
  }
}
```

## Schemas and compatibility

Dimension filter columns come from semantic aliases over the effective pivot schema, including unexposed attributes. Dimension columns are analyzed by `POST /dimensions/schema`; Transaction columns by `POST /transactions/resolution/schema`. Both accept an unsaved complete object definition and return metadata only. Multi-grain filters can use generated native keys, resolved dates, configured values and retained columns; a raw column discarded by mapping must be retained first. Planning schema analysis requires ready input managed views/parent populations, but does not create an output table. Requests are revision-scoped.

Existing Dimension/Planning filter shapes and IDs remain supported. Absent Transaction filters are omitted from serialization to preserve existing definition hashes. Disabled filters serialize as absent/null, so existing definitions without filters keep their existing execution path. Changing a filter invalidates the normal definition/build fingerprint; normal Preview and Save retain the established build/commit behavior. No data migration or new collection is needed.

Regression coverage includes quoted literals, type/operator rejection, nested groups and bounds, all basic scalar types in ClickHouse, filters after independent pivots, Cartesian/projection outputs, filtering after split/last-wins, measure materialisation, filtered row counts and UI save/reopen behavior.

## Filter editor UX

The empty section states that all rows are included and offers **Add filter**. Enabled filters use compact typed rows, AND/OR toggle buttons with explicit connectors, bordered nested groups, inline removal, and Add condition/Add group actions within each group. New nested groups default to the opposite combinator to make common AND-with-OR expressions faster to author. The header reports condition count, Clear all removes the optional filter definition, and a readable resulting-rule summary makes parentheses visible. Matching/NULL/list guidance is under About matching. Lists remain one literal per line, preserving commas within values. Controls stack on small screens; existing tree IDs, predicates and query execution are unchanged.
