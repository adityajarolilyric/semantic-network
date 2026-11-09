import {
  emptySnapshot,
  type Dimension,
  type Envelope,
  type Population,
  type Snapshot,
} from '../../src/metadata/types'
const envelope: Envelope = {
  modelId: 'model',
  revisionId: 'revision',
  version: 1,
  createdAt: '',
  updatedAt: '',
}
const identity = (objectId: string, name: string) => ({
  ...envelope,
  objectId,
  name,
  displayName: name,
  description: '',
})
const column = { columnId: 'id', name: 'product_id', physicalType: 'String', nullable: false }
const qtyColumn = { columnId: 'qty', name: 'forecast_qty', physicalType: 'Float64', nullable: true }
const makeDimension = (id: string, name: string, population: Population): Dimension & Envelope => ({
  ...identity(id, name),
  kind: 'dimension',
  definition: {
    population,
    grain: {
      keyIds: ['key'],
      columns: [{ grainColumnId: 'key', name: 'product_id', populationRef: { columnId: 'id' } }],
    },
    memberBindings: [],
  },
})
export function contextGraphFixture(): Snapshot {
  const data = emptySnapshot()
  data.sources = ['products', 'intersections', 'forecast', 'sales', 'calendar'].map((id) => ({
    ...identity(id, id),
    kind: 'source',
    definition: { tableName: id, inScope: true, keyColumns: [], columns: [column, qtyColumn] },
  }))
  const product = makeDimension('product', 'Product', { operation: 'source', sourceId: 'products' })
  product.memberCatalogId = 'product-members'
  product.memberCatalog = {
    sourceId: 'products',
    columns: [
      {
        columnId: 'product-key',
        name: 'product_id',
        displayName: 'Product ID',
        sourceColumnId: 'id',
      },
    ],
    keyColumnIds: ['product-key'],
    labelColumnId: 'product-key',
    attributeColumnIds: [],
    time: null,
  }
  const base = makeDimension('base', 'Product Location Week', {
    operation: 'cartesian_product',
    factors: [
      { factorId: 'pl', sourceId: 'intersections', selectedColumnIds: ['id'] },
      { factorId: 'time', sourceId: 'calendar', selectedColumnIds: ['id'] },
    ],
  })
  base.definition.memberBindings = [
    {
      memberCatalogId: 'product-members',
      keyMappings: [{ grainColumnId: 'key', memberColumnId: 'product-key' }],
      selectedAttributeColumnIds: [],
    },
  ]
  const derived = makeDimension('derived', 'Brand Zone Week', {
    operation: 'distinct_projection',
    sourceDimensionId: 'base',
  })
  data.dimensions = [product, base, derived]
  data.dimensionMembers = [
    {
      ...identity('product-members', 'Product'),
      kind: 'dimensionMembers',
      isTimeDim: false,
      definition: product.memberCatalog,
    },
  ]
  data.transactions = [
    ['forecast-tx', 'Forecast', 'forecast', 'base'],
    ['sales-tx', 'Sales', 'sales', 'derived'],
    ['standalone', 'Independent Orders', 'sales', null],
  ].map(([id, name, source, dimension]) => ({
    ...identity(id!, name!),
    kind: 'transaction',
    effectiveGrainId: dimension || 'independent-context',
    definition: {
      sourceId: source!,
      dimensionId: dimension,
      mappingMode: 'direct',
      grainColumnIds: ['id'],
      keyMappings: [{ sourceColumnId: 'id', grainColumnId: 'key' }],
      expectedRowsPerGrain: 'many',
      unmatchedGrainPolicy: 'reject',
    },
  }))
  data.measures = [
    ['fqty', 'Forecast Qty', 'forecast-tx'],
    ['sqty', 'Sales Qty', 'sales-tx'],
    ['orders', 'Order Qty', 'standalone'],
  ].map(([id, name, tx]) => ({
    ...identity(id, name),
    kind: 'measure',
    business: { unit: null, aliases: [] },
    definition: {
      measureType: 'referenced',
      transactionId: tx,
      valueColumnId: 'qty',
      nativeGrainId: 'stale-context',
      editable: false,
      resultType: { type: 'number', nullable: true },
      aggregation: {
        nonTime: 'sum',
        time: 'sum',
        nullInputPolicy: 'ignore',
        allNullResult: 'null',
      },
      allocation: { mode: 'weighted_by_self', basisMeasureId: null },
      sourceNullPolicy: 'preserve',
      missingTransactionValue: 'null',
    },
  }))
  data.measures.push({
    ...identity('error', 'Absolute Error'),
    kind: 'measure',
    business: { unit: null, aliases: [] },
    definition: {
      measureType: 'calculated',
      outputGrain: { kind: 'dimension', objectId: 'derived' },
      nativeGrainId: 'stale-context',
      editable: false,
      calculationType: 'AggregateFirst',
      resultType: { type: 'number', nullable: true },
      formula: {
        displayText: 'ABS([Forecast Qty] - [Sales Qty])',
        expression: {
          type: 'call',
          function: 'abs',
          arguments: [
            {
              type: 'binary',
              operator: 'subtract',
              left: { type: 'measureRef', measureId: 'fqty' },
              right: { type: 'measureRef', measureId: 'sqty' },
            },
          ],
        },
      },
    },
  })
  return data
}
