import { DimensionEditor } from './DimensionEditor'
import {
  newTransactionPopulation,
  suggestPopulationMappings,
  type TransactionPopulationSave,
} from '../transactionPopulation'
import { inputOf } from '../types'
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
import { Actions, IdentityFields, options, Select, Check, type EditorProps } from './Fields'
export function TransactionEditor({
  initial,
  data,
  editing,
  onSave,
  onCancel,
  busy,
  base,
  baseDimensionId,
  revisionVersion,
  savedVersion,
  onSavePopulation,
}: EditorProps<Transaction> & {
  base?: string
  baseDimensionId?: string | null
  revisionVersion?: number
  savedVersion?: number
  onSavePopulation?: (command: TransactionPopulationSave) => Promise<void>
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
  const [populationMode, setPopulationMode] = useState<'rows' | 'reuse' | 'custom'>(
    initial.definition.dimensionId ? 'reuse' : 'rows',
  )
  const [populationDraft, setPopulationDraft] = useState<Dimension>(() => {
    const draft = newTransactionPopulation(initial)
    const keys = initial.definition.grainColumnIds ?? []
    draft.definition.grain = {
      keyIds: keys,
      columns: keys.map((id) => ({
        grainColumnId: id,
        name:
          data.sources
            .find((source) => source.objectId === initial.definition.sourceId)
            ?.definition.columns.find((column) => column.columnId === id)?.name ?? id,
        populationRef: { columnId: id },
      })),
    }
    return draft
  })
  const [populationVersion, setPopulationVersion] = useState<number | null>(null)
  const [useAsBase, setUseAsBase] = useState(!baseDimensionId)
  const createGrain = populationMode === 'rows'
  const creatingPopulation = populationMode !== 'reuse'
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
  }, [value, populationDraft, populationMode, useAsBase])
  const d = value.definition
  const update = (patch: Partial<typeof d>) =>
    set((current) => ({ ...current, definition: { ...current.definition, ...patch } }))
  const level = creatingPopulation
    ? populationDraft
    : (dimensionMembers.levels.find((l) => l.objectId === d.dimensionId) ?? createdLevel)
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
  const mappingSignature = JSON.stringify(level?.definition.grain.columns ?? [])
  const inputSignature = JSON.stringify(
    grainColumns.map((column) => [column.columnId, column.name]),
  )
  useEffect(() => {
    if (
      createGrain ||
      d.mappingMode !== 'direct' ||
      !level ||
      !pivotReady ||
      (d.calendarResolution && !calendarColumns.length)
    )
      return
    const mappings = suggestPopulationMappings(level, grainColumns, d.keyMappings)
    if (JSON.stringify(mappings) !== JSON.stringify(d.keyMappings))
      update({ keyMappings: mappings })
  }, [
    mappingSignature,
    inputSignature,
    pivotReady,
    calendarColumns.length,
    populationMode,
    level?.objectId,
    createGrain,
    d.mappingMode,
    d.dimensionId,
  ])

  const filterColumns = useFilterColumns(
    base ? base + '/transactions/population/schema' : undefined,
    {
      ...populationCommand(),
      transaction: {
        ...populationCommand().transaction,
        definition: { ...transactionDefinition(), filters: null },
      },
    },
    !createGrain && !!d.filters,
  )
  function transactionDefinition() {
    const target = level
    const keyMappings = createGrain
      ? populationDraft.definition.grain.keyIds.map((grainColumnId) => ({
          grainColumnId,
          sourceColumnId: populationDraft.definition.grain.columns.find(
            (column) => column.grainColumnId === grainColumnId,
          )!.populationRef.columnId!,
        }))
      : d.keyMappings
    return {
      ...d,
      timeColumnId:
        createGrain &&
        d.timeColumnId &&
        !keyMappings.some((mapping) => mapping.sourceColumnId === d.timeColumnId)
          ? null
          : d.timeColumnId,
      dimensionId: target?.objectId ?? null,
      grainColumnIds:
        d.mappingMode === 'direct' || createGrain
          ? keyMappings.map((mapping) => mapping.sourceColumnId)
          : [],
      keyMappings: createGrain || d.mappingMode === 'direct' ? keyMappings : [],
      mappingMode: createGrain ? ('direct' as const) : d.mappingMode,
      resolution: createGrain ? null : d.resolution,
    }
  }
  function populationCommand(includeMeasures = false): TransactionPopulationSave {
    const population = creatingPopulation ? inputOf(populationDraft) : null
    if (population && createGrain) {
      population.definition = {
        ...population.definition,
        population: {
          operation: 'source',
          sourceId: d.sourceId,
          inputTransform: {
            objectId: value.objectId,
            pivot: d.pivot ?? null,
            calendarResolution: d.calendarResolution ?? null,
            filters: d.filters ?? null,
          },
        },
      }
    }
    return {
      transaction: {
        ...inputOf(value),
        definition: transactionDefinition(),
        ...(includeMeasures && !editing
          ? { measures: transactionMeasureSelections(measureDrafts) }
          : {}),
      },
      population,
      expectedVersion: savedVersion ?? null,
      expectedPopulationVersion: creatingPopulation ? populationVersion : null,
      expectedRevisionVersion: revisionVersion ?? 1,
      useAsBase: useAsBase || !!(level && level.objectId === baseDimensionId),
    }
  }
  const populationReady =
    !!level?.definition.grain.keyIds.length &&
    (!creatingPopulation || !!populationDraft.name.trim())
  const keyNames = populationDraft.definition.grain.columns
    .filter((column) => populationDraft.definition.grain.keyIds.includes(column.grainColumnId))
    .map((column) => column.name.toLowerCase())
    .sort()
    .join('|')
  const similarPopulations =
    creatingPopulation && keyNames
      ? data.dimensions.filter(
          (dimension) =>
            dimension.objectId !== populationDraft.objectId &&
            dimension.definition.grain.columns
              .filter((column) => dimension.definition.grain.keyIds.includes(column.grainColumnId))
              .map((column) => column.name.toLowerCase())
              .sort()
              .join('|') === keyNames,
        )
      : []
  async function saveTransaction() {
    if (!measuresReady || !populationReady) return
    if (onSavePopulation) await onSavePopulation(populationCommand(true))
    else await onSave(populationCommand(true).transaction)
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
              if (creatingPopulation) {
                const next = newTransactionPopulation({ ...value, definition: { ...d, sourceId } })
                setPopulationDraft(next)
                setPopulationVersion(null)
              }
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
          <p className="meta-hint">Choose which valid combinations your measures will use.</p>
          <fieldset className="measure-type-control" disabled={!source || !pivotReady}>
            <legend>Population</legend>
            <div className="measure-type-switch">
              {(
                [
                  ['rows', 'Rows from this transaction'],
                  ['reuse', 'Use existing population'],
                  ['custom', 'Define population'],
                ] as const
              ).map(([mode, label]) => (
                <label className="measure-type-option" key={mode}>
                  <input
                    type="radio"
                    name="transaction-population"
                    checked={populationMode === mode}
                    onChange={() => {
                      if (mode === populationMode) return
                      setPopulationMode(mode)
                      if (mode !== 'reuse') {
                        setPopulationDraft(newTransactionPopulation(value))
                        setPopulationVersion(null)
                      }
                      update({ mappingMode: 'direct', resolution: null, keyMappings: [] })
                    }}
                  />
                  <span>{label}</span>
                </label>
              ))}
            </div>
          </fieldset>
          {creatingPopulation && (
            <DimensionEditor
              key={populationDraft.objectId + populationMode}
              embedded
              fixedSource={createGrain}
              inputColumns={grainColumns}
              initial={populationDraft}
              data={data}
              editing={populationVersion !== null}
              onDraftChange={setPopulationDraft}
              busy={busy}
              base={base}
              onSave={async () => {}}
              onCancel={() => {}}
            />
          )}
          {populationMode === 'reuse' && (
            <Select
              label="Existing population"
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
                        : suggestPopulationMappings(l, grainColumns),
                  })
                } catch (error) {
                  setError(error instanceof Error ? error.message : 'Could not select Dimension.')
                } finally {
                  setOnboardingBusy(false)
                }
              }}
            />
          )}
          {similarPopulations.length > 0 && (
            <div className="population-summary" role="status">
              <strong>Existing populations with these key names</strong>
              <p className="meta-hint">Reuse one if its valid combinations fit this transaction.</p>
              {similarPopulations.map((population) => (
                <button
                  key={population.objectId}
                  type="button"
                  className="secondary-button"
                  onClick={() => {
                    setPopulationMode('reuse')
                    setCreatedLevel(population)
                    update({
                      dimensionId: population.objectId,
                      mappingMode: 'direct',
                      resolution: null,
                      keyMappings: [],
                    })
                  }}
                >
                  Use {population.name}
                </button>
              ))}
            </div>
          )}
          {createGrain && (
            <Select
              label="Time key (optional)"
              required={false}
              value={d.timeColumnId ?? ''}
              items={populationDraft.definition.grain.columns.map((column) => ({
                value: column.populationRef.columnId!,
                label: column.name,
              }))}
              onChange={(timeColumnId) => update({ timeColumnId: timeColumnId || null })}
            />
          )}
          {populationMode === 'reuse' && level && (
            <div className="population-summary">
              <div>
                <strong>{level.name}</strong>
                {level.objectId === baseDimensionId && <span className="meta-badge">Base</span>}
              </div>
              <p className="meta-hint">
                {level.definition.grain.columns
                  .filter((column) => level.definition.grain.keyIds.includes(column.grainColumnId))
                  .map((column) => column.name)
                  .join(' × ')}
              </p>
              {
                <button
                  type="button"
                  className="text-button"
                  onClick={() => {
                    setPopulationDraft(structuredClone(inputOf(level)))
                    setPopulationVersion(
                      data.dimensions.find((dimension) => dimension.objectId === level.objectId)
                        ?.version ?? null,
                    )
                    setPopulationMode('custom')
                  }}
                >
                  Edit shared population
                </button>
              }
            </div>
          )}
          {populationVersion !== null && (
            <p className="meta-hint" role="status">
              Shared with:{' '}
              {data.transactions
                .filter(
                  (transaction) => transaction.definition.dimensionId === populationDraft.objectId,
                )
                .map((transaction) => transaction.name)
                .join(', ') || 'this transaction'}
              . Saving changes affects all connected measures and requires a network rebuild.
            </p>
          )}
          {populationReady && (
            <div className="population-base-choice">
              <Check
                label="Use this population as Base"
                value={useAsBase || level?.objectId === baseDimensionId}
                disabled={level?.objectId === baseDimensionId}
                onChange={setUseAsBase}
              />
              {useAsBase && baseDimensionId && level?.objectId !== baseDimensionId && (
                <p className="meta-hint">
                  Replaces{' '}
                  {data.dimensions.find((dimension) => dimension.objectId === baseDimensionId)
                    ?.name ?? 'the current Base'}
                  . Rebuild the semantic network after saving.
                </p>
              )}
              <p className="meta-hint">
                Base defines the finest shared planning combinations. Measures reach it only through
                supported dimension mappings and visibility policies.
              </p>
            </div>
          )}
          {!createGrain && (
            <Check
              label="Resolve multi-grain data"
              value={d.mappingMode === 'resolve_multi_grain'}
              disabled={!source || !pivotReady || !populationReady}
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
          {createGrain ? null : d.mappingMode === 'resolve_multi_grain' &&
            d.resolution &&
            level &&
            source ? (
            <>
              <TransactionResolutionEditor
                value={d.resolution}
                onChange={(resolution) => update({ resolution })}
                columns={d.calendarResolution ? calendarColumns : inputColumns}
                calendarResolved={!!d.calendarResolution}
                level={level}
                population={creatingPopulation ? populationDraft : undefined}
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
              populationReady &&
              (createGrain
                ? populationDraft.definition.grain.keyIds.length > 0 &&
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
                !populationReady ||
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
                      base + '/transactions/population/preview',
                      'POST',
                      populationCommand(),
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
                !populationReady ||
                !pivotReady ||
                (d.mappingMode === 'resolve_multi_grain' && !resolutionComplete(d.resolution)) ||
                (!!d.calendarResolution && !calendarColumns.length)
              }
              loadSql={(signal) => {
                if (!form.current?.reportValidity())
                  throw new Error('Complete the required fields first.')
                return metadataApi.request(
                  base + '/transactions/population/preview?sql_only=true',
                  'POST',
                  populationCommand(),
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
