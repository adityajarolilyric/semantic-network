import { QuickCalculatedMeasure } from './QuickCalculatedMeasure'
import { PlanningViewPanel } from './PlanningViewPanel'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import type { TableManagement } from '../components/TableCatalog'
import { ApiError } from '../lib/api'
import { metadataApi } from './api'
import { planningLevelBuilds } from './planningLevelBuilds'
import { useMetadata } from './useMetadata'
import { MaterialisePanel } from './MaterialisePanel'
import {
  identity,
  effectiveSnapshot,
  inputOf,
  type Dimension,
  type Kind,
  type Measure,
  type ObjectDocument,
  type ObjectInput,
  type PlanningLevel,
  type Report,
  type Revision,
  type Transaction,
} from './types'
import { Check, Select } from './editors/Fields'
import { ManagedTableEditor } from './editors/ManagedTableEditor'
import { DimensionEditor } from './editors/DimensionEditor'
import { PlanningLevelEditor } from './editors/PlanningLevelEditor'
import { TransactionEditor } from './editors/TransactionEditor'
import { MeasureEditor, defaultAggregation } from './editors/MeasureEditor'
export type Tab =
  | 'sources'
  | 'managed'
  | 'dimensions'
  | 'levels'
  | 'transactions'
  | 'measures'
  | 'materialise'
  | 'planning-view'
