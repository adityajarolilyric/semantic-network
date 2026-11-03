import type { FilterCondition, FilterDefinition, FilterGroup, FilterNode } from '../types'
import { TransformationSection } from './TransformationSection'
import { Filter, Plus, Trash2, X } from 'lucide-react'

export type FilterColumn = { columnId: string; name: string; physicalType?: string | null }
type Kind = 'string' | 'number' | 'boolean' | 'date' | 'datetime'
export function filterKind(type = ''): Kind {
  if (/Bool/.test(type)) return 'boolean'
  if (/Int|Float|Decimal/.test(type)) return 'number'
  if (/DateTime/.test(type)) return 'datetime'
  if (/Date/.test(type)) return 'date'
  return 'string'
}
const comparison = [
  ['eq', 'Equals'],
  ['ne', 'Does not equal'],
  ['in', 'Is one of'],
  ['not_in', 'Is not one of'],
]
const ordered = [
  ['gt', 'Greater than'],
  ['gte', 'Greater than or equal'],
  ['lt', 'Less than'],
  ['lte', 'Less than or equal'],
  ['between', 'Between (inclusive)'],
  ['not_between', 'Not between'],
]
const dateOrdered = [
  ['gt', 'After'],
  ['gte', 'On or after'],
  ['lt', 'Before'],
  ['lte', 'On or before'],
  ['between', 'Between (inclusive)'],
  ['not_between', 'Not between'],
]
const text = [
  ['is_empty', 'Is blank (empty string)'],
  ['is_not_empty', 'Is not blank (empty string)'],
  ['contains', 'Contains'],
  ['not_contains', 'Does not contain'],
  ['starts_with', 'Starts with'],
  ['not_starts_with', 'Does not start with'],
  ['ends_with', 'Ends with'],
  ['not_ends_with', 'Does not end with'],
]
const nulls = [
  ['is_null', 'Is empty (NULL)'],
  ['is_not_null', 'Is not empty (NULL)'],
]
function operators(kind: Kind) {
  return [
    ...(kind === 'boolean' ? comparison.slice(0, 2) : comparison),
    ...(kind === 'string'
      ? text
      : kind === 'boolean'
        ? []
        : kind === 'number'
          ? ordered
          : dateOrdered),
    ...nulls,
  ]
}
function rule(columns: FilterColumn[]): FilterCondition {
  const col = columns[0]
  return {
    type: 'condition',
    columnId: col?.columnId ?? '',
    operator: 'eq',
    values: [filterKind(col?.physicalType ?? '') === 'boolean' ? false : ''],
  }
}
export function FilterEditor({
  value,
  columns,
  onChange,
  loading = false,
  error = '',
}: {
  value?: FilterDefinition | null
  columns: FilterColumn[]
  onChange: (value: FilterDefinition | null) => void
  loading?: boolean
  error?: string
}) {
  function condition(
    node: FilterCondition,
    change: (node: FilterNode) => void,
    remove: () => void,
  ) {
    const col = columns.find((c) => c.columnId === node.columnId)
    const kind = filterKind(col?.physicalType ?? '')
    const many = node.operator === 'in' || node.operator === 'not_in'
    const none = node.operator.startsWith('is_')
    const between = node.operator === 'between' || node.operator === 'not_between'
    const available = operators(kind)
    return (
      <div className="filter-condition">
        <label>
          <span className="filter-input-label">Column</span>
          <select
            aria-label="Filter column"
            required
            value={node.columnId}
            onChange={(e) => {
              const col = columns.find((c) => c.columnId === e.target.value)
              change({
                ...node,
                columnId: e.target.value,
                operator: 'eq',
                values: [filterKind(col?.physicalType ?? '') === 'boolean' ? false : ''],
              })
            }}
          >
            <option value="">Choose column…</option>
            {node.columnId && !col && (
              <option value={node.columnId}>Unavailable column — update this rule</option>
            )}
            {columns.map((c) => (
              <option key={c.columnId} value={c.columnId}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span className="filter-input-label">Condition</span>
          <select
            aria-label="Filter operator"
            value={node.operator}
            onChange={(e) => {
              const operator = e.target.value as FilterCondition['operator']
              const count = operator.startsWith('is_')
                ? 0
                : operator === 'between' || operator === 'not_between'
                  ? 2
                  : 1
              change({
                ...node,
                operator,
                values: Array.from(
                  { length: count },
                  (_, i) => node.values[i] ?? (kind === 'boolean' ? false : ''),
                ),
              })
            }}
          >
            {!available.some(([op]) => op === node.operator) && (
              <option value={node.operator}>Unsupported condition — update this rule</option>
            )}
            {available.map(([op, name]) => (
              <option key={op} value={op}>
                {name}
              </option>
            ))}
          </select>
        </label>
        {!none &&
          (many ? (
            <label>
              <span className="filter-input-label">
                Values <small>{node.values.length}/100</small>
              </span>
              <textarea
                aria-label="Filter values"
                required
                rows={2}
                placeholder="One value per line"
                value={node.values.join('\n')}
                onChange={(e) => change({ ...node, values: e.target.value.split('\n') })}
              />
            </label>
          ) : kind === 'boolean' ? (
            <label>
              <span className="filter-input-label">Value</span>
              <select
                aria-label="Filter value"
                value={String(node.values[0] ?? false)}
                onChange={(e) => change({ ...node, values: [e.target.value === 'true'] })}
              >
                <option value="true">True</option>
                <option value="false">False</option>
              </select>
            </label>
          ) : (
            <div className="filter-values">
              {Array.from({ length: between ? 2 : 1 }, (_, i) => (
                <label key={i}>
                  <span className="filter-input-label">
                    {between ? (i ? 'To' : 'From') : 'Value'}
                  </span>
                  <input
                    aria-label={
                      between ? (i ? 'Filter to value' : 'Filter from value') : 'Filter value'
                    }
                    required
                    type={
                      kind === 'date' ? 'date' : kind === 'datetime' ? 'datetime-local' : 'text'
                    }
                    placeholder={
                      between
                        ? i
                          ? 'Maximum'
                          : 'Minimum'
                        : kind === 'number'
                          ? 'Enter a number'
                          : 'Enter a value'
                    }
                    inputMode={kind === 'number' ? 'decimal' : undefined}
                    step={kind === 'datetime' ? 'any' : undefined}
                    value={String(node.values[i] ?? '')}
                    onChange={(e) =>
                      change({
                        ...node,
                        values: Array.from({ length: between ? 2 : 1 }, (_, index) =>
                          index === i ? e.target.value : (node.values[index] ?? ''),
                        ),
                      })
                    }
                  />
                </label>
              ))}
            </div>
          ))}
        {none && <span className="filter-no-value">No value needed</span>}
        <button
          type="button"
          className="filter-remove"
          aria-label="Remove filter condition"
          title="Remove condition"
          onClick={remove}
        >
          <X size={16} aria-hidden="true" />
        </button>
      </div>
    )
  }
  function group(
    node: FilterGroup,
    change: (node: FilterGroup | null) => void,
    depth = 0,
    remove?: () => void,
  ) {
    return (
      <fieldset className={'filter-group' + (depth ? ' filter-group-nested' : '')}>
        <legend className="sr-only">{depth ? 'Nested group' : 'Keep rows matching'}</legend>
        <div className="filter-group-heading">
          <div className="filter-logic" role="group" aria-label="Filter group operator">
            {(['and', 'or'] as const).map((operator) => (
              <button
                type="button"
                key={operator}
                aria-pressed={node.combinator === operator}
                onClick={() => change({ ...node, combinator: operator })}
              >
                {operator.toUpperCase()}
              </button>
            ))}
          </div>
          <span className="filter-match-help">
            {node.combinator === 'and' ? 'Match all conditions' : 'Match any condition'}
          </span>
          {remove && (
            <button
              type="button"
              className="filter-remove"
              aria-label="Remove filter group"
              title="Remove group"
              onClick={remove}
            >
              <Trash2 size={15} aria-hidden="true" />
            </button>
          )}
        </div>
        <div className="filter-group-children">
          {node.children.map((child, i) => {
            const replace = (next: FilterNode | null) => {
              const children = node.children.flatMap((c, index) =>
                index === i ? (next ? [next] : []) : [c],
              )
              change(children.length ? { ...node, children } : null)
            }
            return (
              <div className="filter-node" key={i}>
                {i > 0 && (
                  <span className={'filter-connector filter-connector-' + node.combinator}>
                    {node.combinator.toUpperCase()}
                  </span>
                )}
                {child.type === 'group'
                  ? group(child, replace, depth + 1, () => replace(null))
                  : condition(child, replace, () => replace(null))}
              </div>
            )
          })}
        </div>
        <div className="filter-add-actions">
          <button
            type="button"
            className="filter-add"
            disabled={loading || node.children.length >= 100}
            onClick={() => change({ ...node, children: [...node.children, rule(columns)] })}
          >
            <Plus size={14} aria-hidden="true" /> Add condition
          </button>
          <button
            type="button"
            className="filter-add"
            disabled={loading || depth >= 15 || node.children.length >= 100}
            onClick={() =>
              change({
                ...node,
                children: [
                  ...node.children,
                  {
                    type: 'group',
                    combinator: node.combinator === 'and' ? 'or' : 'and',
                    children: [rule(columns)],
                  },
                ],
              })
            }
          >
            <Plus size={14} aria-hidden="true" /> Add group
          </button>
        </div>
      </fieldset>
    )
  }
  function count(node: FilterNode): number {
    return node.type === 'condition'
      ? 1
      : node.children.reduce((sum, child) => sum + count(child), 0)
  }
  function describe(node: FilterNode): string {
    if (node.type === 'group')
      return (
        '(' + node.children.map(describe).join(node.combinator === 'and' ? ' AND ' : ' OR ') + ')'
      )
    const column = columns.find((c) => c.columnId === node.columnId)
    if (!column) return node.columnId ? 'Unavailable column' : 'Choose a column'
    const operator =
      operators(filterKind(column.physicalType ?? '')).find(([op]) => op === node.operator)?.[1] ??
      'Unsupported condition'
    const values = node.values.map((v) =>
      v === ''
        ? '…'
        : typeof v === 'boolean'
          ? v
            ? 'True'
            : 'False'
          : filterKind(column.physicalType ?? '') === 'number'
            ? String(v)
            : JSON.stringify(v),
    )
    return [
      column.name,
      operator.toLowerCase(),
      values.join(node.operator.includes('between') ? ' and ' : ', '),
    ]
      .filter(Boolean)
      .join(' ')
  }
  const conditionCount = value ? count(value.root) : 0
  const summary = value ? describe(value.root).slice(1, -1) : ''
  return (
    <TransformationSection
      title="Filters"
      configured={!!value}
      status={
        value ? conditionCount + (conditionCount === 1 ? ' condition' : ' conditions') : 'All rows'
      }
    >
      {!value ? (
        <div className="filter-empty">
          <div>
            <span className="filter-empty-icon">
              <Filter size={18} aria-hidden="true" />
            </span>
            <span>
              <strong>All rows are included</strong>
              <small>Add conditions to keep only the rows you need.</small>
            </span>
          </div>
          <button
            type="button"
            className="secondary-button"
            onClick={() =>
              onChange({ root: { type: 'group', combinator: 'and', children: [rule(columns)] } })
            }
          >
            <Plus size={15} aria-hidden="true" /> Add filter
          </button>
        </div>
      ) : (
        <>
          <div className="filter-editor-heading">
            <span>Keep rows that match the rules below.</span>
            <button type="button" className="filter-clear" onClick={() => onChange(null)}>
              Clear all
            </button>
          </div>
          {loading && (
            <p role="status" className="meta-hint">
              Loading available filter columns…
            </p>
          )}
          {error && (
            <p className="meta-error" role="alert">
              {error}
            </p>
          )}
          {group(value.root, (root) => onChange(root ? { root } : null))}
          <div className="filter-expression" aria-label="Filter summary">
            <span>RESULTING RULE</span>
            <p>{summary.length > 700 ? summary.slice(0, 700) + '…' : summary}</p>
          </div>
          <details className="filter-matching-notes">
            <summary>About matching</summary>
            <p>
              Text matching is case-sensitive. NULL means a missing value; blank means an empty
              string. Use an explicit NULL condition to include missing values. For a list, enter
              one value per line. Commas stay part of the value.
            </p>
          </details>
        </>
      )}
    </TransformationSection>
  )
}
