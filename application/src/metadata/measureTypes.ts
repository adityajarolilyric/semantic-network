import type { Aggregation, MeasureCommon } from './types'
export function referencedResultType(
  physicalType: string,
  nullable: boolean,
): MeasureCommon['resultType'] | null {
  let type = physicalType.replace(/\s/g, '').toLowerCase()
  while (/^(nullable|lowcardinality)\(/.test(type) && type.endsWith(')'))
    type = type.slice(type.indexOf('(') + 1, -1)
  if (/^u?int(8|16|32|64|128|256)$/.test(type) || /^float(32|64)$/.test(type))
    return { type: 'number', nullable }
  const decimal = /^decimal\((\d+),(\d+)\)$/.exec(type)
  const sized = /^decimal(32|64|128|256)\((\d+)\)$/.exec(type)
  const precision = decimal
    ? Number(decimal[1])
    : sized
      ? ({ '32': 9, '64': 18, '128': 38, '256': 76 } as Record<string, number>)[sized[1]]
      : 0
  const scale = decimal ? Number(decimal[2]) : sized ? Number(sized[2]) : -1
  return precision >= 1 && precision <= 76 && scale >= 0 && scale <= precision
    ? { type: 'number', nullable }
    : null
}

export function typedResultType(
  physicalType: string,
  nullable = true,
): MeasureCommon['resultType'] | null {
  let type = physicalType.replace(/\s/g, '').toLowerCase()
  while (/^(nullable|lowcardinality)\(/.test(type) && type.endsWith(')'))
    type = type.slice(type.indexOf('(') + 1, -1)
  if (type === 'string' || type.startsWith('fixedstring(')) return { type: 'string', nullable }
  if (type === 'bool' || type === 'boolean') return { type: 'boolean', nullable }
  if (type === 'date' || type === 'date32') return { type: 'date', nullable }
  return referencedResultType(physicalType, nullable)
}
export const numericType = (type: string) => ['number', 'integer', 'decimal'].includes(type)
export function aggregationOptions(type: string, time = false) {
  const options = numericType(type)
    ? ['sum', 'avg', 'min', 'max', 'count', 'count_distinct']
    : type === 'boolean'
      ? ['single_value', 'any_true', 'all_true']
      : type === 'date'
        ? ['single_value', 'min', 'max']
        : ['single_value']
  const labels: Record<string, string> = {
    single_value: type === 'string' ? 'None' : 'Same value only',
    any_true: 'Any true',
    all_true: 'All true',
  }
  if (type === 'date') {
    labels.min = 'Earliest date'
    labels.max = 'Latest date'
  }
  return [...options, ...(time ? ['first', 'last'] : [])].map((value) => ({
    value,
    label: labels[value] ?? value,
  }))
}

/** One policy for typed values; numeric measures retain independent axes. */
export function aggregationForType(policy: Aggregation, kind: string): Aggregation {
  const nonTimeOptions = aggregationOptions(kind)
  const timeOptions = aggregationOptions(kind, true)
  const nonTime = (
    nonTimeOptions.some((option) => option.value === policy.nonTime)
      ? policy.nonTime
      : nonTimeOptions[0].value
  ) as Aggregation['nonTime']
  return {
    ...policy,
    nonTime,
    time: !numericType(kind)
      ? nonTime
      : ((timeOptions.some((option) => option.value === policy.time)
          ? policy.time
          : timeOptions[0].value) as Aggregation['time']),
    allNullResult: numericType(kind) ? policy.allNullResult : 'null',
  }
}
