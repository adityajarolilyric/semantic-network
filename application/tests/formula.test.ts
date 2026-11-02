import { describe, expect, it } from 'vitest'
import { formatFormula, parseFormula, formulaUnit } from '../src/metadata/formula'
const measures = [
  { objectId: 'm_fqty', name: 'FQTY' },
  { objectId: 'm_sqty', name: 'SQTY' },
]
describe('semantic formula grammar', () => {
  it('binds names to stable references with arithmetic precedence', () => {
    expect(parseFormula('ABS(FQTY-SQTY*2)', measures)).toEqual({
      type: 'call',
      function: 'abs',
      arguments: [
        {
          type: 'binary',
          operator: 'subtract',
          left: { type: 'measureRef', measureId: 'm_fqty' },
          right: {
            type: 'binary',
            operator: 'multiply',
            left: { type: 'measureRef', measureId: 'm_sqty' },
            right: { type: 'literal', value: 2 },
          },
        },
      ],
    })
  })
  it('round-trips persisted AST independently of display name changes', () => {
    const e = parseFormula('ROUND(([m_fqty] - [m_sqty]) / 3, 2)', measures)
    expect(
      parseFormula(
        formatFormula(e),
        measures.map((m) => ({ ...m, name: 'Renamed' })),
      ),
    ).toEqual(e)
  })
  it('supports constants, unary minus, exponent literals and numeric grain references', () => {
    expect(parseFormula('-1.25e2 + GRAIN([key_1])', []).type).toBe('binary')
  })
  it.each([
    'FQTY; DROP TABLE Sales',
    'window.alert(1)',
    'UNKNOWN + 1',
    'ABS(',
    '1 2',
    '1e999',
    '1 +',
    ' '.repeat(4),
  ])('rejects invalid input %s', (text) => expect(() => parseFormula(text, measures)).toThrow())
  it('rejects ambiguous names and bounds nesting', () => {
    expect(() =>
      parseFormula('FQTY', [...measures, { objectId: 'm_other', name: 'FQTY' }]),
    ).toThrow('ambiguous')
    expect(() => parseFormula('('.repeat(34) + '1' + ')'.repeat(34), [])).toThrow('nesting')
  })
})

it('infers hidden units from existing dependencies, including coalesce after a constant', () => {
  const inputs = measures.map((m) => ({ ...m, business: { unit: 'EA', aliases: [] } }))
  expect(formulaUnit(parseFormula('ABS(FQTY-SQTY)', inputs), inputs)).toBe('EA')
  expect(formulaUnit(parseFormula('COALESCE(0,FQTY)', inputs), inputs)).toBe('EA')
  expect(formulaUnit(parseFormula('FQTY/SQTY', inputs), inputs)).toBe('1')
})
