import { useEffect, useRef } from 'react'
import { metadataApi } from '../api'
import { PlanningResult, type PlanningTableStatus } from '../PlanningTablePanel'
import {
  TransactionResolutionEditor,
  newResolution,
  resolutionComplete,
} from './TransactionResolutionEditor'
import { FieldHelp } from '../../components/FieldHelp'
import { useState } from 'react'
import { TransactionCalendarEditor } from './TransactionCalendarEditor'
import type { SourceColumn, Transaction } from '../types'
import { Actions, IdentityFields, options, Select, Check, type EditorProps } from './Fields'
export function TransactionEditor({
  initial,
  data,
  editing,
  onSave,
  onCancel,
  busy,
  base,
}: EditorProps<Transaction> & { base?: string }) {
  const [value, set] = useState(() =>
    initial.definition.resolution
      ? {
          ...initial,
          definition: {
            ...initial.definition,
            resolution: {
              ...initial.definition.resolution,
              orderColumnIds: initial.definition.resolution.orderColumnIds.slice(0, 1),
              partitionColumnIds: [],
            },
          },
        }
      : initial,
  )
  const [calendarColumns, setCalendarColumns] = useState<SourceColumn[]>([])
  const form = useRef<HTMLFormElement>(null)
  const [preview, setPreview] = useState<
    (PlanningTableStatus & { diagnostics: Record<string, number> }) | null
  >(null)
  const [previewBusy, setPreviewBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => {
    setPreview(null)
    setError('')
  }, [value])
  const d = value.definition
  const update = (patch: Partial<typeof d>) => set({ ...value, definition: { ...d, ...patch } })
  const level = data.planningLevels.find((l) => l.objectId === d.planningLevelId)
  const source = data.sources.find((s) => s.objectId === d.sourceId)
  return (
    <form
      ref={form}
      onSubmit={(e) => {
        e.preventDefault()
        void onSave(value)
      }}
    >
      <fieldset disabled={busy || previewBusy}>
        <IdentityFields value={value} editing={editing} onChange={(v) => set({ ...value, ...v })} />
        <div className="meta-grid">
          <Select
            label="Managed table"
            value={d.sourceId}
            items={options(data.sources.filter((s) => s.definition.inScope))}
            onChange={(sourceId) =>
              update({
                sourceId,
                calendarResolution: null,
                mappingMode: 'direct',
                resolution: null,
                keyMappings: d.keyMappings.map((m) => ({ ...m, sourceColumnId: '' })),
              })
            }
          />
          <Select
            label="Native planning level"
            value={d.planningLevelId}
            items={options(data.planningLevels)}
            onChange={(planningLevelId) => {
              const l = data.planningLevels.find((l) => l.objectId === planningLevelId)
              update({
                planningLevelId,
                resolution: d.mappingMode === 'resolve_multi_grain' && l ? newResolution(l) : null,
                keyMappings:
                  d.mappingMode === 'resolve_multi_grain'
                    ? []
                    : (l?.definition.grain.keyIds ?? []).map((grainColumnId) => ({
                        grainColumnId,
                        sourceColumnId: '',
                      })),
              })
            }}
          />
        </div>

        {!d.resolution?.axes.some((a) => a.dateRange) && (
          <>
            <Check
              label="Resolve a date range using a calendar"
              value={!!d.calendarResolution}
              onChange={(enabled) => {
                setCalendarColumns([])
                update({
                  calendarResolution: enabled
                    ? { dimensionId: '', startColumnId: '', endColumnId: '', selectedColumnIds: [] }
                    : null,
                  resolution: d.resolution
                    ? {
                        ...d.resolution,
                        values: d.resolution.values.map((v) => ({
                          ...v,
                          timeMode: enabled ? 'copy' : null,
                        })),
                      }
                    : d.resolution,
                })
              }}
            />
            {d.calendarResolution && source && (
              <TransactionCalendarEditor
                value={d.calendarResolution}
                onChange={(calendarResolution) => update({ calendarResolution })}
                columns={source.definition.columns}
                onColumns={setCalendarColumns}
                base={base}
                sourceId={source.objectId}
                transactionId={value.objectId}
                levelId={d.planningLevelId}
              />
            )}
          </>
        )}
        <Select
          label="Transaction mapping"
          value={d.mappingMode}
          items={[
            { value: 'direct', label: 'Direct mapping' },
            { value: 'resolve_multi_grain', label: 'Resolve multi grain' },
          ]}
          disabled={!level || !source}
          onChange={(mode) =>
            update({
              mappingMode: mode as typeof d.mappingMode,
              resolution: mode === 'resolve_multi_grain' && level ? newResolution(level) : null,
              keyMappings:
                mode === 'resolve_multi_grain'
                  ? []
                  : (level?.definition.grain.keyIds ?? []).map((grainColumnId) => ({
                      grainColumnId,
                      sourceColumnId: '',
                    })),
            })
          }
        />
        {d.mappingMode === 'resolve_multi_grain' && d.resolution && level && source ? (
          <>
            <TransactionResolutionEditor
              value={d.resolution}
              onChange={(resolution) => update({ resolution })}
              columns={d.calendarResolution ? calendarColumns : source.definition.columns}
              calendarResolved={!!d.calendarResolution}
              level={level}
              base={base}
              sourceId={source.objectId}
            />
            <div className="meta-toolbar meta-subsection">
              <button
                type="button"
                className="secondary-button"
                disabled={!base || busy || previewBusy || !resolutionComplete(d.resolution)}
                onClick={async () => {
                  if (!form.current?.reportValidity()) return
                  setPreviewBusy(true)
                  setError('')
                  try {
                    setPreview(
                      await metadataApi.request(
                        base + '/transactions/resolution/preview',
                        'POST',
                        value,
                      ),
                    )
                  } catch (error) {
                    setError(error instanceof Error ? error.message : 'Resolution preview failed.')
                  } finally {
                    setPreviewBusy(false)
                  }
                }}
              >
                {previewBusy ? 'Resolving…' : 'Preview resolved data'}
              </button>
              <span className="meta-hint">
                Preview validates all assignments and shows at most 100 resolved rows. It does not
                save metadata.
              </span>
            </div>
            {error && (
              <p className="meta-error" role="alert">
                {error}
              </p>
            )}
            {preview && (
              <>
                <p className="meta-hint" role="status">
                  {preview.diagnostics.inputRows} input rows · {preview.diagnostics.resolvedRows}{' '}
                  winning rows · {preview.diagnostics.overwrittenMatches} overwritten matches ·{' '}
                  {preview.diagnostics.shadowedAssignments} earlier identical assignments replaced
                </p>
                <PlanningResult
                  data={preview}
                  name={value.name + ' — resolved'}
                  showRefresh={false}
                />
              </>
            )}
          </>
        ) : (
          <>
            <h3>
              Map every grain key
              <FieldHelp label="Map every grain key" />
            </h3>
            <div className="meta-grid">
              {d.keyMappings.map((m, i) => (
                <Select
                  key={m.grainColumnId}
                  label={
                    'Source column for ' +
                    (level?.definition.grain.columns.find(
                      (c) => c.grainColumnId === m.grainColumnId,
                    )?.name ?? m.grainColumnId)
                  }
                  value={m.sourceColumnId}
                  items={(d.calendarResolution
                    ? calendarColumns
                    : (source?.definition.columns ?? [])
                  ).map((c) => ({
                    value: c.columnId,
                    label: c.name,
                  }))}
                  onChange={(sourceColumnId) =>
                    update({
                      keyMappings: d.keyMappings.map((x, j) =>
                        i === j ? { ...x, sourceColumnId } : x,
                      ),
                    })
                  }
                />
              ))}
            </div>
            <p className="meta-hint">
              Transaction data will be aggregated by these native planning-level keys before it is
              left joined to the planning level.
            </p>
          </>
        )}
      </fieldset>
      <Actions
        busy={busy || previewBusy}
        canSave={
          (!d.calendarResolution || calendarColumns.length > 0) &&
          (d.mappingMode === 'direct' || resolutionComplete(d.resolution))
        }
        onCancel={onCancel}
      />
    </form>
  )
}
