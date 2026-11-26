export type Identity = {
  objectId: string
  name: string
  description: string
  schemaVersion?: number
}
export type Semantic = Identity & { displayName: string }
export type SourceColumn = {
  columnId: string
  name: string
  physicalType: string
  nullable: boolean
  description?: string
  unit?: string | null
}
export type AttributeValue = string | number | boolean | null
export type PivotPair = {
  pairId: string
  attributeColumnId: string
  valueColumnId: string
  outputs: { columnId?: string | null; attributeValue: AttributeValue; name: string }[]
}
export type ManagedDefinition = {
  primaryKeyColumnIds: string[]
  pipeline: {
    stageId: string
    type: 'pivot'
    enabled: boolean
    recordKeyColumnIds?: string[] | null
    mode?: 'auto' | 'merge_rows' | 'expand_rows' | null
    resolvedMode?: 'merge_rows' | 'expand_rows' | null
    groupByColumnIds: string[]
    pairs: PivotPair[]
  }[]
}
export type PivotStage = ManagedDefinition['pipeline'][number]
export type ManagedTable = {
  ready?: boolean
  sourceId: string
  version: number
  definitionHash: string
  definition: ManagedDefinition & { columns: SourceColumn[] }
}
export type EngineRun = {
  runId: string
  sourceId?: string
  resourceKind: 'managedTable' | 'dimension' | 'dimensionPreview' | 'measureSet' | 'semanticNetwork'
  resourceId: string
  state: string
  error: { message: string; details?: { message: string }[] } | null
  buildMode?: 'incremental' | 'full' | 'edit'
  reused?: boolean
  generationId?: string
}
export type Source = Identity & {
  managed?: ManagedTable
  kind: 'source'
  definition: { tableName: string; inScope: boolean; keyColumns: string[]; columns: SourceColumn[] }
}
export type DimColumn = {
  columnId: string
  name: string
  displayName: string
  sourceColumnId: string
}
export type DimensionMembers = Semantic & {
  kind: 'dimensionMembers'
  isTimeDim: boolean
  definition: {
    sourceId: string
    pivot?: PivotStage | null
    columns: DimColumn[]
    keyColumnIds: string[]
    labelColumnId: string
    attributeColumnIds: string[]
    time: {
      baseGrain: 'day' | 'week' | 'month'
      dateColumnId: string
      orderingColumnId: string
    } | null
    filters?: FilterDefinition | null
  }
}
export type GrainColumn = {
  grainColumnId: string
  name: string
  populationRef: {
    factorId?: string | null
    columnId?: string | null
    sourceGrainColumnId?: string | null
    sourceOutputColumnId?: string | null
  }
}
export type PopulationAttribute = {
  columnId: string
  name: string
  populationRef: GrainColumn['populationRef']
}
export type Factor = { factorId: string; sourceId: string; selectedColumnIds: string[] }
export type Population =
  | {
      operation: 'source'
      sourceId: string
      inputTransform?: {
        objectId: string
        pivot?: PivotStage | null
        calendarResolution?: CalendarResolution | null
      } | null
    }
  | { operation: 'cartesian_product'; factors: Factor[] }
  | { operation: 'distinct_projection'; sourceDimensionId: string }
