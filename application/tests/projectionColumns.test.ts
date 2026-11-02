import { expect, it } from 'vitest'
import { projectionAttributes } from '../src/metadata/projectionColumns'
import type { Dimension, PlanningLevel } from '../src/metadata/types'

it('hides parent grain aliases and uses dimension display names for business attributes', () => {
  const parent = {
    definition: { grain: { keyIds: ['product'] }, populationAttributes: [] },
  } as unknown as PlanningLevel
  const dimension = {
    objectId: 'Product',
    definition: { columns: [{ columnId: 'brand', displayName: 'Brand' }] },
  } as unknown as Dimension
  const options = projectionAttributes(
    parent,
    [
      {
        columnId: 'product',
        name: 'Forecast_Intersection__product_id',
        physicalType: 'String',
        role: 'key',
      },
      {
        columnId: 'brand_output',
        name: 'Product__Brand',
        physicalType: 'String',
        role: 'attribute',
        lineage: { kind: 'dimension', dimensionId: 'Product', columnId: 'brand' },
      },
      { columnId: 'FQTY', name: 'FQTY', physicalType: 'Int64', role: 'measure' },
    ],
    [dimension],
  )
  expect(options).toEqual([{ value: 'brand_output', label: 'Brand' }])
})
