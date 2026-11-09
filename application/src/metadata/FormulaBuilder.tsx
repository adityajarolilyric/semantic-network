import { useId, useLayoutEffect, useRef, useState, type MouseEvent } from 'react'
import { formulaReference, type FormulaAttribute } from './formula'
import type { Measure, Snapshot } from './types'
import type { FormulaValidation } from './useFormulaValidation'
import { FieldHelp } from '../components/FieldHelp'

export type FormulaFunction = {
  name: string
  description: string
  group?: string
  minimum?: number
  maximum?: number
}
export type FormulaMeasureAvailability = Record<string, { available: boolean; reason: string }>
type Tab = 'Measures' | 'Columns' | 'Functions'
const tabs: Tab[] = ['Measures', 'Columns', 'Functions']
const guides: Record<string, [string, string]> = {
  abs: ['ABS(number)', 'ABS([Qty])'],
  sqrt: ['SQRT(number)', 'SQRT([Variance])'],
  root: ['ROOT(number, degree)', 'ROOT([Volume], 3)'],
  power: ['POWER(number, exponent)', 'POWER([Qty], 2)'],
  ceil: ['CEIL(number)', 'CEIL([Qty])'],
  floor: ['FLOOR(number)', 'FLOOR([Qty])'],
  rolling: ['ROLLING(value, "operation", start, end)', 'ROLLING([Demand], "SUM", -3, 0)'],
  cumulative: ['CUMULATIVE(value, "operation")', 'CUMULATIVE([Demand], "SUM")'],
  lag: ['LAG(value, periods)', 'LAG([Demand], 1)'],
  lead: ['LEAD(value, periods)', 'LEAD([Demand], 1)'],
  round: ['ROUND(number, decimal_places)', 'ROUND([Qty], 2)'],
  if: ['IF(condition, when_true, when_false)', 'IF([Qty] < 0, "At Risk", "Healthy")'],
  coalesce: ['COALESCE(value, fallback, …)', 'COALESCE([Qty], 0)'],
  min: ['MIN(first, second, …)', 'MIN([Forecast], [Sales])'],
  max: ['MAX(first, second, …)', 'MAX([Forecast], [Sales])'],
  upper: ['UPPER(text)', 'UPPER([Product.Brand])'],
  lower: ['LOWER(text)', 'LOWER([Product.Brand])'],
  trim: ['TRIM(text)', 'TRIM([Product.Brand])'],
  count: ['COUNT(value)', 'COUNT([Qty])'],
  count_if: ['COUNT_IF(condition)', 'COUNT_IF([Qty] > 0)'],
  count_distinct: ['COUNT_DISTINCT(value)', 'COUNT_DISTINCT([Product.Brand])'],
  count_rows: ['COUNT_ROWS()', 'COUNT_ROWS()'],
  concat: ['CONCAT(text, text, …)', 'CONCAT([Product.Brand], " / ", [Product.Name])'],
  date: ['DATE(iso_date_text)', 'DATE("2026-01-01")'],
  add_days: ['ADD_DAYS(date, days)', 'ADD_DAYS([Dispatch Date], 2)'],
  days_between: ['DAYS_BETWEEN(start_date, end_date)', 'DAYS_BETWEEN([Start Date], [End Date])'],
  and: ['AND(condition, condition, …)', 'AND([Qty] > 0, [Active])'],
  or: ['OR(condition, condition, …)', 'OR([Qty] > 0, [Active])'],
  not: ['NOT(condition)', 'NOT([Active])'],
  is_null: ['IS_NULL(value)', 'IS_NULL([Qty])'],
}
const typeName = (type: string) =>
  type === 'number' || type === 'decimal' || type === 'integer'
    ? 'Number'
    : type[0]?.toUpperCase() + type.slice(1)