export type Binding = {
  memberCatalogId: string
  lookupCardinality?: 'many_to_one'
  keyMappings: {
    grainColumnId?: string | null
    populationColumnId?: string | null
    memberColumnId: string
  }[]
  selectedAttributeColumnIds: string[]
  unmatchedMemberPolicy?: 'reject'
}
export type Dimension = Semantic & {
  kind: 'dimension'
  memberCatalogId?: string | null
  memberCatalog?: DimensionMembers['definition'] | null
  isTimeDim?: boolean
  definition: {
    population: Population
    grain: { keyIds: string[]; columns: GrainColumn[] }
    populationAttributes?: PopulationAttribute[]
    memberBindings: Binding[]
    filters?: FilterDefinition | null
    duplicateKeyPolicy?: 'reject'
    nullKeyPolicy?: 'reject'
  }
}
export type ResolutionTarget = { memberCatalogId?: string | null; columnId: string }
export type ResolutionRule = {
  ruleId: string
  kind: 'column' | 'level_value'
  blankIsAbsent?: boolean
  sourceColumnId?: string | null
  target?: ResolutionTarget | null
  selectorColumnId?: string | null
  valueColumnId?: string | null
  selectors: { value: string; target: ResolutionTarget }[]
}
export type ResolutionDateRange = {
  memberCatalogId: string
  calendarKeyColumnId: string
  startColumnId: string
  endColumnId: string
}
export type CalendarResolution = {
  memberCatalogId: string
  startColumnId: string
  endColumnId: string
  selectedColumnIds: string[]
}
export type GrainResolution = {
  axes: {
    grainColumnId: string
    allowAll: boolean
    rules: ResolutionRule[]
    dateRange?: ResolutionDateRange | null
  }[]
  values: { sourceColumnId: string; mode: 'split' | 'copy'; timeMode?: 'split' | 'copy' | null }[]
  orderColumnIds: string[]
  partitionColumnIds: string[]
  passthroughColumnIds: string[]
  overlapPolicy: 'last_applied_wins'
}
export type Transaction = Semantic & {
  kind: 'transaction'
  effectiveGrainId?: string
  definition: {
    filters?: FilterDefinition | null
    sourceId: string
    dimensionId: string | null
    grainColumnIds?: string[]
    timeColumnId?: string | null
    mappingMode: 'direct' | 'resolve_multi_grain'
    resolution?: GrainResolution | null
    calendarResolution?: CalendarResolution | null
    pivot?: PivotStage | null
    keyMappings: { sourceColumnId: string; grainColumnId: string }[]
    expectedRowsPerGrain: 'zero_or_one' | 'many'
    unmatchedGrainPolicy: 'reject'
  }
}
export type Expression =
  | {
      type: 'count'
      function: 'count' | 'count_distinct' | 'count_if' | 'count_rows'
      arguments: Expression[]
    }
  | { type: 'measureRef'; measureId: string }
  | { type: 'grainRef'; grainColumnId: string }
  | {
      type: 'attributeRef'
      memberCatalogId?: string
      dimensionId?: string
      transactionId?: string
      columnId: string
    }
  | { type: 'literal'; value: number | string | boolean }
  | {
      type: 'binary'
      operator:
        'add' | 'subtract' | 'multiply' | 'divide' | 'eq' | 'ne' | 'gt' | 'gte' | 'lt' | 'lte'
      left: Expression
      right: Expression
    }
  | {
      type: 'call'
      function:
        | 'abs'
        | 'sqrt'
        | 'root'
        | 'power'
        | 'ceil'
        | 'floor'
        | 'rolling'
        | 'cumulative'
        | 'lag'
        | 'lead'
        | 'round'
        | 'coalesce'
        | 'min'
        | 'max'
        | 'if'
        | 'and'
        | 'or'
        | 'not'
        | 'concat'
        | 'upper'
        | 'lower'
        | 'trim'
        | 'date'
        | 'add_days'
        | 'days_between'
        | 'is_null'
      arguments: Expression[]
    }
