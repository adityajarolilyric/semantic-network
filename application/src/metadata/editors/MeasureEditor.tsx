import { useState } from 'react'
import { formatFormula, parseFormula, formulaUnit } from '../formula'
import { referencedResultType } from '../measureTypes'
import type { Aggregation, CalcDefinition, Measure, RefDefinition } from '../types'
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
}: EditorProps<Measure>) {
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
  const [error, setError] = useState('')
  const d = value.definition
  const update = (patch: Partial<typeof d>) =>
    set({ ...value, definition: { ...d, ...patch } as Measure['definition'] })
  const tx =
    d.measureType === 'referenced'
      ? data.transactions.find((t) => t.objectId === d.transactionId)
      : undefined
  const source = data.sources.find((s) => s.objectId === tx?.definition.sourceId)
  const valueColumns =
    tx?.definition.mappingMode === 'resolve_multi_grain'
      ? (source?.definition.columns ?? [])
          .filter((c) =>
            tx.definition.resolution?.values.some((v) => v.sourceColumnId === c.columnId),
          )
          .map((c) => ({ ...c, physicalType: 'Float64' }))
      : (source?.definition.columns ?? [])
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
      <Select
        label="Measure type"
        value={d.measureType}
        items={['referenced', 'calculated']}
        onChange={(type) => {
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
                  sourceNullPolicy: 'reject',
                  allocation: { mode: 'none' },
                }
              : {
                  ...common,
                  measureType: 'calculated',
                  calculationType: 'AggregateFirst',
                  formula: { displayText: '', expression: { type: 'literal', value: 0 } },
                }
          set({ ...value, definition })
        }}
      />
      {d.measureType === 'referenced' && (
        <>
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
          <p className="meta-hint">
            Native level:{' '}
            {data.planningLevels.find((l) => l.objectId === d.nativePlanningLevelId)?.name ??
              'Choose a transaction'}
          </p>
        </>
      )}
      {d.measureType === 'referenced' ? (
        <>
          <div className="meta-grid">
            <Select
              label="Missing transaction value"
              value={d.missingTransactionValue}
              items={['null', 'zero']}
              onChange={(v) => update({ missingTransactionValue: v as 'null' | 'zero' })}
            />
            <Select
              label="Source null policy"
              value={d.sourceNullPolicy}
              items={['reject', 'preserve']}
              onChange={(v) => update({ sourceNullPolicy: v as 'reject' | 'preserve' })}
            />
          </div>
          <Check
            label="Editable measure"
            value={d.editable}
            onChange={(editable) => update({ editable })}
          />
        </>
      ) : (
        <>
          <div className="meta-grid">
            <Select
              label="Native planning level"
              value={d.nativePlanningLevelId}
              items={options(data.planningLevels)}
              onChange={(nativePlanningLevelId) => update({ nativePlanningLevelId })}
            />
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
                  },
                })
              }
            />
          </div>
          <Field label="Formula" hint="Use measure names, such as ABS(FQTY - SQTY).">
            <textarea
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
                  onClick={() => setText((t) => t + `[${m.name}]`)}
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
      {(d.measureType === 'referenced' || d.calculationType === 'CalculateFirst') && (
        <>
          <h3>Dis-Aggregation policies</h3>
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
          <p className="meta-hint">
            None repeats the value to every finer row. Your aggregation policies control how those
            values combine; no aggregation method is inferred. This policy also applies to future
            edits at coarser levels, including for Base-native measures.
          </p>
          {d.allocation?.mode === 'weighted_by_self' && (
            <p className="meta-hint">
              Uses this measure’s existing base-level values as proportional weights.
            </p>
          )}
        </>
      )}
      {error && (
        <p className="meta-error" role="alert">
          {error}
        </p>
      )}
      <Actions busy={busy} onCancel={onCancel} />
    </form>
  )
}
