import { metadataApi } from '../api'
import { MeasureBaseVisibility } from './MeasureBaseVisibility'
import { FieldHelp } from '../../components/FieldHelp'
import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { formatFormula, parseFormula, formulaUnit } from '../formula'
import { referencedResultType } from '../measureTypes'
import type { Aggregation, CalcDefinition, Measure, RefDefinition, SourceColumn } from '../types'
import { Actions, Check, Field, IdentityFields, options, Select, type EditorProps } from './Fields'
export const defaultAggregation: Aggregation = {
  nonTime: 'sum',
  time: 'sum',
  nullInputPolicy: 'ignore',
  allNullResult: 'null',
}
export function MeasureEditor({
  initial,
  data,
  editing,
  onSave,
  onCancel,
  busy,
  base,
  baseLevelId,
}: EditorProps<Measure> & { base?: string; baseLevelId?: string | null }) {
  const [value, set] = useState(initial)
  const [text, setText] = useState(
    initial.definition.measureType === 'calculated'
      ? formatFormula(
          initial.definition.formula.expression,
          data.measures,
          data.planningLevels.find((l) => l.objectId === initial.definition.nativePlanningLevelId)
            ?.definition.grain.columns,
        )
      : '',
  )
  const formulaEditor = useRef<HTMLTextAreaElement>(null)
  const pendingCaret = useRef<number | null>(null)
  useLayoutEffect(() => {
    if (pendingCaret.current === null || !formulaEditor.current) return
    const caret = pendingCaret.current
    pendingCaret.current = null
    formulaEditor.current.focus({ preventScroll: true })
    formulaEditor.current.setSelectionRange(caret, caret)
  }, [text])
  function insertMeasure(name: string) {
    const editor = formulaEditor.current
    if (!editor) return
    const { selectionStart: start, selectionEnd: end } = editor
    const token = `[${name}]`
    const next = text.slice(0, start) + token + text.slice(end)
    if (next.length > editor.maxLength) {
      editor.focus({ preventScroll: true })
      return
    }
    const caret = start + token.length
    if (next === text) {
      editor.focus({ preventScroll: true })
      editor.setSelectionRange(caret, caret)
      return
    }
    pendingCaret.current = caret
    setText(next)
  }
  const [error, setError] = useState('')
  const d = value.definition
  const update = (patch: Partial<typeof d>) =>
    set({ ...value, definition: { ...d, ...patch } as Measure['definition'] })
  const tx =
    d.measureType === 'referenced'
      ? data.transactions.find((t) => t.objectId === d.transactionId)
      : undefined
  const source = data.sources.find((s) => s.objectId === tx?.definition.sourceId)
  const transformedTransaction =
    !!tx?.definition.pivot?.enabled || !!tx?.definition.calendarResolution
  const schemaKey =
    base && tx ? base + '/transactions/' + encodeURIComponent(tx.objectId) + '/schema' : ''
  const [transactionSchema, setTransactionSchema] = useState<{
    key: string
    columns: SourceColumn[]
    valueColumnIds?: string[]
  } | null>(null)
  const [schemaError, setSchemaError] = useState('')
  useEffect(() => {
    setSchemaError('')
    if (!schemaKey || !transformedTransaction) return
    const controller = new AbortController()
    void metadataApi
      .request<{ columns: SourceColumn[]; valueColumnIds?: string[] }>(
        schemaKey,
        'GET',
        undefined,
        undefined,
        controller.signal,
      )
      .then((result) => {
        if (!controller.signal.aborted) setTransactionSchema({ key: schemaKey, ...result })
      })
      .catch((e) => {
        if (!controller.signal.aborted) setSchemaError(e.message)
      })
    return () => controller.abort()
  }, [schemaKey, transformedTransaction, tx?.version])
  const effectiveColumns = transformedTransaction
    ? transactionSchema?.key === schemaKey
      ? transactionSchema.columns
      : []
    : (source?.definition.columns ?? [])
  const valueColumns =
    tx?.definition.mappingMode === 'resolve_multi_grain'
      ? effectiveColumns
          .filter((c) =>
            tx.definition.resolution?.values.some((v) => v.sourceColumnId === c.columnId),
          )
          .map((c) => ({ ...c, physicalType: 'Float64' }))
      : effectiveColumns
  const aggregation = d.aggregation ?? defaultAggregation
  const valueColumn =
    d.measureType === 'referenced'
      ? valueColumns.find((c) => c.columnId === d.valueColumnId)
      : undefined
  const sourceResultType =
    d.measureType === 'referenced' && valueColumn
      ? referencedResultType(
          valueColumn.physicalType,
          valueColumn.nullable ||
            d.missingTransactionValue === 'null' ||
            aggregation.allNullResult === 'null',
        )
      : null
  const typeGroupId = useId()
  const changeMeasureType = (type: 'referenced' | 'calculated') => {
    const common = {
      nativePlanningLevelId: '',
      resultType: { type: 'number' as const, nullable: true },
      editable: false,
    }
    const definition: RefDefinition | CalcDefinition =
      type === 'referenced'
        ? {
            ...common,
            measureType: 'referenced',
            transactionId: '',
            valueColumnId: '',
            aggregation: defaultAggregation,
            missingTransactionValue: 'null',
            sourceNullPolicy: 'preserve',
            allocation: { mode: 'weighted_by_self' },
          }
        : {
            ...common,
            measureType: 'calculated',
            calculationType: 'AggregateFirst',
            formula: { displayText: '', expression: { type: 'literal', value: 0 } },
          }
    set({ ...value, definition })
    setError('')
  }
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        setError('')
        try {
          const input =
            d.measureType === 'calculated'
              ? {
                  ...value,
                  definition: {
                    ...d,
                    resultType:
                      d.resultType.type === 'integer'
                        ? { type: 'number' as const, nullable: d.resultType.nullable }
                        : d.resultType,
                    formula: {
                      displayText: text,
                      expression: parseFormula(
                        text,
                        data.measures.filter((m) => m.objectId !== value.objectId),
                        data.planningLevels.find((l) => l.objectId === d.nativePlanningLevelId)
                          ?.definition.grain.columns,
                      ),
                    },
                  },
                }
              : {
                  ...value,
                  definition: {
                    ...d,
                    resultType: sourceResultType ?? { type: 'number' as const, nullable: true },
                  },
                }
          void onSave({
            ...input,
            business: {
              ...input.business,
              unit:
                input.definition.measureType === 'calculated'
                  ? (formulaUnit(input.definition.formula.expression, data.measures) ??
                    value.business.unit)
                  : (valueColumn?.unit ?? value.business.unit),
              aliases: value.business.aliases,
            },
          })
        } catch (error) {
          setError(error instanceof Error ? error.message : 'Invalid formula.')
        }
      }}
    >
      <IdentityFields value={value} editing={editing} onChange={(v) => set({ ...value, ...v })} />
      <fieldset className="measure-type-control" disabled={busy}>
        <legend>
          Measure type <FieldHelp label="Measure type" />
        </legend>
        <div className="measure-type-switch">
          {(
            [
              ['referenced', 'Reference'],
              ['calculated', 'Calculated'],
            ] as const
          ).map(([type, label]) => (
            <label key={type} className="measure-type-option">
              <input
                type="radio"
                name={typeGroupId}
                value={type}
                checked={d.measureType === type}
                onChange={() => changeMeasureType(type)}
              />
              <span>{label}</span>
            </label>
          ))}
        </div>
      </fieldset>
      {d.measureType === 'referenced' && (
        <>
          {schemaError && (
            <p className="meta-error" role="alert">
              {schemaError}
            </p>
          )}
          <div className="meta-grid">
            <Select
              label="Transaction"
              value={d.transactionId}
              items={options(data.transactions)}
              onChange={(transactionId) => {
                const transaction = data.transactions.find((t) => t.objectId === transactionId)!
                update({
                  transactionId,
                  nativePlanningLevelId: transaction.definition.planningLevelId,
                  valueColumnId: '',
                })
              }}
            />
            <Select
              label="Value column"
              value={d.valueColumnId}
              items={valueColumns
                .filter((c) =>
                  /^(?:Nullable\(|LowCardinality\()*(?:U?Int|Float|Decimal)/i.test(c.physicalType),
                )
                .map((c) => ({ value: c.columnId, label: c.name }))}
              onChange={(valueColumnId) => update({ valueColumnId })}
            />
          </div>
          <div className="measure-output-level" role="group" aria-label="Output Planning Level">
            <span className="field-label-with-help">
              Output Planning Level <FieldHelp label="Output Planning Level" />
            </span>
            <strong>
              {data.planningLevels.find((l) => l.objectId === d.nativePlanningLevelId)?.name ??
                'Choose a transaction'}
            </strong>
          </div>
        </>
      )}
      {d.measureType === 'referenced' ? (
        <>
          <Check
            label="Editable measure"
            value={d.editable}
            onChange={(editable) => update({ editable })}
          />
          <div className="meta-grid">
            <Select
              label="Missing transaction value"
              value={d.missingTransactionValue}
              items={['null', 'zero']}
              onChange={(v) => update({ missingTransactionValue: v as 'null' | 'zero' })}
            />
          </div>
        </>
      ) : (
        <>
          <div className="meta-grid">
            <div className="measure-output-level">
              <Select
                label="Output Planning Level"
                value={d.nativePlanningLevelId}
                items={options(data.planningLevels)}
                onChange={(nativePlanningLevelId) => update({ nativePlanningLevelId })}
              />
            </div>
            <Select
              label="Calculation type"
              value={d.calculationType}
              items={['AggregateFirst', 'CalculateFirst']}
              onChange={(v) =>
                set({
                  ...value,
                  definition: {
                    ...d,
                    calculationType: v as 'AggregateFirst' | 'CalculateFirst',
                    aggregation: v === 'CalculateFirst' ? defaultAggregation : null,
                    allocation: v === 'CalculateFirst' ? { mode: 'weighted_by_self' } : null,
                  },
                })
              }
            />
          </div>
          <Field label="Formula" hint="Use measure names, such as ABS(FQTY - SQTY).">
            <textarea
              ref={formulaEditor}
              value={text}
              onChange={(e) => setText(e.target.value)}
              required
              maxLength={4000}
              placeholder="ABS(FQTY - SQTY)"
            />
          </Field>
          <div className="meta-reference-list">
            {data.measures
              .filter((m) => m.objectId !== value.objectId)
              .map((m) => (
                <button
                  key={m.objectId}
                  type="button"
                  className="meta-tag"
                  onMouseDown={(event) => {
                    if (event.button === 0) event.preventDefault()
                  }}
                  onClick={() => insertMeasure(m.name)}
                  title={m.name}
                >
                  {m.name}
                </button>
              ))}
          </div>
          <p className="meta-hint">
            {d.calculationType === 'AggregateFirst'
              ? 'Inputs are aligned and aggregated before the formula is evaluated.'
              : 'The formula is evaluated at its native grain before its result is aggregated.'}
          </p>
        </>
      )}
      {d.measureType === 'calculated' && (
        <>
          <Check
            label="Nullable result"
            value={d.resultType.nullable}
            onChange={(nullable) => update({ resultType: { ...d.resultType, nullable } })}
          />
        </>
      )}
      {(d.measureType === 'referenced' || d.calculationType === 'CalculateFirst') && (
        <>
          <h3>Aggregation policies</h3>
          <div className="meta-grid">
            <Select
              label="Non-time aggregation"
              value={aggregation.nonTime}
              items={['sum', 'avg', 'min', 'max', 'count', 'count_distinct']}
              onChange={(v) =>
                update({ aggregation: { ...aggregation, nonTime: v as Aggregation['nonTime'] } })
              }
            />
            <Select
              label="Time aggregation"
              value={aggregation.time}
              items={['sum', 'avg', 'min', 'max', 'count', 'count_distinct', 'first', 'last']}
              onChange={(v) =>
                update({ aggregation: { ...aggregation, time: v as Aggregation['time'] } })
              }
            />
            <Select
              label="Aggregation order"
              value={aggregation.order ?? 'nonTimeThenTime'}
              items={[
                { value: 'nonTimeThenTime', label: 'Non-time, then Time' },
                { value: 'timeThenNonTime', label: 'Time, then non-time' },
              ]}
              onChange={(order) =>
                update({ aggregation: { ...aggregation, order: order as Aggregation['order'] } })
              }
            />
            <Select
              label="Null input policy"
              value={aggregation.nullInputPolicy}
              items={['ignore', 'propagate']}
              onChange={(v) =>
                update({
                  aggregation: { ...aggregation, nullInputPolicy: v as 'ignore' | 'propagate' },
                })
              }
            />
            <Select
              label="All-null result"
              value={aggregation.allNullResult}
              items={['null', 'zero']}
              onChange={(v) =>
                update({ aggregation: { ...aggregation, allNullResult: v as 'null' | 'zero' } })
              }
            />
          </div>
        </>
      )}
      {(d.measureType === 'referenced' ||
        (d.calculationType === 'CalculateFirst' &&
          !(baseLevelId && d.nativePlanningLevelId === baseLevelId))) && (
        <>
          <h3>Dis-Aggregation policies</h3>
          <p className="meta-hint">
            Used for user edits. Materialisation splits SUM axes equally and repeats other axes.
          </p>
          <div className="meta-grid">
            <Select
              label="Dis-aggregation policy"
              value={d.allocation?.mode ?? 'none'}
              items={[
                { value: 'none', label: 'None — repeat value' },
                { value: 'equal', label: 'Equal' },
                { value: 'weighted', label: 'Weighted' },
                { value: 'weighted_by_self', label: 'Weighted by self' },
              ]}
              onChange={(v) =>
                update({
                  allocation: {
                    mode: v as 'none' | 'equal' | 'weighted' | 'weighted_by_self',
                    ...(v === 'weighted' ? { basisMeasureId: '' } : {}),
                  },
                })
              }
            />
            {d.allocation?.mode === 'weighted' && (
              <Select
                label="Dis-aggregation basis measure"
                value={d.allocation.basisMeasureId ?? ''}
                items={options(data.measures.filter((m) => m.objectId !== value.objectId))}
                onChange={(basisMeasureId) =>
                  update({ allocation: { mode: 'weighted', basisMeasureId } })
                }
              />
            )}
          </div>

          {d.allocation?.mode === 'weighted_by_self' && (
            <p className="meta-hint">
              Uses this measure’s populated values at the edit write level (Base when reachable,
              otherwise its output level) as proportional weights.
            </p>
          )}
        </>
      )}
      <MeasureBaseVisibility
        definition={d}
        levels={data.planningLevels}
        base={base}
        baseLevelId={baseLevelId}
      />
      {error && (
        <p className="meta-error" role="alert">
          {error}
        </p>
      )}
      <Actions busy={busy} onCancel={onCancel} />
    </form>
  )
}