export type Aggregation = {
  order?: 'nonTimeThenTime' | 'timeThenNonTime'
  nonTime:
    | 'sum'
    | 'avg'
    | 'min'
    | 'max'
    | 'count'
    | 'count_distinct'
    | 'single_value'
    | 'any_true'
    | 'all_true'
  time:
    | 'sum'
    | 'avg'
    | 'min'
    | 'max'
    | 'count'
    | 'count_distinct'
    | 'first'
    | 'last'
    | 'single_value'
    | 'any_true'
    | 'all_true'
  nullInputPolicy: 'ignore' | 'propagate'
  allNullResult: 'null' | 'zero'
}
export type MeasureCommon = {
  nativeGrainId: string
  resultType: {
    type: 'number' | 'integer' | 'decimal' | 'string' | 'date' | 'boolean'
    precision?: number | null
    scale?: number | null
    nullable: boolean
  }
  editable: boolean
  overflowPolicy?: 'error'
}
export type RefDefinition = MeasureCommon & {
  measureType: 'referenced'
  isConversionFactor?: boolean
  conversion?: {
    factorMeasureId: string
    operation: 'multiply' | 'divide'
    targetUnit: string
  } | null
  transactionId: string
  valueColumnId: string
  aggregation: Aggregation
  missingTransactionValue: 'null' | 'zero'
  sourceNullPolicy: 'preserve'
  allocation: {
    mode: 'none' | 'equal' | 'weighted' | 'weighted_by_self'
    basisMeasureId?: string | null
  } | null
}
export type GrainReference = { kind: 'transaction' | 'dimension'; objectId: string }
export type GrainOption = Omit<Dimension, 'kind'> & {
  kind: 'dimension' | 'transactionGrain'
  owner: GrainReference
}
export type CalcDefinition = MeasureCommon & {
  outputGrain?: GrainReference
  measureType: 'calculated'
  temporal?: {
    timeKeyId: string
    orderColumnId: string
    periodUnit: 'day' | 'week' | 'month' | 'index'
    partitionColumnIds: string[]
    resetColumnId?: string | null
    minimumPeriods?: number
  } | null
  calculationType: 'AggregateFirst' | 'CalculateFirst'
  formula: {
    displayText: string
    expression: Expression
    languageVersion?: 'planning-v1'
    astVersion?: 1 | 2
  }
  aggregation?: Aggregation | null
  allocation?: RefDefinition['allocation'] | null
  nullInputPolicy?: 'propagate'
}
export type Measure = Semantic & {
  formulaDefinition?: string
  kind: 'measure'
  business: { unit: string | null; aliases: string[] }
  definition: RefDefinition | CalcDefinition
}
export type ObjectInput = Source | DimensionMembers | Dimension | Transaction | Measure
export type Kind = ObjectInput['kind']
export type Envelope = {
  modelId: string
  revisionId: string
  version: number
  createdAt: string
  updatedAt: string
}
export type ObjectDocument = ObjectInput & Envelope
export type Model = {
  modelId: string
  name: string
  displayName: string
  description: string
  draftRevisionId: string | null
  publishedRevisionId: string | null
  version: number
  revisionCounter: number
}
export type Revision = {
  modelId: string
  revisionId: string
  version: number
  revisionNumber: number
  approvalStatus: 'draft' | 'published'
  metadataContractVersion?: number
  baseDimensionId: string | null
  changeReason: string
  parentRevisionId: string | null
}
export type Report = {
  valid: boolean
  diagnostics: { code: string; message: string; objectId?: string | null; path?: string | null }[]
}
export type Snapshot = {
  sources: (Source & Envelope)[]
  dimensionMembers: (DimensionMembers & Envelope)[]
  dimensions: (Dimension & Envelope)[]
  transactions: (Transaction & Envelope)[]
  measures: (Measure & Envelope)[]
}
export const emptySnapshot = (): Snapshot => ({
  sources: [],
  dimensionMembers: [],
  dimensions: [],
  transactions: [],
  measures: [],
})
export const resources: Record<Kind, string> = {
  source: 'sources',
  dimensionMembers: 'dimension-members',
  dimension: 'dimensions',
  transaction: 'transactions',
  measure: 'measures',
}
export const identity = (prefix: string): Identity => ({
  objectId: prefix + '_' + crypto.randomUUID().replaceAll('-', '').slice(0, 12),
  name: '',
  description: '',
})
export function inputOf<T extends ObjectInput>(document: T): T {
  const {
    modelId: _m,
    revisionId: _r,
    version: _v,
    createdAt: _c,
    updatedAt: _u,
    managed: _managed,
    ...input
  } = document as T & Partial<Envelope> & { managed?: ManagedTable }
  if (input.kind === 'transaction') delete (input as unknown as Transaction).effectiveGrainId
  if (input.kind === 'measure') {
    const measure = input as unknown as Measure
    const definition = { ...measure.definition } as Measure['definition'] & {
      nativeGrainId?: string
    }
    if (
      definition.measureType === 'calculated' &&
      !definition.outputGrain &&
      definition.nativeGrainId
    )
      definition.outputGrain = { kind: 'dimension', objectId: definition.nativeGrainId }
    const { nativeGrainId: _resolvedGrain, ...canonical } = definition
    return { ...measure, definition: canonical } as unknown as T
  }
  return input as T
}

export function effectiveSnapshot(data: Snapshot): Snapshot {
  return {
    ...data,
    sources: data.sources.map((source) =>
      source.managed
        ? {
            ...source,
            definition: {
              ...source.definition,
              columns: source.managed.definition.columns,
              keyColumns: source.managed.definition.primaryKeyColumnIds,
            },
          }
        : source,
    ),
  }
}

export type FilterCondition = {
  type: 'condition'
  columnId: string
  operator:
    | 'eq'
    | 'ne'
    | 'gt'
    | 'gte'
    | 'lt'
    | 'lte'
    | 'in'
    | 'not_in'
    | 'between'
    | 'not_between'
    | 'contains'
    | 'not_contains'
    | 'starts_with'
    | 'not_starts_with'
    | 'ends_with'
    | 'not_ends_with'
    | 'is_empty'
    | 'is_not_empty'
    | 'is_null'
    | 'is_not_null'
  values: (string | number | boolean)[]
}
export type FilterGroup = { type: 'group'; combinator: 'and' | 'or'; children: FilterNode[] }
export type FilterNode = FilterCondition | FilterGroup
export type FilterDefinition = { root: FilterGroup }
