import { expect, it } from 'vitest'
import { dimensionKeyOptions } from '../src/metadata/dimensionKeyOptions'
import {
  emptySnapshot,
  type Dimension,
  type PlanningLevel,
  type Source,
} from '../src/metadata/types'
const envelope = { modelId: 'm', revisionId: 'r', version: 1, createdAt: '', updatedAt: '' }
const source = (id: string, names: string[]): Source => ({
  objectId: id,
  name: id,
  kind: 'source',
  description: '',
  definition: {
    tableName: id,
    inScope: true,
    keyColumns: [],
    columns: names.map((name, i) => ({
      columnId: 'col_' + i,
      name,
      physicalType: 'String',
      nullable: false,
    })),
  },
})
const product: Dimension = {
  objectId: 'product',
  kind: 'dimension',
  name: 'Product',
  displayName: 'Product',
  description: '',
  isTimeDim: false,
  definition: {
    sourceId: 'products',
    columns: [
      {
        columnId: 'product_key',
        sourceColumnId: 'col_0',
        name: 'ProductKey',
        displayName: 'Product ID',
      },
    ],
    keyColumnIds: ['product_key'],
    labelColumnId: 'product_key',
    attributeColumnIds: [],
    time: null,
  },
}
function fixture() {
  const data = emptySnapshot()
  data.sources = [
    source('products', ['product_id']),
    source('matrix', ['product_id', 'location_id']),
    source('dates', ['date']),
  ].map((s) => ({ ...s, ...envelope }))
  const level: PlanningLevel = {
    objectId: 'plt',
    kind: 'planningLevel',
    name: 'PLT',
    displayName: 'PLT',
    description: '',
    definition: {
      population: {
        operation: 'cartesian_product',
        factors: [
          { factorId: 'matrix', sourceId: 'matrix', selectedColumnIds: ['col_0', 'col_1'] },
          { factorId: 'time', sourceId: 'dates', selectedColumnIds: ['col_0'] },
        ],
      },
      grain: {
        keyIds: ['p', 'l', 't'],
        columns: [
          {
            grainColumnId: 'p',
            name: 'Product',
            populationRef: { factorId: 'matrix', columnId: 'col_0' },
          },
          {
            grainColumnId: 'l',
            name: 'Location',
            populationRef: { factorId: 'matrix', columnId: 'col_1' },
          },
          {
            grainColumnId: 't',
            name: 'Time',
            populationRef: { factorId: 'time', columnId: 'col_0' },
          },
        ],
      },
      dimensionBindings: [],
    },
  }
  return { data, level }
}
it('matches a matrix product key without confusing IDs reused by location and time sources', () => {
  const { data, level } = fixture()
  expect(dimensionKeyOptions(level, product, 'product_key', data)).toEqual([
    { value: 'p', label: 'Product' },
  ])
  expect(dimensionKeyOptions(level, product, 'missing', data)).toEqual([])
})
it('filters composite key controls independently and rejects mismatched types', () => {
  const { data, level } = fixture()
  const composite: Dimension = {
    ...product,
    definition: {
      ...product.definition,
      sourceId: 'matrix',
      columns: [
        { columnId: 'p', sourceColumnId: 'col_0', name: 'Product', displayName: 'Product' },
        { columnId: 'l', sourceColumnId: 'col_1', name: 'Location', displayName: 'Location' },
      ],
      keyColumnIds: ['p', 'l'],
    },
  }
  expect(dimensionKeyOptions(level, composite, 'l', data)).toEqual([
    { value: 'l', label: 'Location' },
  ])
  data.sources[0].definition.columns[0].physicalType = 'Int64'
  expect(dimensionKeyOptions(level, product, 'product_key', data)).toEqual([])
})
it('uses explicit parent bindings through multiple projections even when physical names differ', () => {
  const { data, level } = fixture()
  data.sources[1].definition.columns[0].name = 'item_reference'
  data.sources[2].definition.columns[0].name = 'product_id'
  level.definition.dimensionBindings = [
    {
      dimensionId: 'product',
      keyMappings: [{ dimensionColumnId: 'product_key', grainColumnId: 'p' }],
      selectedAttributeColumnIds: [],
    },
  ]
  data.planningLevels = [{ ...level, ...envelope }]
  const child: PlanningLevel = {
    ...level,
    objectId: 'pt',
    definition: {
      population: { operation: 'distinct_projection', sourcePlanningLevelId: 'plt' },
      grain: {
        keyIds: ['cp', 'ct'],
        columns: [
          { grainColumnId: 'cp', name: 'Product', populationRef: { sourceGrainColumnId: 'p' } },
          { grainColumnId: 'ct', name: 'Time', populationRef: { sourceGrainColumnId: 't' } },
        ],
      },
      dimensionBindings: [],
    },
  }
  data.planningLevels.push({ ...child, ...envelope })
  const grandchild: PlanningLevel = {
    ...child,
    objectId: 'p2',
    definition: {
      ...child.definition,
      population: { operation: 'distinct_projection', sourcePlanningLevelId: 'pt' },
      grain: {
        keyIds: ['gp', 'gt'],
        columns: [
          { grainColumnId: 'gp', name: 'Product', populationRef: { sourceGrainColumnId: 'cp' } },
          { grainColumnId: 'gt', name: 'Time', populationRef: { sourceGrainColumnId: 'ct' } },
        ],
      },
    },
  }
  expect(dimensionKeyOptions(grandchild, product, 'product_key', data)).toEqual([
    { value: 'gp', label: 'Product' },
  ])
})
it('handles source population and missing/cyclic lineage without offering unrelated columns', () => {
  const { data, level } = fixture()
  const direct: PlanningLevel = {
    ...level,
    definition: {
      ...level.definition,
      population: { operation: 'source', sourceId: 'products' },
      grain: {
        keyIds: ['p'],
        columns: [{ grainColumnId: 'p', name: 'Product', populationRef: { columnId: 'col_0' } }],
      },
    },
  }
  expect(dimensionKeyOptions(direct, product, 'product_key', data)).toEqual([
    { value: 'p', label: 'Product' },
  ])
  const cyclic: PlanningLevel = {
    ...direct,
    definition: {
      ...direct.definition,
      population: { operation: 'distinct_projection', sourcePlanningLevelId: 'plt' },
      grain: {
        keyIds: ['p'],
        columns: [
          { grainColumnId: 'p', name: 'Product', populationRef: { sourceGrainColumnId: 'p' } },
        ],
      },
    },
  }
  data.planningLevels = [{ ...cyclic, ...envelope }]
  expect(dimensionKeyOptions(cyclic, product, 'product_key', data)).toEqual([])
})

