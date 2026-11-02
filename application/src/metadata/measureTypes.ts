import type { MeasureCommon } from './types'
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
