import type { Aggregation, Measure, RefDefinition, SourceColumn } from '../types'
import { typedResultType, numericType, aggregationOptions } from '../measureTypes'
import { TransformationSection } from './TransformationSection'

export type TransactionMeasureDraft = {
  valueColumnId: string
  name: string
  editable: boolean
  valueType?: string
  nonTime: Aggregation['nonTime'] | ''
  time: Aggregation['time'] | ''
  allocation: NonNullable<RefDefinition['allocation']>
}
export const measureValueColumns = (columns: SourceColumn[]) =>
  columns.filter((column) => typedResultType(column.physicalType, true))
export function transactionMeasuresComplete(
  drafts: TransactionMeasureDraft[],
  columns: SourceColumn[],
) {
  return drafts.every(
    (draft) =>
      columns.some((column) => column.columnId === draft.valueColumnId) &&
      draft.nonTime &&
      draft.time &&
      (draft.allocation.mode !== 'weighted' || draft.allocation.basisMeasureId),
  )
}
export function transactionMeasureSelections(drafts: TransactionMeasureDraft[]) {
  return drafts.map((draft) => ({
    valueColumnId: draft.valueColumnId,
    editable: draft.editable,
    aggregation: {
      nonTime: draft.nonTime,
      time: draft.valueType && !numericType(draft.valueType) ? draft.nonTime : draft.time,
      order: 'nonTimeThenTime',
      nullInputPolicy: 'ignore',
      allNullResult: 'null',
    },
    allocation: draft.valueType && !numericType(draft.valueType) ? null : draft.allocation,
  }))
}
export function TransactionMeasureEditor({
  columns,
  drafts,
  onChange,
  measures,
  loading,
}: {
  columns: SourceColumn[]
  drafts: TransactionMeasureDraft[]
  onChange: (drafts: TransactionMeasureDraft[]) => void
  measures: Measure[]
  loading: boolean
}) {
  const update = (id: string, patch: Partial<TransactionMeasureDraft>) =>
    onChange(drafts.map((draft) => (draft.valueColumnId === id ? { ...draft, ...patch } : draft)))
  const rows = [
    ...columns,
    ...drafts
      .filter((draft) => !columns.some((column) => column.columnId === draft.valueColumnId))
      .map((draft) => ({ columnId: draft.valueColumnId, name: draft.name, physicalType: '' })),
  ]
  return (
    <TransformationSection
      title="Measures"
      configured={drafts.length > 0}
      status={drafts.length ? `${drafts.length} selected` : 'Optional'}
    >
      <p className="meta-hint">
        Select columns to create measures when you save. Choose their aggregation policies; the
        remaining settings are filled automatically.
      </p>
      {loading ? (
        <p role="status">Loading transformed columns…</p>
      ) : !rows.length ? (
        <p className="meta-hint">No supported value columns are available.</p>
      ) : (
        <div className="transaction-measures-scroll">
          <table className="transaction-measures-table">
            <thead>
              <tr>
                <th>Column</th>
                <th>Editable</th>
                <th colSpan={2}>Aggregation</th>
                <th>Dis-Aggregation policy</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((column) => {
                const draft = drafts.find((item) => item.valueColumnId === column.columnId)
                const kind =
                  typedResultType(column.physicalType)?.type ?? draft?.valueType ?? 'number'
                const numeric = numericType(kind)
                const available = columns.some((item) => item.columnId === column.columnId)
                return (
                  <tr key={column.columnId}>
                    <td>
                      <label>
                        <input
                          type="checkbox"
                          checked={!!draft}
                          onChange={(event) =>
                            onChange(
                              event.target.checked
                                ? [
                                    ...drafts,
                                    {
                                      valueColumnId: column.columnId,
                                      name: column.name,
                                      editable: false,
                                      valueType: kind,
                                      nonTime: kind === 'string' ? 'single_value' : '',
                                      time: kind === 'string' ? 'single_value' : '',
                                      allocation: { mode: 'weighted_by_self' },
                                    },
                                  ]
                                : drafts.filter((item) => item.valueColumnId !== column.columnId),
                            )
                          }
                        />{' '}
                        {column.name}
                      </label>
                      {!available && (
                        <span role="alert" className="meta-error">
                          Column no longer available; deselect it.
                        </span>
                      )}
                    </td>
                    <td>
                      <input
                        type="checkbox"
                        aria-label={`${column.name} editable`}
                        disabled={!draft || loading || !numeric}
                        checked={draft?.editable ?? false}
                        onChange={(event) =>
                          update(column.columnId, { editable: event.target.checked })
                        }
                      />
                    </td>
                    {!numeric ? (
                      <td colSpan={2}>
                        <select
                          aria-label={`${column.name} aggregation`}
                          disabled={!draft || loading || kind === 'string'}
                          value={kind === 'string' ? 'single_value' : (draft?.nonTime ?? '')}
                          onChange={(event) =>
                            update(column.columnId, {
                              nonTime: event.target.value as TransactionMeasureDraft['nonTime'],
                              time: event.target.value as TransactionMeasureDraft['time'],
                            })
                          }
                        >
                          {kind !== 'string' && <option value="">Choose…</option>}
                          {aggregationOptions(kind).map(({ value, label }) => (
                            <option key={value} value={value}>
                              {label}
                            </option>
                          ))}
                        </select>
                      </td>
                    ) : (
                      <>
                        <td>
                          <small>Non-time</small>
                          <select
                            aria-label={`${column.name} non-time aggregation`}
                            disabled={!draft || loading}
                            value={draft?.nonTime ?? ''}
                            onChange={(event) =>
                              update(column.columnId, {
                                nonTime: event.target.value as TransactionMeasureDraft['nonTime'],
                              })
                            }
                          >
                            <option value="">Choose…</option>
                            {aggregationOptions(kind).map(({ value, label }) => (
                              <option key={value} value={value}>
                                {label}
                              </option>
                            ))}
                          </select>
                        </td>
                        <td>
                          <small>Time</small>
                          <select
                            aria-label={`${column.name} time aggregation`}
                            disabled={!draft || loading}
                            value={draft?.time ?? ''}
                            onChange={(event) =>
                              update(column.columnId, {
                                time: event.target.value as TransactionMeasureDraft['time'],
                              })
                            }
                          >
                            <option value="">Choose…</option>
                            {aggregationOptions(kind, true).map(({ value, label }) => (
                              <option key={value} value={value}>
                                {label}
                              </option>
                            ))}
                          </select>
                        </td>
                      </>
                    )}
                    <td>
                      {!numeric ? (
                        <span className="meta-hint">Not applicable</span>
                      ) : (
                        <>
                          <select
                            aria-label={`${column.name} dis-aggregation policy`}
                            disabled={!draft || loading}
                            value={draft?.allocation.mode ?? 'weighted_by_self'}
                            onChange={(event) =>
                              update(column.columnId, {
                                allocation: {
                                  mode: event.target.value as NonNullable<
                                    RefDefinition['allocation']
                                  >['mode'],
                                },
                              })
                            }
                          >
                            <option value="weighted_by_self">Weighted by self</option>
                            <option value="none">None — Repeat values</option>
                            <option value="equal">Equal</option>
                            <option value="weighted">Weighted by measure</option>
                          </select>
                          {draft?.allocation.mode === 'weighted' && (
                            <select
                              aria-label={`${column.name} weight measure`}
                              value={draft.allocation.basisMeasureId ?? ''}
                              onChange={(event) =>
                                update(column.columnId, {
                                  allocation: {
                                    mode: 'weighted',
                                    basisMeasureId: event.target.value || null,
                                  },
                                })
                              }
                            >
                              <option value="">Choose measure…</option>
                              {measures.map((measure) => (
                                <option key={measure.objectId} value={measure.objectId}>
                                  {measure.name}
                                </option>
                              ))}
                            </select>
                          )}
                        </>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
      {drafts.length > 0 && !transactionMeasuresComplete(drafts, columns) && (
        <p className="meta-hint">
          Choose both aggregation policies for each selected column and a weight measure when
          required.
        </p>
      )}
    </TransformationSection>
  )
}