it('does not offer another dimension’s generic id key just because its name and type match', () => {
  const { data, level } = fixture()
  data.sources[0].definition.columns[0].name = 'id'
  data.sources[2].definition.columns[0].name = 'id'
  data.dimensions = [
    { ...product, ...envelope },
    {
      ...product,
      ...envelope,
      objectId: 'other_dimension',
      name: 'Other',
      definition: { ...product.definition, sourceId: 'dates' },
    },
  ]
  const population: PlanningLevel = {
    ...level,
    definition: {
      ...level.definition,
      population: {
        operation: 'cartesian_product',
        factors: [
          { factorId: 'own', sourceId: 'products', selectedColumnIds: ['col_0'] },
          { factorId: 'other', sourceId: 'dates', selectedColumnIds: ['col_0'] },
        ],
      },
      grain: {
        keyIds: ['own', 'other'],
        columns: [
          {
            grainColumnId: 'own',
            name: 'Product ID',
            populationRef: { factorId: 'own', columnId: 'col_0' },
          },
          {
            grainColumnId: 'other',
            name: 'Other ID',
            populationRef: { factorId: 'other', columnId: 'col_0' },
          },
        ],
      },
    },
  }
  expect(dimensionKeyOptions(population, product, 'product_key', data)).toEqual([
    { value: 'own', label: 'Product ID' },
  ])
})
