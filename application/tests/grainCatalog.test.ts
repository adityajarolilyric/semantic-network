import { expect, it } from 'vitest'
import {
  grainCatalog,
  materialisationCatalog,
  withResolvedGrains,
} from '../src/metadata/grainCatalog'
import { useGrainCatalog } from '../src/metadata/useGrainCatalog'
import {
  emptySnapshot,
  type Dimension,
  type Snapshot,
  type Envelope,
  type Transaction,
} from '../src/metadata/types'

const envelope: Envelope = {
  modelId: 'm',
  revisionId: 'r',
  version: 1,
  createdAt: '',
  updatedAt: '',
}

function data() {
  const snapshot = emptySnapshot()
  snapshot.dimensions = ['ProdLocWeek', 'BrandZoneWeek', 'UnusedLookup'].map(
    (name) =>
      ({
        ...envelope,
        objectId: name,
        kind: 'dimension',
        name,
        displayName: name,
        description: '',
        definition: {
          population: { operation: 'source', sourceId: 'source' },
          grain: {
            keyIds: ['key'],
            columns: [{ grainColumnId: 'key', name: 'key', populationRef: { columnId: 'col_1' } }],
          },
          memberBindings: [],
        },
      }) as Dimension & Envelope,
  )
  snapshot.transactions = (
    [
      {
        objectId: 'sales',
        kind: 'transaction',
        name: 'sales_history',
        displayName: 'sales_history',
        description: '',
        effectiveGrainId: 'BrandZoneWeek',
        definition: {
          sourceId: 'source',
          dimensionId: 'BrandZoneWeek',
          grainColumnIds: ['col_1'],
          mappingMode: 'direct',
          keyMappings: [{ sourceColumnId: 'col_1', grainColumnId: 'key' }],
        },
      },
      {
        objectId: 'forecast_brand',
        kind: 'transaction',
        name: 'forecast_brand_zone',
        displayName: 'forecast_brand_zone',
        description: '',
        effectiveGrainId: 'BrandZoneWeek',
        definition: {
          sourceId: 'source',
          dimensionId: 'BrandZoneWeek',
          grainColumnIds: ['col_1'],
          mappingMode: 'direct',
          keyMappings: [{ sourceColumnId: 'col_1', grainColumnId: 'key' }],
        },
      },
      {
        objectId: 'forecast',
        kind: 'transaction',
        name: 'Forecast',
        displayName: 'Forecast',
        description: '',
        effectiveGrainId: 'tg_forecast',
        definition: {
          sourceId: 'source',
          dimensionId: null,
          grainColumnIds: ['col_1'],
          mappingMode: 'direct',
          keyMappings: [],
        },
      },
    ] as Transaction[]
  ).map((transaction) => ({ ...transaction, ...envelope }))
  return snapshot
}

it('shows each bound Dimension once under its own name, without Transaction aliases', () => {
  const catalog = grainCatalog(data())
  expect(
    catalog.filter((grain) => grain.objectId === 'BrandZoneWeek').map((grain) => grain.name),
  ).toEqual(['BrandZoneWeek'])
  expect(catalog.some((grain) => grain.name.includes('sales_history'))).toBe(false)
  expect(catalog.some((grain) => grain.name.includes('forecast_brand_zone'))).toBe(false)
  expect(catalog.some((grain) => grain.objectId === 'tg_forecast')).toBe(true)
})

it('previews only actual representation targets and adds Base when the user selects it', () => {
  const snapshot = data()
  expect(materialisationCatalog(snapshot, null).map((grain) => grain.name)).toEqual([
    'BrandZoneWeek',
    'Forecast · Transaction grain',
  ])
  expect(materialisationCatalog(snapshot, 'ProdLocWeek').map((grain) => grain.name)).toEqual([
    'ProdLocWeek',
    'BrandZoneWeek',
    'Forecast · Transaction grain',
  ])
})

it('keeps existing Transaction-owned calculations editable through the canonical Dimension option', async () => {
  const catalog = useGrainCatalog(data())
  expect(catalog.selection('BrandZoneWeek', { kind: 'transaction', objectId: 'sales' })).toBe(
    'dimension:BrandZoneWeek',
  )
  expect((await catalog.resolve('transaction:sales')).name).toBe('BrandZoneWeek')
  expect(catalog.items.some((item) => item.label.includes('sales_history'))).toBe(false)
})

it('resolves a bound Dimension before a stale standalone effective-grain value', () => {
  const snapshot = data()
  snapshot.transactions[0].effectiveGrainId = 'old_standalone'
  snapshot.measures = [
    {
      ...envelope,
      objectId: 'qty',
      kind: 'measure',
      definition: {
        measureType: 'referenced',
        transactionId: 'sales',
        nativeGrainId: 'old_standalone',
      },
    } as Snapshot['measures'][number],
  ]
  expect(withResolvedGrains(snapshot).measures[0].definition.nativeGrainId).toBe('BrandZoneWeek')
})
