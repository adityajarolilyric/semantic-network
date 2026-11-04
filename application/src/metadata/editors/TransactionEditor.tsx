import { FilterEditor } from './FilterEditor'
import { useFilterColumns } from './useFilterColumns'
import { TransformationSection } from './TransformationSection'
import { ObjectPivotEditor } from './ObjectPivotEditor'
import { RawDataPreview } from './RawDataPreview'
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
import { inputOf, type PlanningLevel, type SourceColumn, type Transaction } from '../types'
import {
  Actions,
  IdentityFields,
  options,
  Select,
  Check,
  Checks,
  Text,
  type EditorProps,
} from './Fields'
type GrainCreated = {
  transaction: Transaction & { version: number }
  planningLevel: PlanningLevel & { version: number }
}
export function TransactionEditor({
  initial,
  data,
  editing,
  onSave,
  onCancel,
  busy,
  base,
  onCreated,
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
  const [createGrain, setCreateGrain] = useState(!editing)
  const [grainKeys, setGrainKeys] = useState<string[]>([])
  const [grainName, setGrainName] = useState('')
  const [onboardingBusy, setOnboardingBusy] = useState(false)
  const [onboardingError, setOnboardingError] = useState('')
  const [createdLevel, setCreatedLevel] = useState<PlanningLevel | null>(null)
  const onboardingRequest = useRef(false)
  const mounted = useRef(true)
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])
  const [calendarColumns, setCalendarColumns] = useState<SourceColumn[]>([])
  const [pivotColumns, setPivotColumns] = useState<SourceColumn[] | null>(null)
  const form = useRef<HTMLFormElement>(null)
  const [preview, setPreview] = useState<
    (PlanningTableStatus & { diagnostics?: Record<string, number> }) | null
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
  const level = data.planningLevels.find((l) => l.objectId === d.planningLevelId) ?? createdLevel
  const source = data.sources.find((s) => s.objectId === d.sourceId)
  const pivotReady = !d.pivot?.enabled || pivotColumns !== null
  const inputColumns = d.pivot?.enabled ? (pivotColumns ?? []) : (source?.definition.columns ?? [])
  const grainColumns = d.calendarResolution ? calendarColumns : inputColumns
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
  const grainPayload = () => ({
    objectId: value.objectId,
    kind: 'transaction',
    name: value.name,
    displayName: value.displayName,
    description: value.description,
    sourceId: d.sourceId,
    pivot: d.pivot ?? null,
    calendarResolution: d.calendarResolution ?? null,
    grainColumnIds: grainKeys,
    planningLevelName:
      grainName || ((value.name || source?.name || 'Transaction') + ' grain').slice(0, 200),
  })
  async function saveTransaction() {
    setOnboardingError('')
    if (!createGrain) {
      await onSave(value)
      return
    }
    if (!base || onboardingRequest.current) return
    onboardingRequest.current = true
    setOnboardingBusy(true)
    let created: GrainCreated | null = null
    try {
      created = await metadataApi.request<GrainCreated>(
        base + '/transactions/from-grain',
        'POST',
        grainPayload(),
      )
      setCreatedLevel(created.planningLevel)
      await metadataApi.managedOperation(
        base + '/planning-levels/' + created.planningLevel.objectId + '/build',
        undefined,
        created.planningLevel.version,
      )
      if (mounted.current) {
        onCreated?.(created.transaction, true)
        if (!onCreated) onCancel()
      }
    } catch (error) {
      if (!mounted.current) return
      if (created) {
        set(inputOf(created.transaction))
        setCreateGrain(false)
        onCreated?.(created.transaction, false)
        setOnboardingError(
          'Metadata was saved, but the planning-level build failed. Correct or rebuild it in Planning Levels. ' +
            (error instanceof Error ? error.message : ''),
        )
      } else setError(error instanceof Error ? error.message : 'Could not create planning level.')
    } finally {
      onboardingRequest.current = false
      setOnboardingBusy(false)
    }
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
          {!createGrain && (
            <Select
              label="Native planning level"
              value={d.planningLevelId}
              items={options(data.planningLevels)}
              onChange={(planningLevelId) => {
                const l = data.planningLevels.find((l) => l.objectId === planningLevelId)
                update({
                  planningLevelId,
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
              }}
            />
          )}
        </div>

        <>
          <TransformationSection title="Pivot transformation" configured={!!d.pivot?.enabled}>
            <ObjectPivotEditor
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
          </TransformationSection>

          <TransformationSection title="Date resolution" configured={!!d.calendarResolution}>
            {!d.resolution?.axes.some((a) => a.dateRange) && (
              <>
                <Check
                  label="Resolve a date range using a calendar"
                  value={!!d.calendarResolution}
                  disabled={!pivotReady}
                  onChange={(enabled) => {
                    setCalendarColumns([])
                    update({
                      calendarResolution: enabled
                        ? {
                            dimensionId: '',
                            startColumnId: '',
                            endColumnId: '',
                            selectedColumnIds: [],
                          }
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
                {d.calendarResolution && source && pivotReady && (
                  <TransactionCalendarEditor
                    value={d.calendarResolution}
                    onChange={(calendarResolution) => update({ calendarResolution })}
                    columns={inputColumns}
                    pivot={d.pivot}
                    onColumns={setCalendarColumns}
                    base={base}
                    sourceId={source.objectId}
                    transactionId={value.objectId}
                    levelId={d.planningLevelId}
                  />
                )}
              </>
            )}
          </TransformationSection>
        </>
        <TransformationSection
          title="Planning-level mapping"
          configured={d.mappingMode === 'resolve_multi_grain'}
          status={
            createGrain
              ? 'New planning level'
              : d.mappingMode === 'resolve_multi_grain'
                ? 'Multi-grain'
                : 'Direct'
          }
        >
          <fieldset className="measure-type-control" disabled={!source || !pivotReady}>
            <legend>
              Transaction mapping <FieldHelp label="Transaction mapping" />
            </legend>
            <div className="measure-type-switch">
              {(
                [
                  ['create_grain', 'No Planning Level'],
                  ['direct', 'Map to Native Planning Level'],
                  ['resolve_multi_grain', 'Resolve Multi Grain'],
                ] as const
              ).map(([mode, label]) => (
                <label className="measure-type-option" key={mode}>
                  <input
                    type="radio"
                    name="transaction-mapping"
                    value={mode}
                    checked={createGrain ? mode === 'create_grain' : d.mappingMode === mode}
                    disabled={mode === 'create_grain' && (editing || !!d.filters)}
                    onChange={() => {
                      setCreateGrain(mode === 'create_grain')
                      update({
                        mappingMode: mode === 'create_grain' ? 'direct' : mode,
                        resolution:
                          mode === 'resolve_multi_grain' && level ? newResolution(level) : null,
                        keyMappings:
                          mode === 'resolve_multi_grain'
                            ? []
                            : (level?.definition.grain.keyIds ?? []).map((grainColumnId) => ({
                                grainColumnId,
                                sourceColumnId: '',
                              })),
                      })
                    }}
                  />
                  <span>{label}</span>
                </label>
              ))}
            </div>
          </fieldset>
          {createGrain ? (
            <>
              <p className="meta-hint">
                Choose the columns that define one planning intersection. Save metadata creates and
                builds a planning level with distinct observed combinations, then links this
                Transaction automatically. Transaction records are preserved for measure
                aggregation.
              </p>
              <Text
                label="Planning level name"
                value={
                  grainName ||
                  ((value.name || source?.name || 'Transaction') + ' grain').slice(0, 200)
                }
                onChange={setGrainName}
              />
              <Checks
                label="Transaction grain columns"
                selected={grainKeys}
                items={(d.calendarResolution ? calendarColumns : inputColumns).map((c) => ({
                  value: c.columnId,
                  label: c.name,
                }))}
                onChange={setGrainKeys}
              />
              <p className="meta-hint">
                The population uses this Pivot and Date resolution output before selecting distinct
                grain keys. You can edit it or add Dimension bindings in Planning Levels later.
                Future Transactions do not automatically expand its population.
              </p>
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
                      (level?.name ?? 'Native Planning Level') +
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
                Transaction data will be aggregated by these native planning-level keys before it is
                left joined to the planning level.
              </p>
            </>
          )}
        </TransformationSection>
        {!createGrain && (
          <FilterEditor
            value={d.filters}
            onChange={(filters) => update({ filters })}
            {...filterColumns}
          />
        )}
        {onboardingError && (
          <p className="meta-error" role="alert">
            {onboardingError}
          </p>
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
              name={
                value.name + (createGrain ? ' — proposed planning population' : ' — transformed')
              }
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
              createGrain
                ? grainKeys.length > 0 &&
                  pivotReady &&
                  (!d.calendarResolution || calendarColumns.length > 0)
                : pivotReady &&
                  (!d.calendarResolution || calendarColumns.length > 0) &&
                  (d.mappingMode === 'direct' || resolutionComplete(d.resolution))
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
          </Actions>
        )}
      />
    </form>
  )
}
