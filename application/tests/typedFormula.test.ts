import { expect, it } from 'vitest'
import { parseFormula, formatFormula, type FormulaAttribute } from '../src/metadata/formula'
import { formulaResultType } from '../src/metadata/formulaResultType'
import { aggregationOptions, typedResultType } from '../src/metadata/measureTypes'
const attributes: FormulaAttribute[] = [
  {
    label: 'Product.Brand',
    key: 'product.brand',
    reference: { type: 'attributeRef', memberCatalogId: 'product', columnId: 'brand' },
    valueType: 'string',
  },
]
it('round-trips typed formulas, escaped strings, comparisons and attributes', () => {
  const text = 'IF(ATTR([Product.Brand]) = "A\\\"B", TRUE, FALSE)'
  const expression = parseFormula(text, [], [], attributes)
  expect(formulaResultType(expression, [], attributes)).toBe('boolean')
  expect(parseFormula(formatFormula(expression, [], [], attributes), [], [], attributes)).toEqual(
    expression,
  )
  expect(parseFormula('ADD_DAYS(DATE("2026-11-05"), 2)', [])).toMatchObject({
    type: 'call',
    function: 'add_days',
  })
  expect(() => parseFormula('ATTR([Unknown.Brand])', [], [], attributes)).toThrow()
})
it('limits policy choices by type and preserves numeric source normalization', () => {
  expect(typedResultType('Nullable(String)')).toEqual({ type: 'string', nullable: true })
  expect(typedResultType('UInt64')).toEqual({ type: 'number', nullable: true })
  expect(typedResultType('Date32')?.type).toBe('date')
  expect(aggregationOptions('string').map((option) => option.value)).toEqual(['single_value'])
  expect(aggregationOptions('boolean').map((option) => option.value)).toEqual([
    'single_value',
    'any_true',
    'all_true',
  ])
  expect(aggregationOptions('date', true).map((option) => option.value)).toEqual([
    'single_value',
    'min',
    'max',
    'first',
    'last',
  ])
})
