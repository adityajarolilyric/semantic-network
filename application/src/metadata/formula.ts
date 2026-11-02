import type { Expression, Measure } from './types'
// Small semantic grammar, never JavaScript or SQL execution.
export function parseFormula(
  text: string,
  measures: Pick<Measure, 'objectId' | 'name'>[],
  grainColumns: { grainColumnId: string; name: string }[] = [],
): Expression {
  if (!text.trim() || text.length > 4000) throw new Error('Enter a formula of 1–4,000 characters.')
  const tokens: { value: string; id: boolean }[] = []
  const pattern =
    /\s*(?:\[([^\[\]\r\n]+)\]|([A-Za-z_][A-Za-z0-9_]*|(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?|[()+*/,\-]))/gy
  let pos = 0
  while (pos < text.length) {
    if (!text.slice(pos).trim()) break
    pattern.lastIndex = pos
    const match = pattern.exec(text)
    if (!match) throw new Error('Unexpected character near ' + text.slice(pos, pos + 20))
    tokens.push({ value: match[1] ?? match[2], id: !!match[1] })
    pos = pattern.lastIndex
  }
  let i = 0
  let depth = 0
  const peek = () => tokens[i]?.value
  const take = (expected: string) => {
    if (peek() !== expected) throw new Error('Expected ' + expected)
    i++
  }
  function atom(): Expression {
    if (++depth > 32) throw new Error('Formula nesting exceeds 32 levels.')
    try {
      if (peek() === '-') {
        i++
        return {
          type: 'binary',
          operator: 'subtract',
          left: { type: 'literal', value: 0 },
          right: atom(),
        }
      }
      if (peek() === '(') {
        i++
        const e = sum()
        take(')')
        return e
      }
      const t = tokens[i++]
      if (!t) throw new Error('Formula is incomplete.')
      if (!t.id && /^\d|^\./.test(t.value)) {
        const value = Number(t.value)
        if (!Number.isFinite(value)) throw new Error('Numeric literal must be finite.')
        return { type: 'literal', value }
      }
      if (!t.id && peek() === '(') {
        i++
        if (t.value.toLowerCase() === 'grain') {
          const id = tokens[i++]?.value
          if (!id) throw new Error('GRAIN needs a column name.')
          take(')')
          const columns = grainColumns.filter((c) => c.name === id || c.grainColumnId === id)
          if (grainColumns.length && columns.length !== 1)
            throw new Error('Choose a unique grain column name.')
          return { type: 'grainRef', grainColumnId: columns[0]?.grainColumnId ?? id }
        }
        const fn = t.value.toLowerCase()
        if (!['abs', 'round', 'coalesce', 'min', 'max'].includes(fn))
          throw new Error('Unsupported function ' + t.value)
        const args: Expression[] = []
        args.push(sum())
        while (peek() === ',') {
          i++
          args.push(sum())
        }
        take(')')
        if (args.length > 16) throw new Error('Functions accept at most 16 arguments.')
        return { type: 'call', function: fn as 'abs', arguments: args }
      }
      const candidates = measures.filter((m) => m.name === t.value || m.objectId === t.value)
      if (candidates.length !== 1)
        throw new Error(
          'Unknown or ambiguous measure ' + t.value + '. Choose a unique measure name.',
        )
      return { type: 'measureRef', measureId: candidates[0].objectId }
    } finally {
      depth--
    }
  }
  function product(): Expression {
    let e = atom()
    while (peek() === '*' || peek() === '/') {
      const op = peek()
      i++
      e = { type: 'binary', operator: op === '*' ? 'multiply' : 'divide', left: e, right: atom() }
    }
    return e
  }
  function sum(): Expression {
    let e = product()
    while (peek() === '+' || peek() === '-') {
      const op = peek()
      i++
      e = { type: 'binary', operator: op === '+' ? 'add' : 'subtract', left: e, right: product() }
    }
    return e
  }
  const expression = sum()
  if (i !== tokens.length) throw new Error('Unexpected token ' + peek())
  return expression
}
export function formatFormula(
  e: Expression,
  measures: Pick<Measure, 'objectId' | 'name'>[] = [],
  grainColumns: { grainColumnId: string; name: string }[] = [],
): string {
  const format = (node: Expression): string => formatFormula(node, measures, grainColumns)
  if (e.type === 'measureRef') {
    const name = measures.find((m) => m.objectId === e.measureId)?.name ?? e.measureId
    return `[${name}]`
  }
  if (e.type === 'grainRef')
    return `GRAIN([${grainColumns.find((c) => c.grainColumnId === e.grainColumnId)?.name ?? e.grainColumnId}])`
  if (e.type === 'literal') return String(e.value)
  if (e.type === 'call')
    return e.function.toUpperCase() + '(' + e.arguments.map(format).join(', ') + ')'
  return (
    '(' +
    format(e.left) +
    ' ' +
    { add: '+', subtract: '-', multiply: '*', divide: '/' }[e.operator] +
    ' ' +
    format(e.right) +
    ')'
  )
}

/** Infer hidden business-unit metadata; the backend validates compatibility. */
export function formulaUnit(
  expression: Expression,
  measures: Pick<Measure, 'objectId' | 'business'>[],
): string | null {
  const unit = (node: Expression): string | null => {
    if (node.type === 'measureRef')
      return measures.find((m) => m.objectId === node.measureId)?.business.unit ?? null
    if (node.type === 'literal' || node.type === 'grainRef') return null
    if (node.type === 'call')
      return node.arguments.map(unit).find((value) => value !== null) ?? null
    const left = unit(node.left),
      right = unit(node.right)
    if (node.operator === 'add' || node.operator === 'subtract') return left ?? right
    if (node.operator === 'multiply') return left && right ? `${left}*${right}` : (left ?? right)
    return left && right
      ? left === right
        ? '1'
        : `${left}/${right}`
      : (left ?? (right ? `1/${right}` : null))
  }
  return unit(expression)
}
