import { ViewSqlButton } from '../../components/ViewSqlButton'
import { useDimensionCatalog } from '../useDimensionCatalog'
import {
  TransactionMeasureEditor,
  measureValueColumns,
  transactionMeasuresComplete,
  transactionMeasureSelections,
  type TransactionMeasureDraft,
} from './TransactionMeasureEditor'
import { FilterEditor } from './FilterEditor'
import { useFilterColumns } from './useFilterColumns'
import { TransformationsPanel } from './TransformationsPanel'
import { ObjectPivotEditor } from './ObjectPivotEditor'
import { RawDataPreview } from './RawDataPreview'
import { useEffect, useRef } from 'react'
import { metadataApi } from '../api'
import { PlanningResult, type DimensionTableStatus } from '../DimensionTablePanel'
import {
  TransactionResolutionEditor,
  newResolution,
  resolutionComplete,
} from './TransactionResolutionEditor'
import { FieldHelp } from '../../components/FieldHelp'
import { useState } from 'react'
import { TransactionCalendarEditor } from './TransactionCalendarEditor'
import { type Dimension, type SourceColumn, type Transaction } from '../types'
import { Actions, IdentityFields, options, Select, Check, Checks, type EditorProps } from './Fields'
export function TransactionEditor({
  initial,
  data,
  editing,
  onSave,
  onCancel,
  busy,
  base,
}: EditorProps<Transaction> & {
  base?: string
  onCreated?: (transaction: Transaction & { version: number }, close: boolean) => void
}) {
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
  const dimensionMembers = useDimensionCatalog(data, base)
  const [measureDrafts, setMeasureDrafts] = useState<TransactionMeasureDraft[]>([])
  const [createGrain, setCreateGrain] = useState(!editing || !initial.definition.dimensionId)
  const [grainKeys, setGrainKeys] = useState<string[]>(
    initial.definition.grainColumnIds ??
      initial.definition.keyMappings.map((mapping) => mapping.sourceColumnId),
  )
  const [onboardingBusy, setOnboardingBusy] = useState(false)
  const [createdLevel, setCreatedLevel] = useState<Dimension | null>(null)
  const [calendarColumns, setCalendarColumns] = useState<SourceColumn[]>([])
  const [pivotColumns, setPivotColumns] = useState<SourceColumn[] | null>(null)
  const form = useRef<HTMLFormElement>(null)
  const [preview, setPreview] = useState<
    (DimensionTableStatus & { diagnostics?: Record<string, number> }) | null
  >(null)
  const [previewBusy, setPreviewBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => {
    setPreview(null)
    setError('')
  }, [value])
  const d = value.definition
  const update = (patch: Partial<typeof d>) =>
    set((current) => ({ ...current, definition: { ...current.definition, ...patch } }))
  const level = dimensionMembers.levels.find((l) => l.objectId === d.dimensionId) ?? createdLevel
  const source = data.sources.find((s) => s.objectId === d.sourceId)
  const pivotReady = !d.pivot?.enabled || pivotColumns !== null
  const inputColumns = d.pivot?.enabled ? (pivotColumns ?? []) : (source?.definition.columns ?? [])
  const grainColumns = d.calendarResolution ? calendarColumns : inputColumns
  const measureColumns = measureValueColumns(
    !createGrain && d.mappingMode === 'resolve_multi_grain'
      ? grainColumns
          .filter(
            (column) =>
              d.resolution?.values.some((value) => value.sourceColumnId === column.columnId) ||
              !!d.resolution?.passthroughColumnIds.includes(column.columnId),
          )
          .map((column) => ({
            ...column,
            physicalType: d.resolution?.values.some(
              (value) => value.sourceColumnId === column.columnId,
            )
              ? 'Float64'
              : column.physicalType,
          }))
      : grainColumns,
  )
  const measuresReady = editing || transactionMeasuresComplete(measureDrafts, measureColumns)
  const grainColumnSignature = JSON.stringify(grainColumns.map((c) => c.columnId))
  useEffect(() => {
    if (!pivotReady || (d.calendarResolution && !calendarColumns.length)) return
    const ids = new Set<string>(JSON.parse(grainColumnSignature))
    setGrainKeys((keys) => keys.filter((key) => ids.has(key)))
  }, [grainColumnSignature, pivotReady, !!d.calendarResolution, calendarColumns.length])
  const filterColumns = useFilterColumns(
    base ? base + '/transactions/resolution/schema' : undefined,
    { ...value, definition: { ...d, filters: null } },
    !createGrain && !!d.filters,
  )
  const grainPayload = (includeMeasures = false) => ({
    ...(includeMeasures ? { measures: transactionMeasureSelections(measureDrafts) } : {}),
    objectId: value.objectId,
    kind: 'transaction',
    name: value.name,
    displayName: value.displayName,
    description: value.description,
    sourceId: d.sourceId,
    pivot: d.pivot ?? null,
    calendarResolution: d.calendarResolution ?? null,
    filters: d.filters ?? null,
    grainColumnIds: grainKeys,
  })
  async function saveTransaction() {
    if (!measuresReady || (createGrain && !grainKeys.length)) return
    const definition = createGrain
      ? {
          ...d,
          dimensionId: null,
          grainColumnIds: grainKeys,
          keyMappings: [],
          mappingMode: 'direct' as const,
          resolution: null,
        }
      : {
          ...d,
          grainColumnIds:
            d.mappingMode === 'direct'
              ? d.keyMappings.map((mapping) => mapping.sourceColumnId)
              : [],
        }
    await onSave({
      ...value,
      definition,
      ...(!editing ? { measures: transactionMeasureSelections(measureDrafts) } : {}),
    })
  }
  return (
    <form
      ref={form}
      onSubmit={(e) => {
        e.preventDefault()
        void saveTransaction()
      }}
    >
      <fieldset disabled={busy || previewBusy || onboardingBusy}>
        <IdentityFields value={value} editing={editing} onChange={(v) => set({ ...value, ...v })} />
        <div className="meta-grid">
          <Select
            label="Managed table"
            value={d.sourceId}
            items={options(data.sources.filter((s) => s.definition.inScope))}
            onChange={(sourceId) => {
              setMeasureDrafts([])
              setGrainKeys([])
              update({
                sourceId,
                pivot: null,
                filters: null,
                calendarResolution: null,
                mappingMode: 'direct',
                resolution: null,
                keyMappings: d.keyMappings.map((m) => ({ ...m, sourceColumnId: '' })),
              })
            }}
          />
        </div>

        <TransformationsPanel
          label="Transaction transformations"
          tabs={[
            {
              label: 'Filters',
              configured: !!d.filters,
              content: (
                <FilterEditor
                  embedded
                  value={d.filters}
                  onChange={(filters) => update({ filters })}
                  {...(createGrain
                    ? {
                        columns: grainColumns,
                        loading: !pivotReady || (!!d.calendarResolution && !calendarColumns.length),
                        error: '',
                      }
                    : filterColumns)}
                />
              ),
            },
            {
              label: 'Pivot Data',
              configured: !!d.pivot?.enabled,
              content: (
                <ObjectPivotEditor
                  showConfiguration
                  key={d.sourceId}
                  objectId={value.objectId}
                  source={source}
                  base={base}
                  kind="transactions"
                  value={d.pivot}
                  onChange={(pivot) => update({ pivot })}
                  onColumns={setPivotColumns}
                  disabled={busy || previewBusy || onboardingBusy}
                />
              ),
            },
            {
              label: 'Date Resolution',
              configured: !!d.calendarResolution,
              content: (
                <>
                  {!d.resolution?.axes.some((a) => a.dateRange) && (
                    <>
                      {source && pivotReady && (
                        <TransactionCalendarEditor
                          configured={!!d.calendarResolution}
                          value={
                            d.calendarResolution ?? {
                              memberCatalogId: '',
                              startColumnId: '',
                              endColumnId: '',
                              selectedColumnIds: [],
                            }
                          }
                          onChange={(calendarResolution) => {
                            setCalendarColumns([])
                            update({
                              calendarResolution,
                              resolution: d.resolution
                                ? {
                                    ...d.resolution,
                                    values: d.resolution.values.map((v) => ({
                                      ...v,
                                      timeMode: 'copy',
                                    })),
                                  }
                                : d.resolution,
                            })
                          }}
                          columns={inputColumns}
                          pivot={d.pivot}
                          onColumns={setCalendarColumns}
                          base={base}
                          sourceId={source.objectId}
                          transactionId={value.objectId}
                          levelId={d.dimensionId ?? ''}
                        />
                      )}
                      {d.calendarResolution && (
                        <button
                          type="button"
                          className="secondary-button"
                          onClick={() => {
                            setCalendarColumns([])
                            update({
                              calendarResolution: null,
                              resolution: d.resolution
                                ? {
                                    ...d.resolution,
                                    values: d.resolution.values.map((v) => ({
                                      ...v,
                                      timeMode: null,
                                    })),
                                  }
                                : d.resolution,
                            })
                          }}
                        >
                          Clear date resolution
                        </button>
                      )}
                    </>
                  )}
                </>
              ),
            },
          ]}
        />
        <section className="transaction-grain-section" aria-label="Grain Selection">
          <div className="transaction-grain-heading">
            <h3>Grain Selection</h3>
            <span className="badge">Required</span>
          </div>
          <fieldset className="measure-type-control" disabled={!source || !pivotReady}>
            <legend>
              Transaction mapping <FieldHelp label="Transaction mapping" />
            </legend>
            <div className="measure-type-switch">
              {(
                [
                  ['create_grain', 'Grain keys only'],
                  ['direct', 'Bind to a Dimension'],
                ] as const
              ).map(([mode, label]) => (
                <label className="measure-type-option" key={mode}>
                  <input
                    type="radio"
                    name="transaction-mapping"
                    value={mode}
                    checked={createGrain ? mode === 'create_grain' : mode === 'direct'}
                    onChange={() => {
                      setCreateGrain(mode === 'create_grain')
                      update({
                        mappingMode: 'direct',
                        resolution: null,
                        keyMappings: (level?.definition.grain.keyIds ?? []).map(
                          (grainColumnId) => ({
                            grainColumnId,
                            sourceColumnId: '',
                          }),
                        ),
                      })
                    }}
                  />
                  <span>{label}</span>
                </label>
              ))}
            </div>
          </fieldset>
          {!createGrain && (
            <Select
              label="Dimension"
              value={dimensionMembers.selection(d.dimensionId ?? '')}
              items={dimensionMembers.items}
              onChange={async (memberCatalogId) => {
                setOnboardingBusy(true)
                setError('')
                try {
                  const l = await dimensionMembers.resolve(memberCatalogId)
                  setCreatedLevel(l)
                  update({
                    dimensionId: l.objectId,
                    resolution:
                      d.mappingMode === 'resolve_multi_grain' && l ? newResolution(l) : null,
                    keyMappings:
                      d.mappingMode === 'resolve_multi_grain'
                        ? []
                        : (l?.definition.grain.keyIds ?? []).map((grainColumnId) => ({
                            grainColumnId,
                            sourceColumnId: '',
                          })),
                  })
                } catch (error) {
                  setError(error instanceof Error ? error.message : 'Could not select Dimension.')
                } finally {
                  setOnboardingBusy(false)
                }
              }}
            />
          )}
          {!createGrain && (
            <Check
              label="Resolve multi-grain data"
              value={d.mappingMode === 'resolve_multi_grain'}
              disabled={!source || !pivotReady || !level}
              onChange={(resolve) =>
                update({
                  mappingMode: resolve ? 'resolve_multi_grain' : 'direct',
                  resolution: resolve && level ? newResolution(level) : null,
                  keyMappings: resolve
                    ? []
                    : (level?.definition.grain.keyIds ?? []).map((grainColumnId) => ({
                        grainColumnId,
                        sourceColumnId: '',
                      })),
                })
              }
            />
          )}
          {createGrain ? (
            <>
              <p className="meta-hint">
                Select the grain keys used to group measure values. Multiple source records at the
                same grain are allowed.
              </p>
              <Checks
                label="Transaction grain columns"
                selected={grainKeys}
                items={(d.calendarResolution ? calendarColumns : inputColumns).map((c) => ({
                  value: c.columnId,
                  label: c.name,
                }))}
                onChange={setGrainKeys}
              />
              <Select
                label="Time key (optional)"
                required={false}
                value={d.timeColumnId ?? ''}
                items={grainKeys.map((key) => ({
                  value: key,
                  label: grainColumns.find((column) => column.columnId === key)?.name ?? key,
                }))}
                onChange={(timeColumnId) => update({ timeColumnId: timeColumnId || null })}
              />
            </>
          ) : d.mappingMode === 'resolve_multi_grain' && d.resolution && level && source ? (
            <>
              <TransactionResolutionEditor
                value={d.resolution}
                onChange={(resolution) => update({ resolution })}
                columns={d.calendarResolution ? calendarColumns : inputColumns}
                calendarResolved={!!d.calendarResolution}
                level={level}
                base={base}
                sourceId={source.objectId}
              />
            </>
          ) : (
            <>
              <h3>
                Map every grain key
                <FieldHelp label="Map every grain key" />
              </h3>
              <div className="transaction-key-mappings">
                {d.keyMappings.map((m, i) => (
                  <Select
                    key={m.grainColumnId}
                    label={
                      (level?.name ?? 'Dimension') +
                      '.' +
                      (level?.definition.grain.columns.find(
                        (c) => c.grainColumnId === m.grainColumnId,
                      )?.name ?? m.grainColumnId)
                    }
                    value={m.sourceColumnId}
                    items={(d.calendarResolution ? calendarColumns : inputColumns).map((c) => ({
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
                Transaction data will be aggregated by these native dimension keys before it is left
                joined to the Dimension.
              </p>
            </>
          )}
        </section>
        {!editing && (
          <TransactionMeasureEditor
            columns={measureColumns}
            drafts={measureDrafts}
            onChange={setMeasureDrafts}
            measures={data.measures}
            loading={!pivotReady || (!!d.calendarResolution && !calendarColumns.length)}
          />
        )}

        {error && (
          <p className="meta-error" role="alert">
            {error}
          </p>
        )}
        {preview && (
          <>
            {preview.diagnostics && (
              <p className="meta-hint" role="status">
                {preview.diagnostics.inputRows} input rows · {preview.diagnostics.resolvedRows}{' '}
                winning rows · {preview.diagnostics.overwrittenMatches} overwritten matches ·{' '}
                {preview.diagnostics.shadowedAssignments} earlier identical assignments replaced
                {!!preview.diagnostics.filteredOutRows && (
                  <> · {preview.diagnostics.filteredOutRows} rows removed by filters</>
                )}
              </p>
            )}
            <PlanningResult
              data={preview}
              name={value.name + ' — transformed'}
              showRefresh={false}
            />
          </>
        )}
      </fieldset>
      <RawDataPreview
        source={source}
        disabled={busy || previewBusy || onboardingBusy}
        renderActions={(rawButton) => (
          <Actions
            busy={busy || previewBusy || onboardingBusy}
            canSave={
              measuresReady &&
              (createGrain
                ? grainKeys.length > 0 &&
                  pivotReady &&
                  (!d.calendarResolution || calendarColumns.length > 0)
                : pivotReady &&
                  (!d.calendarResolution || calendarColumns.length > 0) &&
                  (d.mappingMode === 'direct' || resolutionComplete(d.resolution)))
            }
            onCancel={onCancel}
          >
            {rawButton}
            <button
              type="button"
              className="secondary-button"
              disabled={
                !base ||
                busy ||
                previewBusy ||
                onboardingBusy ||
                (createGrain && !grainKeys.length) ||
                !pivotReady ||
                (d.mappingMode === 'resolve_multi_grain' && !resolutionComplete(d.resolution)) ||
                (!!d.calendarResolution && !calendarColumns.length)
              }
              onClick={async () => {
                if (!form.current?.reportValidity()) return
                setPreviewBusy(true)
                setError('')
                try {
                  setPreview(
                    await metadataApi.request(
                      base +
                        (createGrain
                          ? '/transactions/from-grain/preview'
                          : '/transactions/resolution/preview'),
                      'POST',
                      createGrain ? grainPayload() : value,
                    ),
                  )
                } catch (error) {
                  setError(error instanceof Error ? error.message : 'Resolution preview failed.')
                } finally {
                  setPreviewBusy(false)
                }
              }}
            >
              {previewBusy ? 'Loading preview…' : 'Preview'}
            </button>
            <ViewSqlButton
              disabled={
                !base ||
                busy ||
                previewBusy ||
                onboardingBusy ||
                (createGrain && !grainKeys.length) ||
                !pivotReady ||
                (d.mappingMode === 'resolve_multi_grain' && !resolutionComplete(d.resolution)) ||
                (!!d.calendarResolution && !calendarColumns.length)
              }
              loadSql={(signal) => {
                if (!form.current?.reportValidity())
                  throw new Error('Complete the required fields first.')
                return metadataApi.request(
                  base +
                    (createGrain
                      ? '/transactions/from-grain/preview'
                      : '/transactions/resolution/preview') +
                    '?sql_only=true',
                  'POST',
                  createGrain ? grainPayload() : value,
                  undefined,
                  signal,
                )
              }}
            />
          </Actions>
        )}
      />
    </form>
  )
}