export const tabs: { id: Tab; label: string }[] = [
  { id: 'sources', label: 'Sources' },
  { id: 'managed', label: 'Managed Tables' },
  { id: 'dimensions', label: 'Dimensions' },
  { id: 'levels', label: 'Planning Levels' },
  { id: 'transactions', label: 'Transactions' },
  { id: 'measures', label: 'Measures' },
  { id: 'materialise', label: 'Materialise' },
  { id: 'planning-view', label: 'Planning View' },
]
const tabKind: Partial<Record<Tab, Kind>> = {
  managed: 'source',
  dimensions: 'dimension',
  levels: 'planningLevel',
  transactions: 'transaction',
  measures: 'measure',
}
function newObject(kind: Exclude<Kind, 'source'>): ObjectInput {
  const common = { ...identity(kind), displayName: '' }
  if (kind === 'dimension')
    return {
      ...common,
      kind,
      isTimeDim: false,
      definition: {
        sourceId: '',
        columns: [],
        keyColumnIds: [],
        labelColumnId: '',
        attributeColumnIds: [],
        time: null,
      },
    }
  if (kind === 'planningLevel')
    return {
      ...common,
      kind,
      definition: {
        population: { operation: 'source', sourceId: '' },
        grain: { keyIds: [], columns: [] },
        dimensionBindings: [],
      },
    }
  if (kind === 'transaction')
    return {
      ...common,
      kind,
      definition: {
        sourceId: '',
        planningLevelId: '',
        mappingMode: 'direct',
        keyMappings: [],
        expectedRowsPerGrain: 'many',
        unmatchedGrainPolicy: 'reject',
      },
    }
  return {
    ...common,
    kind: 'measure',
    business: { unit: null, aliases: [] },
    definition: {
      measureType: 'referenced',
      transactionId: '',
      valueColumnId: '',
      nativePlanningLevelId: '',
      resultType: { type: 'number', nullable: true },
      aggregation: defaultAggregation,
      missingTransactionValue: 'null',
      sourceNullPolicy: 'preserve',
      allocation: { mode: 'weighted_by_self' },
      editable: false,
    },
  }
}
export function MetadataWorkspace({
  tab,
  onTab,
  children,
}: {
  tab: Tab
  onTab: (tab: Tab) => void
  children: (management: TableManagement) => ReactNode
}) {
  const meta = useMetadata()
  const [busy, setBusy] = useState(false)
  const [quickMeasure, setQuickMeasure] = useState(false)
  const [pendingManaged, setPendingManaged] = useState<Record<string, boolean>>({})
  useEffect(() => {
    if (!meta.loading && !meta.error)
      setPendingManaged((current) =>
        Object.fromEntries(
          Object.entries(current).filter(
            ([table, target]) =>
              meta.data.sources.some(
                (s) => s.definition.tableName === table && s.definition.inScope,
              ) !== target,
          ),
        ),
      )
  }, [meta.loading, meta.data, meta.error])
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [report, setReport] = useState<Report | null>(null)
  const [editor, setEditor] = useState<{ input: ObjectInput; version?: number } | null>(null)
  const editorRef = useRef<HTMLElement>(null)
  useEffect(() => {
    if (editor) editorRef.current?.scrollIntoView({ block: 'start', behavior: 'smooth' })
  }, [editor?.input.objectId])
  const [deleteTarget, setDeleteTarget] = useState<ObjectDocument | null>(null)
  const [showExcluded, setShowExcluded] = useState(false)
  const model = meta.model
  const revision = meta.revision
  const editable = revision?.approvalStatus === 'draft' && !meta.loading && !meta.error
  const locked = busy || meta.loading
  useEffect(() => {
    setEditor(null)
    setQuickMeasure(false)
    setPendingManaged({})
    setDeleteTarget(null)
    setReport(null)
    setError('')
    setNotice('')
  }, [tab, model?.modelId, revision?.revisionId])
  function fail(error: unknown) {
    const message = error instanceof Error ? error.message : 'The operation failed.'
    const readable = Object.values(meta.data)
      .flat()
      .reduce((text, o) => text.replaceAll(o.objectId, o.name), message)
    setError(readable)
    if (error instanceof ApiError && error.requestId)
      setError(readable + '\nRequest ID: ' + error.requestId)
  }
  async function run(task: () => Promise<unknown>, success: string) {
    setBusy(true)
    setError('')
    setNotice('')
    try {
      await task()
      setNotice(success)
      setReport(null)
      meta.refresh()
      return true
    } catch (error) {
      fail(error)
      return false
    } finally {
      setBusy(false)
    }
  }
  async function save(input: ObjectInput) {
    if (!model || !revision || !editor) return
    const ok = await run(
      () => metadataApi.save(model.modelId, revision.revisionId, inputOf(input), editor.version),
      'Changes saved.',
    )
    if (ok) setEditor(null)
  }
  async function createQuickMeasure(measure: Measure) {
    if (!model || !revision || !editable || busy) return
    setBusy(true)
    setError('')
    setNotice('')
    let saved = false
    try {
      await metadataApi.save(model.modelId, revision.revisionId, inputOf(measure))
      saved = true
      setQuickMeasure(false)
      const base = metadataApi.scope(model.modelId, revision.revisionId)
      const current = await metadataApi.request<Revision>(base)
      await metadataApi.managedOperation(
        base + '/measures/execution/build',
        { mode: 'incremental' },
        current.version,
      )
      setNotice('Calculated measure created and materialised. Select it under Values.')
    } catch (error) {
      if (!saved) throw error
      fail(
        new Error(
          'Measure saved, but materialisation failed. Retry Build Semantic Network from Materialise.\n' +
            (error instanceof Error ? error.message : 'Build failed.'),
        ),
      )
    } finally {
      setBusy(false)
      meta.refresh()
    }
  }
  const all = Object.values(meta.data).flat() as ObjectDocument[]
  const kind = tabKind[tab]
  const objects = all.filter(
    (o) => o.kind === kind && (o.kind !== 'source' || showExcluded || o.definition.inScope),
  )
  const management: TableManagement = {
    pending: pendingManaged,
    managed: new Set(
      meta.data.sources.filter((s) => s.definition.inScope).map((s) => s.definition.tableName),
    ),
    disabled: !editable || locked,
    onChange: (tableName, inScope) => {
      setPendingManaged((current) => ({ ...current, [tableName]: inScope }))
      const source = meta.data.sources.find((s) => s.definition.tableName === tableName)
      void run(
        async () => {
          const base = metadataApi.scope(model!.modelId, revision!.revisionId)
          if (inScope)
            await metadataApi.managedOperation(base + '/managed-tables/enable', { tableName })
          else if (source?.managed)
            await metadataApi.request(
              base + '/managed-tables/' + source.objectId + '/disable',
              'POST',
              undefined,
              source.managed.version,
            )
          else if (source)
            await metadataApi.save(
              model!.modelId,
              revision!.revisionId,
              { ...inputOf(source), definition: { ...source.definition, inScope: false } },
              source.version,
            )
        },
        inScope ? 'Table is now managed.' : 'Table removed from managed scope.',
      ).then((ok) => {
        if (!ok)
          setPendingManaged((current) => {
            const next = { ...current }
            delete next[tableName]
            return next
          })
      })
    },
  }
  const common = {
    data: effectiveSnapshot(meta.data),
    editing: !!editor?.version,
    onCancel: () => setEditor(null),
    busy,
  }
  return (
    <>
      {tab === 'sources' &&
        editable &&
        (revision.metadataContractVersion === 1 ||
          meta.data.sources.some(
            (source) => source.definition.inScope && !source.managed?.ready,
          )) && (
          <section className="meta-context" aria-label="Source actions">
            <div className="meta-toolbar">
              <button
                className="secondary-button"
                disabled={locked}
                onClick={() =>
                  void run(async () => {
                    const base = metadataApi.scope(model!.modelId, revision.revisionId)
                    for (const source of meta.data.sources.filter(
                      (s) => s.definition.inScope && !s.managed?.ready,
                    ))
                      await metadataApi.managedOperation(base + '/managed-tables/enable', {
                        tableName: source.definition.tableName,
                      })
                    const latest = await metadataApi.request<Revision>(base)
                    await metadataApi.request(
                      base + '/managed-tables/upgrade-contract',
                      'POST',
                      undefined,
                      latest.version,
                    )
                  }, 'Managed views initialized. This draft now uses the engine contract.')
                }
              >
                Initialize managed views
              </button>
            </div>
          </section>
        )}
      {meta.error && (
        <div className="meta-error" role="alert">
          {meta.error}
          <button className="secondary-button" onClick={meta.refresh}>
            Retry metadata
          </button>
        </div>
      )}
      {revision && (tab === 'sources' || tab === 'levels') && (
        <section className="meta-revision">
          <div>
            <span className={'meta-badge ' + revision.approvalStatus}>
              {revision.approvalStatus}
            </span>
            <span> {all.length} objects</span>
          </div>
          {tab === 'sources' && (
            <div className="meta-toolbar">
              <button
                className="secondary-button"
                disabled={locked}
                onClick={async () => {
                  setBusy(true)
                  setError('')
                  try {
                    setReport(await metadataApi.validate(model!.modelId, revision.revisionId))
                  } catch (e) {
                    fail(e)
                  } finally {
                    setBusy(false)
                  }
                }}
              >
                Validate metadata
              </button>
            </div>
          )}
          {revision.approvalStatus === 'published' && (
            <p className="meta-hint">This saved revision is read-only.</p>
          )}
        </section>
      )}
      {meta.loading && (
        <p className="meta-hint" role="status">
          Loading saved metadata…
        </p>
      )}
      {error && (
        <div className="meta-error" role="alert">
          {error}
          {error.includes('version') && (
            <p>Reload metadata and reopen the object before applying your changes.</p>
          )}
        </div>
      )}
      {notice && (
        <p className="meta-notice" role="status">
          {notice}
        </p>
      )}
      {report && (
        <section className="meta-card" aria-label="Validation results">
          <h2>{report.valid ? 'Metadata is valid' : 'Metadata needs attention'}</h2>
          {report.valid ? (
            <p className="meta-hint">
              Definition checks passed. Warehouse data uniqueness and coverage have not been
              verified.
            </p>
          ) : (
            <ul>
              {report.diagnostics.map((d, i) => (
                <li key={i}>
                  {d.objectId && (
                    <strong>
                      {all.find((o) => o.objectId === d.objectId)?.name ?? 'Definition'}:{' '}
                    </strong>
                  )}
                  {all.reduce((message, o) => message.replaceAll(o.objectId, o.name), d.message)}
                  {d.path && <small> ({d.path})</small>}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
      {quickMeasure && model && revision?.basePlanningLevelId && (
        <QuickCalculatedMeasure
          data={meta.data}
          base={metadataApi.scope(model.modelId, revision.revisionId)}
          baseLevelId={revision.basePlanningLevelId}
          busy={busy}
          onClose={() => setQuickMeasure(false)}
          onSave={createQuickMeasure}
        />
      )}
      {tab === 'sources' ? (
        children(management)
      ) : tab === 'planning-view' ? (
        model && revision ? (
          <PlanningViewPanel
            key={model.modelId + revision.revisionId}
            base={metadataApi.scope(model.modelId, revision.revisionId)}
            revisionVersion={revision.version}
            onCreateMeasure={
              editable && revision.basePlanningLevelId ? () => setQuickMeasure(true) : undefined
            }
            creatingMeasure={busy}
            onCommitted={(version) =>
              meta.updateRevisionVersion(model.modelId, revision.revisionId, version)
            }
          />
        ) : null
      ) : tab === 'materialise' ? (
        <>
          {tab === 'materialise' && model && revision && (
            <MaterialisePanel
              key={model.modelId + revision.revisionId}
              base={metadataApi.scope(model.modelId, revision.revisionId)}
              revisionVersion={revision.version}
              baseLevelId={revision.basePlanningLevelId}
              levels={meta.data.planningLevels}
              measures={meta.data.measures}
              editable={!!editable && !locked}
              onBuilt={meta.refresh}
            />
          )}
        </>
      ) : (
        <>
          {!editor && (
            <>
              <div
                className={
                  tab === 'levels'
                    ? 'meta-list-heading planning-level-list-heading'
                    : 'meta-list-heading'
                }
              >
                {editable && tab === 'levels' && (
                  <div className="planning-level-base">
                    <Select
                      disabled={locked || !!editor}
                      label="Base planning level"
                      value={revision.basePlanningLevelId ?? ''}
                      items={[
                        { value: '__none', label: 'No base selected' },
                        ...meta.data.planningLevels.map((l) => ({
                          value: l.objectId,
                          label: l.displayName,
                        })),
                      ]}
                      required={false}
                      onChange={async (value) => {
                        await run(
                          () =>
                            metadataApi.request(
                              metadataApi.scope(model!.modelId, revision.revisionId),
                              'PUT',
                              {
                                basePlanningLevelId: value && value !== '__none' ? value : null,
                                changeReason: revision.changeReason,
                              },
                              revision.version,
                            ),
                          'Base planning level saved.',
                        )
                      }}
                    />
                  </div>
                )}
                <div className="meta-toolbar">
                  <button
                    className="secondary-button"
                    disabled={locked}
                    onClick={() => {
                      setEditor(null)
                      setReport(null)
                      setError('')
                      meta.refresh()
                    }}
                  >
                    Reload metadata
                  </button>
                  {tab === 'levels' && (
                    <button
                      className="secondary-button"
                      disabled={!editable || locked || meta.data.planningLevels.length === 0}
                      title="Rebuild saved planning populations, parents first. Rebuild measure values separately in Materialise."
                      onClick={() =>
                        void run(
                          () =>
                            planningLevelBuilds.buildAll(
                              model!.modelId,
                              revision!.revisionId,
                              ({ levelName, completed, total }) =>
                                setNotice(`Building ${completed + 1} of ${total}: ${levelName}…`),
                            ),
                          'All planning levels built. Rebuild measure values in Materialise.',
                        ).then((ok) => {
                          // Earlier levels may have committed before a later build failed.
                          if (!ok) {
                            setNotice('')
                            meta.refresh()
                          }
                        })
                      }
                    >
                      Build all planning levels
                    </button>
                  )}
                  {tab === 'managed' ? (
                    <button className="secondary-button" onClick={() => onTab('sources')}>
                      Choose managed tables
                    </button>
                  ) : (
                    kind && (
                      <button
                        className="primary-button"
                        disabled={!editable || locked || !!editor}
                        onClick={() => {
                          setError('')
                          setNotice('')
                          setEditor({ input: newObject(kind as Exclude<Kind, 'source'>) })
                        }}
                      >
                        Create {tab === 'levels' ? 'planning level' : kind}
                      </button>
                    )
                  )}
                </div>
              </div>
              {tab === 'managed' && (
                <Check
                  label="Show sources outside managed scope"
                  value={showExcluded}
                  onChange={setShowExcluded}
                />
              )}
              {!meta.loading && objects.length === 0 && !editor && (
                <div className="meta-card">
                  <h3>No {tabs.find((t) => t.id === tab)?.label.toLowerCase()} yet</h3>
                  <p className="meta-hint">
                    {tab === 'managed'
                      ? 'Mark a source table as Managed to use it here.'
                      : 'Create a definition after its required source tables and dependencies are saved.'}
                  </p>
                </div>
              )}
              <div className="meta-object-list">
                {objects.map((o) => (
                  <article className="meta-object-card" key={o.objectId}>
                    <div>
                      <h3>
                        {o.kind === 'dimension'
                          ? o.name
                          : 'displayName' in o
                            ? o.displayName
                            : o.name}
                      </h3>

                      <p>{o.description || objectSummary(o)}</p>
                      {o.kind === 'planningLevel' &&
                        revision?.basePlanningLevelId === o.objectId && (
                          <span className="meta-badge">Base planning level</span>
                        )}
                      {o.kind === 'source' && !o.definition.inScope && (
                        <span className="meta-badge">Outside scope</span>
                      )}
                    </div>
                    <div className="meta-toolbar">
                      <button
                        className="secondary-button"
                        disabled={locked || !!editor}
                        onClick={() => {
                          setError('')
                          // Source editors need the saved managed definition as well as raw metadata.
                          // inputOf is for API serialization and deliberately removes that data.
                          setEditor({
                            input: o.kind === 'source' ? o : inputOf(o),
                            version: o.version,
                          })
                        }}
                      >
                        {editable ? 'Edit' : 'View'}
                      </button>
                      {editable && (
                        <button
                          className="meta-danger"
                          disabled={locked || !!editor}
                          onClick={() => {
                            setError('')
                            setDeleteTarget(o)
                          }}
                        >
                          Delete
                        </button>
                      )}
                    </div>
                  </article>
                ))}
              </div>
            </>
          )}
          {deleteTarget && (
            <section className="meta-card">
              <h3>Delete {deleteTarget.name}?</h3>
              <p className="meta-hint">
                Only this semantic definition is removed. The physical table remains. Referenced
                definitions cannot be deleted.
              </p>
              <div className="meta-toolbar">
                <button
                  className="secondary-button"
                  disabled={busy}
                  onClick={() => setDeleteTarget(null)}
                >
                  Cancel deletion
                </button>
                <button
                  className="meta-danger"
                  disabled={locked}
                  onClick={async () => {
                    const ok = await run(
                      () =>
                        metadataApi.remove(
                          model!.modelId,
                          revision!.revisionId,
                          deleteTarget.kind,
                          deleteTarget.objectId,
                          deleteTarget.version,
                        ),
                      'Definition deleted.',
                    )
                    if (ok) setDeleteTarget(null)
                  }}
                >
                  Confirm deletion
                </button>
              </div>
            </section>
          )}
          {editor && (
            <section className="meta-card meta-editor" ref={editorRef} aria-label="Metadata editor">
              <h2>
                {editable ? (editor.version ? 'Edit' : 'Create') : 'View'}{' '}
                {editor.input.kind === 'source'
                  ? 'managed table'
                  : editor.input.kind === 'planningLevel'
                    ? 'planning level'
                    : editor.input.kind}
              </h2>
              <fieldset
                disabled={
                  busy ||
                  meta.loading ||
                  (!editable &&
                    editor.input.kind !== 'source' &&
                    editor.input.kind !== 'planningLevel')
                }
              >
                {editor.input.kind === 'source' && (
                  <ManagedTableEditor
                    key={editor.input.objectId}
                    source={editor.input}
                    base={metadataApi.scope(model!.modelId, revision!.revisionId)}
                    busy={busy}
                    readOnly={!editable}
                    onSaved={meta.refresh}
                    onClose={() => setEditor(null)}
                  />
                )}
                {editor.input.kind === 'dimension' && (
                  <DimensionEditor
                    key={editor.input.objectId}
                    base={metadataApi.scope(model!.modelId, revision!.revisionId)}
                    {...common}
                    initial={editor.input as Dimension}
                    onSave={save}
                  />
                )}
                {editor.input.kind === 'planningLevel' && (
                  <PlanningLevelEditor
                    key={editor.input.objectId}
                    {...common}
                    initial={editor.input as PlanningLevel}
                    savedVersion={editor.version}
                    readOnly={!editable}
                    onPersisted={(input, version, close) => {
                      setEditor(close ? null : { input, version })
                      meta.refresh()
                    }}
                    base={metadataApi.scope(model!.modelId, revision!.revisionId)}
                    onSave={save}
                  />
                )}
                {editor.input.kind === 'transaction' && (
                  <TransactionEditor
                    key={editor.input.objectId}
                    base={metadataApi.scope(model!.modelId, revision!.revisionId)}
                    {...common}
                    initial={editor.input as Transaction}
                    onCreated={(transaction, close) => {
                      setEditor((current) =>
                        current?.input.objectId === transaction.objectId
                          ? close
                            ? null
                            : { input: inputOf(transaction), version: transaction.version }
                          : current,
                      )
                      meta.refresh()
                      if (close) setNotice('Transaction created.')
                    }}
                    onSave={save}
                  />
                )}
                {editor.input.kind === 'measure' && (
                  <MeasureEditor
                    key={editor.input.objectId}
                    base={metadataApi.scope(model!.modelId, revision!.revisionId)}
                    baseLevelId={revision!.basePlanningLevelId}
                    {...common}
                    initial={editor.input as Measure}
                    onSave={save}
                  />
                )}
              </fieldset>
              {!editable && (
                <button className="secondary-button" onClick={() => setEditor(null)}>
                  Close view
                </button>
              )}
            </section>
          )}
        </>
      )}
    </>
  )
}
function objectSummary(o: ObjectInput) {
  if (o.kind === 'source')
    return `${o.definition.tableName} · ${o.definition.columns.length} columns · ${o.definition.keyColumns.length} key columns`
  if (o.kind === 'dimension')
    return `${o.isTimeDim ? 'Time dimension' : 'Dimension'} · ${o.definition.columns.length} columns`
  if (o.kind === 'planningLevel')
    return `${o.definition.population.operation} · ${o.definition.grain.keyIds.length} grain keys`
  if (o.kind === 'transaction') {
    if (o.definition.mappingMode === 'resolve_multi_grain' && o.definition.resolution) {
      const keys = o.definition.resolution.axes.length
      const values = o.definition.resolution.values.length
      return `Multi-grain resolution · ${keys} native key${keys === 1 ? '' : 's'} · ${values} value column${values === 1 ? '' : 's'}`
    }
    return `${o.definition.keyMappings.length} mapped keys`
  }
  return o.definition.measureType === 'calculated' ? 'Calculated measure' : 'Referenced measure'
}
