import type { Expression, Measure, MeasureCommon } from './types'
import type { FormulaAttribute } from './formula'
export function formulaResultType(
  node: Expression,
  measures: Measure[],
  attributes: FormulaAttribute[],
): MeasureCommon['resultType']['type'] {
  const infer = (child: Expression) => formulaResultType(child, measures, attributes)
  if (node.type === 'count') return 'number'
  if (node.type === 'literal')
    return typeof node.value === 'boolean'
      ? 'boolean'
      : typeof node.value === 'string'
        ? 'string'
        : 'number'
  if (node.type === 'measureRef')
    return (
      measures.find((measure) => measure.objectId === node.measureId)?.definition.resultType.type ??
      'number'
    )
  if (node.type === 'attributeRef')
    return (attributes.find(
      (attribute) =>
        attribute.key === (node.dimensionId ?? node.planningLevelId) + '.' + node.columnId,
    )?.valueType ?? 'number') as MeasureCommon['resultType']['type']
  if (node.type === 'grainRef') return 'number'
  if (node.type === 'binary')
    return ['eq', 'ne', 'gt', 'gte', 'lt', 'lte'].includes(node.operator) ? 'boolean' : 'number'
  if (['and', 'or', 'not', 'is_null'].includes(node.function)) return 'boolean'
  if (['concat', 'upper', 'lower', 'trim'].includes(node.function)) return 'string'
  if (['date', 'add_days'].includes(node.function)) return 'date'
  if (node.function === 'days_between') return 'number'
  return infer(node.arguments[node.function === 'if' ? 1 : 0])
}
