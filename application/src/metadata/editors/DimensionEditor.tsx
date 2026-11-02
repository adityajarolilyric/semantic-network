import { FieldHelp } from '../../components/FieldHelp'
import { useState } from 'react'
import type { Dimension } from '../types'
import { Actions, Check, options, Select, Text, type EditorProps } from './Fields'

export function DimensionEditor({ initial, data, onSave, onCancel, busy }: EditorProps<Dimension>) {
  const [value, set] = useState(initial)
  const d = value.definition
  const update = (patch: Partial<typeof d>) => set({ ...value, definition: { ...d, ...patch } })
  const source = data.sources.find((s) => s.objectId === d.sourceId)
  const cols = d.columns.map((c) => ({ value: c.columnId, label: c.displayName }))
  const exposed = new Set([...d.keyColumnIds, ...d.attributeColumnIds])
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        void onSave({
          ...value,
          displayName: value.name,
          definition: { ...d, attributeColumnIds: [...exposed] },
        })
      }}
    >
      <div className="meta-grid">
        <Text
          label="Dimension name"
          value={value.name}
          onChange={(name) => set({ ...value, name, displayName: name })}
        />
        <Select
          label="Managed table"
          value={d.sourceId}
          items={options(data.sources.filter((s) => s.definition.inScope))}
          onChange={(sourceId) => {
            const s = data.sources.find((s) => s.objectId === sourceId)
            if (!s) return
            set({
              ...value,
              isTimeDim: false,
              definition: {
                sourceId,
                columns: s.definition.columns.map((c) => ({
                  columnId: c.columnId,
                  name: c.name,
                  displayName: c.name,
                  sourceColumnId: c.columnId,
                })),
                keyColumnIds: s.definition.keyColumns,
                labelColumnId:
                  s.definition.keyColumns[0] ?? s.definition.columns[0]?.columnId ?? '',
                attributeColumnIds: s.definition.columns.map((c) => c.columnId),
                time: null,
                filters: null,
              },
            })
          }}
        />
      </div>
      {d.columns.length > 0 && (
        <div className="dimension-columns-wrap">
          <table className="dimension-columns">
            <thead>
              <tr>
                <th>Source column</th>
                <th aria-label="Display name">
                  Display name
                  <FieldHelp label="Display name" />
                </th>
                <th className="column-toggle" aria-label="Key">
                  Key
                  <FieldHelp label="Key" />
                </th>
                <th className="column-toggle" aria-label="Exposed">
                  Exposed
                  <FieldHelp label="Exposed" />
                </th>
              </tr>
            </thead>
            <tbody>
              {d.columns.map((column, index) => {
                const isKey = d.keyColumnIds.includes(column.columnId)
                const nullable =
                  source?.definition.columns.find((c) => c.columnId === column.sourceColumnId)
                    ?.nullable ?? false
                return (
                  <tr key={column.columnId}>
                    <td>
                      <span title={column.name}>{column.name}</span>
                      {nullable && <small>Nullable</small>}
                    </td>
                    <td>
                      <input
                        aria-label={'Display name for ' + column.name}
                        value={column.displayName}
                        required
                        maxLength={200}
                        onChange={(e) =>
                          update({
                            columns: d.columns.map((c, i) =>
                              i === index ? { ...c, displayName: e.target.value } : c,
                            ),
                          })
                        }
                      />
                    </td>
                    <td className="column-toggle">
                      <input
                        type="checkbox"
                        aria-label={'Key ' + column.name}
                        checked={isKey}
                        disabled={nullable}
                        title={nullable ? 'Nullable columns cannot be keys' : undefined}
                        onChange={(e) => {
                          const keyColumnIds = e.target.checked
                            ? [...d.keyColumnIds, column.columnId]
                            : d.keyColumnIds.filter((k) => k !== column.columnId)
                          update({
                            keyColumnIds,
                            attributeColumnIds: [
                              ...new Set([...d.attributeColumnIds, ...keyColumnIds]),
                            ],
                            labelColumnId: d.labelColumnId || column.columnId,
                          })
                        }}
                      />
                    </td>
                    <td className="column-toggle">
                      <input
                        type="checkbox"
                        aria-label={'Expose ' + column.name}
                        checked={exposed.has(column.columnId)}
                        disabled={isKey}
                        title={isKey ? 'Key columns are always exposed' : undefined}
                        onChange={(e) =>
                          update({
                            attributeColumnIds: e.target.checked
                              ? [...new Set([...d.attributeColumnIds, column.columnId])]
                              : d.attributeColumnIds.filter((k) => k !== column.columnId),
                          })
                        }
                      />
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
      {d.columns.length > 0 && (
        <p className="meta-hint">Select at least one key column. Key columns are always exposed.</p>
      )}
      <Check
        label="Time dimension"
        value={value.isTimeDim}
        onChange={(isTimeDim) =>
          set({
            ...value,
            isTimeDim,
            definition: {
              ...d,
              time: isTimeDim
                ? {
                    baseGrain: 'day',
                    dateColumnId: d.keyColumnIds[0] ?? '',
                    orderingColumnId: d.keyColumnIds[0] ?? '',
                  }
                : null,
            },
          })
        }
      />
      {value.isTimeDim && d.time && (
        <div className="meta-grid">
          <Select
            label="Time grain"
            value={d.time.baseGrain}
            items={['day', 'week', 'month']}
            onChange={(v) =>
              update({ time: { ...d.time!, baseGrain: v as 'day' | 'week' | 'month' } })
            }
          />
          <Select
            label="Date column"
            value={d.time.dateColumnId}
            items={cols}
            onChange={(dateColumnId) => update({ time: { ...d.time!, dateColumnId } })}
          />
          <Select
            label="Ordering column"
            value={d.time.orderingColumnId}
            items={cols}
            onChange={(orderingColumnId) => update({ time: { ...d.time!, orderingColumnId } })}
          />
        </div>
      )}
      <details className="meta-advanced">
        <summary>More settings</summary>
        <Select
          label="Member label column"
          value={d.labelColumnId}
          items={cols}
          onChange={(labelColumnId) => update({ labelColumnId })}
        />
        <Text
          label="Description"
          value={value.description}
          required={false}
          onChange={(description) => set({ ...value, description })}
        />
      </details>
      <Actions busy={busy} canSave={d.keyColumnIds.length > 0} onCancel={onCancel} />
    </form>
  )
}
