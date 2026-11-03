import { TransformationSection } from './TransformationSection'
import { ObjectPivotEditor } from './ObjectPivotEditor'
import { PlanningResult, type PlanningTableStatus } from '../PlanningTablePanel'
import { metadataApi } from '../api'
import { RawDataPreview } from './RawDataPreview'
import { FieldHelp } from '../../components/FieldHelp'
import { useCallback, useEffect, useRef, useState } from 'react'
import type { Dimension, SourceColumn } from '../types'
import { Actions, Check, options, Select, Text, type EditorProps } from './Fields'

export function DimensionEditor({
  initial,
  data,
  onSave,
  onCancel,
  busy,
  base,
}: EditorProps<Dimension> & { base?: string }) {
  const [value, set] = useState(initial)
  const d = value.definition
  const update = (patch: Partial<typeof d>) =>
    set((current) => ({ ...current, definition: { ...current.definition, ...patch } }))
  const source = data.sources.find((s) => s.objectId === d.sourceId)
  const [inputColumns, setInputColumns] = useState<SourceColumn[] | null>(null)
  const [preview, setPreview] = useState<PlanningTableStatus | null>(null)
  const [previewBusy, setPreviewBusy] = useState(false)
  const [previewError, setPreviewError] = useState('')
  const previewRequest = useRef<AbortController | null>(null)
  useEffect(() => {
    previewRequest.current?.abort()
    setPreview(null)
    setPreviewError('')
    setPreviewBusy(false)
    return () => previewRequest.current?.abort()
  }, [value])
  const reconcileColumns = useCallback((columns: SourceColumn[] | null) => {
    setInputColumns(columns)
    setPreview(null)
    if (!columns) return
    set((current) => {
      const d = current.definition
      const previous = new Map(d.columns.map((c) => [c.sourceColumnId, c]))
      const next = columns.map((c) =>
        previous.has(c.columnId)
          ? { ...previous.get(c.columnId)!, name: c.name }
          : { columnId: c.columnId, name: c.name, displayName: c.name, sourceColumnId: c.columnId },
      )
      const ids = new Set(next.map((c) => c.columnId))
      const oldIds = new Set(d.columns.map((c) => c.columnId))
      const keys = d.keyColumnIds.filter((cid) => ids.has(cid))
      const time =
        d.time && ids.has(d.time.dateColumnId) && ids.has(d.time.orderingColumnId) ? d.time : null
      const definition = {
        ...d,
        columns: next,
        keyColumnIds: keys,
        labelColumnId: ids.has(d.labelColumnId)
          ? d.labelColumnId
          : (keys[0] ?? next[0]?.columnId ?? ''),
        attributeColumnIds: [
          ...new Set([
            ...d.attributeColumnIds.filter((cid) => ids.has(cid)),
            ...next.filter((c) => !oldIds.has(c.columnId)).map((c) => c.columnId),
          ]),
        ],
        time,
      }
      return JSON.stringify(definition) === JSON.stringify(d)
        ? current
        : { ...current, definition, isTimeDim: !!time }
    })
  }, [])
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
      <TransformationSection title="Pivot transformation" configured={!!d.pivot?.enabled}>
        <ObjectPivotEditor
          key={d.sourceId}
          objectId={value.objectId}
          source={source}
          base={base}
          kind="dimensions"
          value={d.pivot}
          onChange={(pivot) => {
            update({ pivot })
            setPreview(null)
          }}
          onColumns={reconcileColumns}
          disabled={busy || previewBusy}
        />
      </TransformationSection>
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
                  (inputColumns ?? source?.definition.columns ?? []).find(
                    (c) => c.columnId === column.sourceColumnId,
                  )?.nullable ?? false
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

      {previewError && (
        <p className="meta-error" role="alert">
          {previewError}
        </p>
      )}
      {preview && (
        <PlanningResult data={preview} name={value.name + ' — transformed'} showRefresh={false} />
      )}
      <RawDataPreview
        source={source}
        disabled={busy || previewBusy}
        renderActions={(rawButton) => (
          <Actions
            busy={busy || previewBusy}
            canSave={d.keyColumnIds.length > 0 && (!d.pivot?.enabled || !!inputColumns)}
            onCancel={onCancel}
          >
            {rawButton}
            <button
              type="button"
              className="secondary-button"
              disabled={
                !base ||
                !source ||
                !d.keyColumnIds.length ||
                (d.pivot?.enabled && !inputColumns) ||
                previewBusy ||
                busy
              }
              onClick={async () => {
                const controller = new AbortController()
                previewRequest.current?.abort()
                previewRequest.current = controller
                setPreviewBusy(true)
                setPreviewError('')
                setPreview(null)
                try {
                  const result = await metadataApi.request<PlanningTableStatus>(
                    base + '/dimensions/preview',
                    'POST',
                    {
                      ...value,
                      displayName: value.name,
                      definition: { ...d, attributeColumnIds: [...exposed] },
                    },
                    undefined,
                    controller.signal,
                  )
                  if (!controller.signal.aborted) setPreview(result)
                } catch (e) {
                  if (!controller.signal.aborted)
                    setPreviewError(e instanceof Error ? e.message : 'Preview failed.')
                } finally {
                  if (!controller.signal.aborted) setPreviewBusy(false)
                }
              }}
            >
              {previewBusy ? 'Loading preview…' : 'Preview'}
            </button>
          </Actions>
        )}
      />
    </form>
  )
}
