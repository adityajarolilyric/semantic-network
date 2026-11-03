/** Display-only decimal rounding, without losing precision through Number(). */
export function formatNumericDisplay(value: string): string {
  const match = /^([+-]?)(\d*)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/.exec(value)
  if (!match || !(match[2] + (match[3] ?? '')).length) return value
  const exponent = Number(match[4] ?? 0)
  if (!Number.isSafeInteger(exponent) || Math.abs(exponent) > 4000) return value
  const digits = (match[2] || '0') + (match[3] ?? '')
  const point = (match[2] || '0').length + exponent
  const integer =
    point <= 0
      ? '0'
      : point >= digits.length
        ? digits + '0'.repeat(point - digits.length)
        : digits.slice(0, point)
  const fraction = point <= 0 ? '0'.repeat(-point) + digits : digits.slice(point)
  let cents = BigInt((integer + fraction.padEnd(2, '0').slice(0, 2)).replace(/^0+(?=\d)/, ''))
  if ((fraction[2] ?? '0') >= '5') cents += 1n
  const rounded = cents.toString().padStart(3, '0')
  return (
    (match[1] === '-' && cents !== 0n ? '-' : '') + rounded.slice(0, -2) + '.' + rounded.slice(-2)
  )
}