/** Inline editor and input library. The backend remains the language authority. */
export function FormulaBuilder({
  value,
  onChange,
  measures,
  measureAvailability,
  measureError = '',
  attributes,
  functions,
  data,
  validation,
  attributeError,
  functionError,
  levelSelected,
  compact = false,
  disabled = false,
}: {
  value: string
  onChange: (text: string) => void
  measures: Measure[]
  measureAvailability?: FormulaMeasureAvailability
  measureError?: string
  attributes: FormulaAttribute[]
  functions: FormulaFunction[]
  data: Pick<Snapshot, 'dimensionMembers' | 'dimensions'>
  validation: FormulaValidation | null
  attributeError: string
  functionError: string
  levelSelected: boolean
  compact?: boolean
  disabled?: boolean
}) {
  const editor = useRef<HTMLTextAreaElement | HTMLInputElement>(null)
  const search = useRef<HTMLInputElement>(null)
  const library = useRef<HTMLDivElement>(null)
  const pendingCaret = useRef<number | null>(null)
  const [tab, setTab] = useState<Tab>('Measures')
  const [query, setQuery] = useState('')
  const [activeFunction, setActiveFunction] = useState<FormulaFunction | null>(null)
  const id = useId()
  const matches = (text: string) => text.toLowerCase().includes(query.trim().toLowerCase())
  const filteredMeasures = measures.filter((measure) => matches(measure.name))
  const filteredColumns = attributes.filter((attribute) =>
    matches(attribute.label + ' ' + (attribute.aliases ?? []).join(' ')),
  )
  const filteredFunctions = functions.filter((fn) => matches(fn.name + ' ' + fn.description))
  const owners = new Map(
    [...data.dimensionMembers, ...data.dimensions].map((owner) => [owner.objectId, owner]),
  )
  const groups = new Map<string, FormulaAttribute[]>()
  for (const column of filteredColumns) {
    const ownerId = column.reference.memberCatalogId ?? column.reference.dimensionId!
    groups.set(ownerId, [...(groups.get(ownerId) ?? []), column])
  }
  useLayoutEffect(() => {
    if (pendingCaret.current === null || !editor.current) return
    const caret = pendingCaret.current
    pendingCaret.current = null
    editor.current.focus({ preventScroll: true })
    editor.current.setSelectionRange(caret, caret)
  }, [value])
  function insert(token: string, inside = false) {
    const input = editor.current
    if (!input || disabled) return
    const start = input.selectionStart ?? value.length
    const end = input.selectionEnd ?? start
    const next = value.slice(0, start) + token + value.slice(end)
    if (next.length > 4000) {
      input.focus({ preventScroll: true })
      return
    }
    const caret = start + token.length - (inside ? 1 : 0)
    if (next === value) {
      input.focus({ preventScroll: true })
      input.setSelectionRange(caret, caret)
    } else {
      pendingCaret.current = caret
      onChange(next)
    }
  }
  const preserveSelection = (event: MouseEvent) => {
    if (event.button === 0) event.preventDefault()
  }
  function insertFirst() {
    const first = library.current?.querySelector<HTMLButtonElement>(
      '[data-formula-input]:not(:disabled)',
    )
    first?.click()
  }
  const selectedGuide = activeFunction
    ? (guides[activeFunction.name] ?? [activeFunction.name.toUpperCase() + '(value)', ''])
    : null
  const hasError = !!(value.trim() && levelSelected && validation && !validation.valid)
  return (
    <section
      className={`formula-builder ${compact ? 'formula-builder-compact' : ''} ${hasError ? 'has-error' : ''}`}
      aria-label="Formula builder"
    >
      <div className="field-label-with-help formula-builder-label">
        <label htmlFor={`${id}-editor`}>Formula</label>
        <FieldHelp label="Formula" />
      </div>
      <div className="formula-builder-main">
        <div className="formula-workspace">
          <div className="meta-field formula-input-field">
            {compact ? (
              <input
                id={`${id}-editor`}
                aria-invalid={hasError || undefined}
                aria-describedby={`${id}-feedback`}
                ref={(element) => {
                  editor.current = element
                }}
                className="quick-measure-formula"
                value={value}
                onChange={(event) => onChange(event.target.value)}
                required
                maxLength={4000}
                spellCheck={false}
                autoComplete="off"
                disabled={disabled}
                onKeyDown={(event) => {
                  if (event.ctrlKey && event.code === 'Space') {
                    event.preventDefault()
                    search.current?.focus()
                  }
                }}
              />
            ) : (
              <textarea
                id={`${id}-editor`}
                aria-invalid={hasError || undefined}
                aria-describedby={`${id}-feedback`}
                ref={(element) => {
                  editor.current = element
                }}
                value={value}
                onChange={(event) => onChange(event.target.value)}
                required
                maxLength={4000}
                rows={5}
                spellCheck={false}
                disabled={disabled}
                onKeyDown={(event) => {
                  if (event.ctrlKey && event.code === 'Space') {
                    event.preventDefault()
                    search.current?.focus()
                  }
                }}
              />
            )}
          </div>
          {!compact && (
            <div className="formula-operators" aria-label="Formula operators">
              {['+', '-', '*', '/', '(', ')', '>', '<', '='].map((operator) => (
                <button
                  type="button"
                  key={operator}
                  aria-label={'Insert ' + operator}
                  disabled={disabled}
                  onMouseDown={preserveSelection}
                  onClick={() =>
                    insert(['(', ')'].includes(operator) ? operator : ' ' + operator + ' ')
                  }
                >
                  {operator}
                </button>
              ))}
              <small>Click an input to insert · Ctrl + Space to search</small>
            </div>
          )}
          <div
            id={`${id}-feedback`}
            className={`formula-feedback ${hasError ? 'is-error' : validation?.valid ? 'is-valid' : ''}`}
            role="status"
            aria-live="polite"
          >
            {!value.trim()
              ? 'Start typing, or add an input from the library.'
              : !levelSelected
                ? 'Choose an Output Dimension to validate this formula.'
                : !validation
                  ? 'Checking formula…'
                  : validation.valid
                    ? `Valid formula · ${typeName(validation.resultType?.type ?? 'number')}`
                    : validation.diagnostics
                        .map(
                          (item) =>
                            `${item.line ? `Line ${item.line}, column ${item.column}: ` : ''}${item.message}`,
                        )
                        .join(' ')}
          </div>
        </div>
        <aside
          className={`formula-library ${tab === 'Functions' && activeFunction ? 'has-guide' : ''}`}
          aria-label="Formula input library"
        >
          <div role="tablist" aria-label="Formula inputs" className="formula-library-tabs">
            {tabs.map((name, index) => (
              <button
                key={name}
                type="button"
                role="tab"
                id={`${id}-tab-${name}`}
                aria-controls={`${id}-panel`}
                aria-selected={tab === name}
                tabIndex={tab === name ? 0 : -1}
                onClick={() => {
                  setTab(name)
                  setQuery('')
                  setActiveFunction(null)
                }}
                onKeyDown={(event) => {
                  if (!['ArrowRight', 'ArrowLeft', 'Home', 'End'].includes(event.key)) return
                  event.preventDefault()
                  const target =
                    event.key === 'Home'
                      ? 0
                      : event.key === 'End'
                        ? tabs.length - 1
                        : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) %
                          tabs.length
                  setTab(tabs[target])
                  setQuery('')
                  setActiveFunction(null)
                  document.getElementById(`${id}-tab-${tabs[target]}`)?.focus()
                }}
              >
                {name}
              </button>
            ))}
          </div>
          <input
            type="search"
            ref={search}
            className="formula-library-search"
            aria-label="Search formula inputs"
            placeholder={
              tab === 'Columns' ? 'Search tables or columns' : 'Search ' + tab.toLowerCase()
            }
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            disabled={disabled}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault()
                insertFirst()
              }
              if (event.key === 'ArrowDown') {
                event.preventDefault()
                library.current
                  ?.querySelector<HTMLButtonElement>('[data-formula-input]:not(:disabled)')
                  ?.focus()
              }
              if (event.key === 'Escape') {
                event.preventDefault()
                editor.current?.focus()
              }
            }}
          />
          <div
            ref={library}
            id={`${id}-panel`}
            role="tabpanel"
            aria-labelledby={`${id}-tab-${tab}`}
            className="formula-library-results"
          >
            {tab === 'Measures' && measureError && <p role="alert">{measureError}</p>}
            {tab === 'Measures' &&
              (filteredMeasures.length ? (
                filteredMeasures.map((measure) => (
                  <button
                    type="button"
                    key={measure.objectId}
                    data-formula-input
                    aria-label={measure.name}
                    disabled={
                      disabled || measureAvailability?.[measure.objectId]?.available === false
                    }
                    title={measureAvailability?.[measure.objectId]?.reason}
                    onMouseDown={preserveSelection}
                    onClick={() => insert(formulaReference(measure.name))}
                  >
                    <span>{measure.name}</span>
                    <small>
                      {measureAvailability?.[measure.objectId]?.available === false
                        ? measureAvailability[measure.objectId].reason
                        : `${measure.definition.measureType === 'calculated' ? 'Calculated · ' : ''}${typeName(measure.definition.resultType.type)}`}
                    </small>
                  </button>
                ))
              ) : (
                <p>
                  {query
                    ? 'No matching measures.'
                    : 'Create a referenced or calculated measure to use it here.'}
                </p>
              ))}
            {tab === 'Columns' &&
              (filteredColumns.length ? (
                [...groups].map(([ownerId, columns]) => {
                  const owner = owners.get(ownerId)
                  const ownerName =
                    owner?.name ?? columns[0].label.slice(0, columns[0].label.lastIndexOf('.'))
                  return (
                    <div className="formula-column-group" key={ownerId}>
                      <div className="formula-column-heading">
                        <strong>{ownerName}</strong>
                        <small>
                          {columns[0].reference.memberCatalogId ? 'Dimension' : 'Dimension'}
                        </small>
                      </div>
                      {columns.map((column) => (
                        <button
                          type="button"
                          key={column.key}
                          data-formula-input
                          disabled={disabled}
                          aria-label={'Insert ' + column.label}
                          title={'[' + column.label + ']'}
                          onMouseDown={preserveSelection}
                          onClick={() => insert(`[${column.label}]`)}
                        >
                          <span>
                            {column.label.startsWith(ownerName + '.')
                              ? column.label.slice(ownerName.length + 1)
                              : column.label}
                          </span>
                          <small>{typeName(column.valueType)}</small>
                        </button>
                      ))}
                    </div>
                  )
                })
              ) : (
                <p>
                  {attributeError ||
                    (query
                      ? 'No matching columns.'
                      : levelSelected
                        ? 'No columns are available at this Output Dimension.'
                        : 'Choose an Output Dimension to see available columns.')}
                </p>
              ))}
            {tab === 'Functions' &&
              (filteredFunctions.length ? (
                [...new Set(filteredFunctions.map((fn) => fn.group ?? 'Functions'))].map(
                  (group) => (
                    <div className="formula-column-group" key={group}>
                      <div className="formula-column-heading">{group}</div>
                      {filteredFunctions
                        .filter((fn) => (fn.group ?? 'Functions') === group)
                        .map((fn) => (
                          <button
                            type="button"
                            key={fn.name}
                            data-formula-input
                            disabled={disabled}
                            aria-label={'Insert ' + fn.name.toUpperCase()}
                            onMouseDown={preserveSelection}
                            onMouseEnter={() => setActiveFunction(fn)}
                            onFocus={() => setActiveFunction(fn)}
                            onClick={() => {
                              setActiveFunction(fn)
                              insert(
                                fn.name.toUpperCase() + '()',
                                fn.minimum !== 0 && fn.name !== 'count_rows',
                              )
                            }}
                          >
                            <span>{fn.name.toUpperCase()}</span>
                            <small>{fn.group ?? 'Function'}</small>
                          </button>
                        ))}
                    </div>
                  ),
                )
              ) : (
                <p>
                  {functionError ||
                    (query
                      ? 'No matching functions.'
                      : 'Functions will appear when the language service is available.')}
                </p>
              ))}
          </div>
          {tab === 'Functions' && activeFunction && selectedGuide && (
            <div className="formula-function-guide">
              <code>{selectedGuide[0]}</code>
              <p>{activeFunction.description}</p>
              {selectedGuide[1] && (
                <small>
                  Example: <code>{selectedGuide[1]}</code>
                </small>
              )}
            </div>
          )}
        </aside>
      </div>
    </section>
  )
}
