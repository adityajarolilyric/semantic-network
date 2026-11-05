/** Reviewed UI vocabulary; no API/schema dependency or persisted help state. */
const help: Record<string, string> = {
  'Record keys for separate attribute rows (optional)':
    'Choose retained columns identifying one record when its attributes arrive on separate rows, such as Customer ID for Channel and Sold To. Leave empty when each row contains its attribute/value pairs, such as Brand and Zone in a forecast. This is independent of the optional output primary key.',
  'Retained columns':
    'Columns to keep beside the generated pivot outputs, such as Week and Forecast quantity. Attribute/value input columns are consumed; choose the generated Brand or Zone columns as keys after discovery.',
  'Time resolution':
    'Exact value uses normal column/attribute matching. Date range expands inclusive Start/End dates through the daily Time dimension before mapping them to the native period key.',
  'Range Time dimension':
    'A daily calendar providing actual dates and their Day, Week or Month IDs. Custom fiscal calendars work through these saved mappings.',
  'Calendar column':
    'The Time dimension attribute containing the native planning period key. For a weekly level, choose its complete Week ID; dates are not grouped using an assumed week rule.',
  'Range start column':
    'The first included calendar date. Use Date/Date32 or an ISO date string, such as 2026-01-01. Both range endpoints are required.',
  'Range end column':
    'The last included calendar date. Start and End may be the same day. Reversed, invalid or partially uncovered ranges fail.',
  'Across dates':
    'Split divides a value among its original matched calendar days; Copy repeats it each day. Later overrides do not redistribute earlier shares. Weekly values then use the measure’s Time aggregation.',

  'Allow all members':
    'If a row has no restriction for this axis, match every member present in the native population. Other axes still restrict the assignment. Use deliberately to avoid a broad expansion.',
  'Selector value':
    'An exact literal stored in the level column, such as brand. Each literal maps to one native output or dimension attribute.',
  'Attribute for selector':
    'The attribute used to interpret the member whenever this selector literal appears.',
  'Resolve value columns':
    'Select only numeric quantities to distribute. Split divides by the assignment’s full original match count; Copy repeats its value. Later assignments replace earlier values without redistributing overwritten shares.',
  'Treat empty text as absent':
    'Empty text skips this restriction. Other restrictions still apply; an unconstrained axis requires Allow all members. NULL is always absent.',
  'Transaction mapping':
    'No Planning Level creates a planning level from distinct selected grain keys. Map to Planning Level uses an existing level. Select Resolve multi-grain data when source rows need expansion to its grain.',
  'Resolve multi-grain data':
    'Use when rows describe different grains, such as Product/Location and Brand/Zone. Match them to the selected planning level and choose how each numeric value is split or copied. Leave unchecked for direct grain-key mapping.',
  'Match using':
    'Column equals attribute matches a fixed source column. Level and value uses a selector column, such as Brand or Product ID, to interpret a second column.',
  'Match source column':
    'Contains the member to match, for example Brand A. Every supplied restriction must match the native intersection.',
  'Match native attribute':
    'The native output or reachable dimension attribute against which this source value is matched.',
  'Level column':
    'Identifies which attribute each row uses, such as id or brand. Selecting this column discovers its values; map each one to an attribute below.',
  'Matching value column':
    'Contains the member for the selected level. For example selector brand with member A matches all native products in Brand A.',
  'Apply-order column':
    'Larger values apply later. Choose a source or retained column that uniquely orders overlapping assignments. Numbers/dates use natural order; text is alphabetical (P10 before P2). A later coarse row can overwrite earlier detail; physical row order is ignored.',
  'Order tie-breaker columns':
    'Compared in the displayed order when the primary order ties. Overlapping assignments with equal complete order fail instead of choosing arbitrarily.',
  'Independent assignment series':
    'Separate streams that do not overwrite one another, such as independent order books. Measures subsequently aggregate the resulting rows using their configured policy.',
  'Additional columns to retain':
    'Carry these fields from the winning assignment. Numeric fields retained here are not split or copied as measure values.',

  'Planning View rows':
    'Dimension or planning-level fields that group records down the pivot. Example: Brand then Zone gives one row for each Brand/Zone combination. Use the arrows to change their order.',
  'Planning View columns':
    'Dimension or planning-level fields whose values become column groups. Example: Month compares January and February side by side. Keep this list short for a readable pivot.',
  'Planning View values':
    'Measures appear as rows at the selected dimension grain. Aggregation follows each measure’s saved Time and NonTime policies. AggregateFirst formulas use inputs resolved at this grain.',
  'Planning View source':
    'The engine selects a compatible materialised level that can answer correctly. It falls back to Base when a coarser table lacks attributes, measures or safely reusable aggregates. Stored averages, for example, cannot generally be averaged again.',

  'Semantic model':
    'Groups the tables, dimensions, planning levels and measures that belong together.',
  Revision:
    'A version of your semantic definitions. Drafts can be changed; published revisions are read-only.',
  Managed:
    'Makes this source available for semantic modelling through a managed view. The original table stays unchanged.',
  'Copy the published definition':
    'Starts the new draft with the published definitions. Its managed views and planning tables must be built again.',
  'Pivot long-format columns':
    'Turns attribute/value pairs into named columns. Groups pair values with retained columns. Provide record keys to combine attributes from separate rows. Example: product_attribute=brand becomes a Brand column.',
  'Retain / group by columns':
    'Columns that identify each output row after pivoting. Example: Product and Date. Duplicate pivot cells fail the build; values are not summed or deduplicated.',
  'Attribute / value pair':
    'Connects a column containing output names with the column containing their values.',
  'Attribute column':
    'Contains the values that become column names. Example: metric contains Forecast and Sales.',
  'Value column':
    'The column containing the values to use. In a pivot, its values fill the new output columns. For a measure, choose the numeric transaction column.',
  'Output columns': 'Choose which discovered attribute values become columns in the managed view.',
  'Optional managed output primary key':
    'Columns whose combined values must uniquely identify each output row. Duplicate key combinations fail the build.',
  'Ensure saved view': 'Rebuilds the managed view using its saved configuration.',
  'Managed table':
    'Supplies the records for this definition through its managed view. Example: Product for a dimension, or Forecast for a transaction.',
  'Display name': 'The column label shown to users. Changing it does not rename the source column.',
  Key: 'Identifies a dimension member. Select multiple columns when the member needs a combined key.',
  Exposed: 'Makes this attribute available in the semantic layer. Key columns are always exposed.',
  'Time dimension':
    'Identifies this as a calendar dimension so the engine can apply Time aggregation rules.',
  'Time grain': 'The time interval represented by each member. Example: one day or one month.',
  'Date column': 'The date representing each time member.',
  'Ordering column':
    'Defines chronological order, including which value First or Last aggregation selects.',
  'Member label column':
    'The readable label for a member. Example: show Product Name for a Product ID.',
  'Base planning level':
    'The finest planning level in the model. Compatible measure values flow through Base to other levels.',
  'Population method':
    'How planning rows are created: read one table, combine tables, or take distinct combinations from another level.',
  'Population table': 'Supplies the existing combinations used to create planning rows.',
  'Grain columns':
    'Columns whose combined values identify one planning row. Example: Product, Location and Date.',
  'Cartesian product':
    'Combines every row from one table with every row from the others. Example: 20 Product–Location combinations × 12 months creates 240 rows.',
  'Population attributes':
    'Additional columns to carry into the planning table. They describe a row without becoming its grain keys.',
  'Parent planning level':
    'The existing level from which distinct key combinations and attributes are taken.',
  'Select keys from Parent keys':
    'Choose which parent keys identify rows in the new level. Example: retain Product and Time from Product–Location–Time.',
  'Select attributes from Parent':
    'Carry parent attributes into the new table without using them as grain keys.',
  'Make Parent attribute as a grain key':
    'Create a level identified by parent attributes. Example: use Brand instead of Product ID.',
  'Dimension bindings (optional)':
    'Connect planning columns to dimension members for validation, enrichment and hierarchy traversal. Explicit key projections can work without bindings.',
  'Planning-level column':
    'Choose the planning column used for the dimension lookup. Example: planning product_id matches the Product dimension’s product_id.',
  'Dimension column':
    'Choose the matching dimension column. The mapping checks that values exist. If you select extra attributes, the mapped values must determine one distinct combination of those attributes.',
  'Dimension attributes':
    'Choose dimension attributes to include in the planning table. Example: bring Brand from Product.',
  'Native planning level':
    'The level where a measure is originally loaded or its calculation is defined. Transactions are first grouped and matched at their native level.',
  'Transaction grain columns':
    'Columns identifying one planning intersection, such as Product, Location and Time. The planning population takes distinct combinations; repeated fact records remain available for measure aggregation.',
  'Map every grain key':
    'Match each native planning key to its transaction column. Transactions are aggregated by all these keys before the left join.',
  'Output Planning Level':
    'The planning level where this measure is defined. Reference measures inherit it from their transaction; calculated measures use the selected level.',
  'Measure type':
    'Referenced measures read a transaction column. Calculated measures evaluate a formula using other measures.',
  Transaction: 'The transaction definition supplying this measure’s records and native grain.',
  'Missing transaction value':
    'What to show when a planning row has no matching transaction group: NULL or zero.',
  'Editable measure':
    'Declares that this measure may be edited at its native or a supported coarser level. Planning View permits edits at native or proven coarser grains. Changes go to Base where reachable, otherwise native, and compatible coarser values are recomputed.',
  'Calculation type':
    'AggregateFirst recalculates the formula from inputs at each requested level. CalculateFirst calculates at native grain, then aggregates or disaggregates that result.',
  Formula:
    'An expression using measure names. Example: ABS(FQTY - SQTY). NULL inputs produce NULL unless the formula explicitly handles them, for example with COALESCE.',
  'Nullable result':
    'Allows the calculated result to be missing or unknown. NULL is different from zero.',
  'Non-time aggregation':
    'How values combine across non-time dimensions. Example: SUM across Locations, or AVG when an average is required. COUNT counts usable values; COUNT DISTINCT counts different values.',
  'Time aggregation':
    'How values combine across time. Example: SUM daily quantities or take the Last inventory value. First and Last use the Time dimension’s ordering column.',
  'Aggregation order':
    'Which axes combine first. Example: combine Locations within each day, then combine days. Changing the order can change the result.',
  'Null input policy':
    'Ignore skips NULL inputs. Propagate returns NULL if any input is NULL, even when All-null result is set to zero.',
  'All-null result':
    'When Ignore leaves no usable inputs, choose whether the result is NULL or zero. This does not fill an absent transaction group.',
  'Dis-aggregation policy':
    'How user edits spread to finer rows. Source materialisation instead splits SUM axes equally and repeats other axes. None repeats the value; Equal shares it evenly; Weighted uses another measure; Weighted by self uses this measure’s previous proportions at the write level (Base where reachable, otherwise native). During edits, NULL cells are left unchanged and only populated values supply weights. Your configured aggregation still controls how values combine.',
  'Dis-aggregation basis measure':
    'The measure supplying proportional weights. Example: weights 30 and 70 split a value of 200 into 60 and 140. Missing weights or a zero total fail the build.',
  'Build Semantic Network':
    'Builds new or changed measure representations and dependent formulas. Unchanged values, including manual edits, are reused. If nothing changed, the current generation is kept. The preview selection does not restrict the build.',
  'Full Refresh':
    'Recomputes every measure from its source data and replaces manual edits. Use after direct external source-data changes or when a complete source reload is required.',

  'Preview planning level':
    'Choose the built table to inspect. Shows at most 50 rows and does not trigger a build.',
  'Network status':
    'Ready means built values are available. Building means work is in progress. Needs rebuild means inputs changed. Failed means the latest build could not finish; any previous generation stays intact.',
  'What gets materialised':
    'Explains each measure’s native level, processing path and policies at the selected level.',
  Unavailable:
    'This measure could not be represented at this level. The accompanying reason explains what is missing.',
}
export function fieldHelp(label: string): string | undefined {
  if (help[label]) return help[label]
  if (label.startsWith('Time resolution for ')) return help['Time resolution']
  if (label.startsWith('Calendar column for ')) return help['Calendar column']
  if (label.startsWith('Allow all ')) return help['Allow all members']
  if (label.startsWith('Selector value ')) return help['Selector value']
  if (label.startsWith('Attribute for ')) return help['Attribute for selector']
  if (label.startsWith('Keys from ')) return help['Grain columns']
  if (label.startsWith('Include ')) return help['Cartesian product']
  if (label.startsWith('Population attributes from ')) return help['Population attributes']
  if (label.startsWith('Attributes from ')) return help['Dimension attributes']
  if (/ planning-level column \d+$/.test(label)) return help['Planning-level column']
  if (/ dimension column \d+$/.test(label)) return help['Dimension column']
  return undefined
}
