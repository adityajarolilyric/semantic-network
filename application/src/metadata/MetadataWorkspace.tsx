import { materialisationCatalog } from './grainCatalog'
import { dimensionCatalog, dimensionGrain } from './dimensionCatalog'
import { QuickCalculatedMeasure } from './QuickCalculatedMeasure'
import { DecisionViewNetwork } from './DecisionViewNetwork'
import { lazy, Suspense, useEffect, useRef, useState, type ReactNode } from 'react'
import type { TableManagement } from '../components/TableCatalog'
import { ApiError } from '../lib/api'
import { metadataApi } from './api'
import { useMetadata } from './useMetadata'
import {
  identity,
  effectiveSnapshot,
  inputOf,
  type DimensionMembers,
  type Kind,
  type Measure,
  type ObjectDocument,
  type ObjectInput,
  type Dimension,
  type Report,
  type Revision,
  type Transaction,
} from './types'
import { Check, Select } from './editors/Fields'
import { ManagedTableEditor } from './editors/ManagedTableEditor'
import { DimensionMembersEditor } from './editors/DimensionMembersEditor'
import { DimensionEditor } from './editors/DimensionEditor'
import { TransactionEditor } from './editors/TransactionEditor'
import { MeasureEditor, defaultAggregation } from './editors/MeasureEditor'
const ContextGraphPanel = lazy(() => import('./contextGraph/ContextGraphPanel'))

export type Tab =
  | 'sources'
  | 'managed'
  | 'dimensions'
  | 'levels'
  | 'transactions'
  | 'measures'
  | 'decision-view'
  | 'context-graph'
export const tabs: { id: Tab; label: string }[] = [
  { id: 'sources', label: 'Sources' },
  { id: 'managed', label: 'Managed Tables' },
  { id: 'dimensions', label: 'Dimensions' },
  { id: 'transactions', label: 'Transactions' },
  { id: 'measures', label: 'Measures' },
  { id: 'decision-view', label: 'Decision View' },
  { id: 'context-graph', label: 'Context Graph' },
]
const tabKind: Partial<Record<Tab, Kind>> = {
  managed: 'source',
  dimensions: 'dimensionMembers',
  levels: 'dimension',
  transactions: 'transaction',
  measures: 'measure',
}
function newObject(kind: Exclude<Kind, 'source'>): ObjectInput {
  const common = { ...identity(kind), displayName: '' }
  if (kind === 'dimensionMembers')
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
  if (kind === 'dimension')
    return {
      ...common,
      kind,
      definition: {
        population: { operation: 'source', sourceId: '' },
        grain: { keyIds: [], columns: [] },
        memberBindings: [],
      },
    }
  if (kind === 'transaction')
    return {
      ...common,
      kind,
      definition: {
        sourceId: '',
        dimensionId: null,
        grainColumnIds: [],
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
      nativeGrainId: '',
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
  const materialisationGrains = materialisationCatalog(meta.data, revision?.baseDimensionId ?? null)
  const defaultGrainId =
    revision?.baseDimensionId ??
    meta.data.transactions[0]?.definition.dimensionId ??
    meta.data.transactions[0]?.effectiveGrainId ??
    materialisationGrains[0]?.objectId ??
    null
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
  async function persistEditor(input: ObjectInput, close: boolean) {
    if (!model || !revision || !editor) return
    let saved: ObjectDocument | undefined
    const ok = await run(async () => {
      if (input.kind === 'dimension' && input.definition.population.operation !== 'source') {
        const base = metadataApi.scope(model.modelId, revision.revisionId)
        await metadataApi.managedOperation(base + '/dimensions/save-and-build', {
          definition: inputOf(input),
          expectedVersion: editor.version ?? null,
        })
        saved = await metadataApi.request<ObjectDocument>(base + '/dimensions/' + input.objectId)
      } else
        saved = await metadataApi.save(
          model.modelId,
          revision.revisionId,
          inputOf(input),
          editor.version,
        )
    }, 'Changes saved.')
    if (!ok || !saved) throw new Error('Save failed. Correct the error before Preview.')
    if (close) setEditor(null)
    else {
      // Source-member creation returns its owning Dimension document. Keep the
      // current authoring facade while advancing its authoritative version.
      setEditor({ input: inputOf(input), version: saved.version })
    }
  }
  async function save(input: ObjectInput) {
    try {
      await persistEditor(input, true)
    } catch {
      /* run() displays the save error */
    }
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
          'Measure saved, but materialisation failed. Retry Build Semantic Network in Decision View.\n' +
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
  const objects =
    tab === 'dimensions' || tab === 'levels'
      ? dimensionCatalog(meta.data)
      : all.filter(
          (o) => o.kind === kind && (o.kind !== 'source' || showExcluded || o.definition.inScope),
        )
  const management: TableManagement = {
    onImported: meta.refresh,
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
    onSaveForPreview: (input: ObjectInput) => persistEditor(input, false),
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
      {revision && (tab === 'sources' || tab === 'dimensions' || tab === 'levels') && (
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
      {quickMeasure && model && revision && defaultGrainId && (
        <QuickCalculatedMeasure
          data={meta.data}
          base={metadataApi.scope(model.modelId, revision.revisionId)}
          baseLevelId={defaultGrainId}
          busy={busy}
          onClose={() => setQuickMeasure(false)}
          onSave={createQuickMeasure}
        />
      )}
      {tab === 'sources' ? (
        children(management)
      ) : tab === 'decision-view' ? (
        model && revision ? (
          <DecisionViewNetwork
            key={model.modelId + revision.revisionId}
            base={metadataApi.scope(model.modelId, revision.revisionId)}
            revisionVersion={revision.version}
            baseLevelId={revision.baseDimensionId}
            levels={materialisationGrains}
            measures={meta.data.measures}
            editable={!!editable && !locked}
            onBuilt={meta.refresh}
            onCreateMeasure={editable && defaultGrainId ? () => setQuickMeasure(true) : undefined}
            creatingMeasure={busy}
            onCommitted={(version) =>
              meta.updateRevisionVersion(model.modelId, revision.revisionId, version)
            }
          />
        ) : null
      ) : tab === 'context-graph' ? (
        model && revision ? (
          <Suspense
            fallback={
              <p className="meta-hint" role="status">
                Loading context graph…
              </p>
            }
          >
            <ContextGraphPanel
              key={model.modelId + ':' + revision.revisionId}
              data={meta.data}
              baseDimensionId={revision.baseDimensionId}
              base={metadataApi.scope(model.modelId, revision.revisionId)}
              revisionVersion={revision.version}
            />
          </Suspense>
        ) : null
      ) : (
        <>
          {!editor && (
            <>
              <div
                className={
                  tab === 'dimensions' || tab === 'levels'
                    ? 'meta-list-heading dimension-list-heading'
                    : 'meta-list-heading'
                }
              >
                {editable && (tab === 'dimensions' || tab === 'levels') && (
                  <div className="dimension-base">
                    <Select
                      disabled={locked || !!editor}
                      label="Base Dimension"
                      value={revision.baseDimensionId ?? ''}
                      items={[
                        { value: '__none', label: 'No base selected' },
                        ...dimensionCatalog(meta.data).map((l) => ({
                          value: dimensionGrain(meta.data, l.objectId)?.objectId ?? l.objectId,
                          label: l.name,
                        })),
                      ]}
                      required={false}
                      onChange={async (value) => {
                        await run(async () => {
                          let selected = value && value !== '__none' ? value : null
                          if (
                            selected &&
                            meta.data.dimensionMembers.some((d) => d.objectId === selected)
                          ) {
                            const grain = await metadataApi.request<Dimension>(
                              metadataApi.scope(model!.modelId, revision.revisionId) +
                                '/dimension-members/' +
                                selected +
                                '/grain',
                              'POST',
                            )
                            selected = grain.objectId
                          }
                          const current = await metadataApi.request<Revision>(
                            metadataApi.scope(model!.modelId, revision.revisionId),
                          )
                          return metadataApi.request(
                            metadataApi.scope(model!.modelId, revision.revisionId),
                            'PUT',
                            {
                              baseDimensionId: selected,
                              changeReason: revision.changeReason,
                            },
                            current.version,
                          )
                        }, 'Base Dimension saved.')
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
                        Create {tab === 'dimensions' || tab === 'levels' ? 'dimension' : kind}
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
                        {o.kind === 'dimensionMembers'
                          ? o.name
                          : 'displayName' in o
                            ? o.displayName
                            : o.name}
                      </h3>

                      <p>{o.description || objectSummary(o)}</p>
                      {(o.kind === 'dimension' || o.kind === 'dimensionMembers') &&
                        revision?.baseDimensionId ===
                          dimensionGrain(meta.data, o.objectId)?.objectId && (
                          <span className="meta-badge">Base Dimension</span>
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
                          const owner =
                            o.kind === 'dimensionMembers'
                              ? dimensionGrain(meta.data, o.objectId)
                              : undefined
                          const customPopulation =
                            owner &&
                            (owner.definition.population.operation !== 'source' ||
                              owner.definition.population.sourceId !==
                                owner.memberCatalog?.sourceId)
                          setEditor({
                            input: customPopulation
                              ? structuredClone(inputOf(owner))
                              : o.kind === 'source'
                                ? o
                                : inputOf(o),
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
                  : editor.input.kind === 'dimension'
                    ? 'dimension'
                    : editor.input.kind === 'dimensionMembers'
                      ? 'dimension'
                      : editor.input.kind}
              </h2>
              <fieldset
                disabled={
                  busy ||
                  meta.loading ||
                  (!editable && editor.input.kind !== 'source' && editor.input.kind !== 'dimension')
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
                {editor.input.kind === 'dimensionMembers' && (
                  <DimensionMembersEditor
                    key={editor.input.objectId}
                    base={metadataApi.scope(model!.modelId, revision!.revisionId)}
                    {...common}
                    initial={editor.input as DimensionMembers}
                    populationControl={(current) => (
                      <fieldset className="measure-type-control">
                        <legend>Population method</legend>
                        <div className="measure-type-switch">
                          {(['source', 'cartesian_product', 'distinct_projection'] as const).map(
                            (operation) => (
                              <label className="measure-type-option" key={operation}>
                                <input
                                  type="radio"
                                  name="dimension-population"
                                  checked={operation === 'source'}
                                  onChange={() => {
                                    if (operation === 'source') return
                                    const owner = dimensionGrain(meta.data, current.objectId)
                                    const input = owner
                                      ? structuredClone(inputOf(owner))
                                      : (newObject('dimension') as Dimension)
                                    input.objectId = owner?.objectId ?? current.objectId
                                    input.name = current.name
                                    input.displayName = current.name
                                    input.description = current.description
                                    input.definition.population =
                                      operation === 'cartesian_product'
                                        ? { operation, factors: [] }
                                        : { operation, sourceDimensionId: '' }
                                    if (owner) {
                                      input.memberCatalog = current.definition
                                      input.isTimeDim = current.isTimeDim
                                    }
                                    input.definition.grain = { keyIds: [], columns: [] }
                                    input.definition.populationAttributes = []
                                    input.definition.memberBindings = []
                                    input.definition.filters = null
                                    setEditor({ input, version: editor.version })
                                  }}
                                />
                                <span>
                                  {operation === 'source'
                                    ? 'From a Table'
                                    : operation === 'cartesian_product'
                                      ? 'From Multiple Tables'
                                      : 'From Existing Dimension'}
                                </span>
                              </label>
                            ),
                          )}
                        </div>
                      </fieldset>
                    )}
                    onSave={save}
                  />
                )}
                {editor.input.kind === 'dimension' && (
                  <DimensionEditor
                    key={editor.input.objectId}
                    {...common}
                    initial={editor.input as Dimension}
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
                    baseLevelId={revision!.baseDimensionId}
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
  if (o.kind === 'dimensionMembers')
    return `${o.isTimeDim ? 'Time dimension' : 'Dimension'} · ${o.definition.columns.length} columns`
  if (o.kind === 'dimension')
    return `${o.definition.population.operation} · ${o.definition.grain.keyIds.length} grain keys`
  if (o.kind === 'transaction') {
    if (o.definition.mappingMode === 'resolve_multi_grain' && o.definition.resolution) {
      const keys = o.definition.resolution.axes.length
      const values = o.definition.resolution.values.length
      return `Multi-grain resolution · ${keys} native key${keys === 1 ? '' : 's'} · ${values} value column${values === 1 ? '' : 's'}`
    }
    return o.definition.dimensionId
      ? `${o.definition.keyMappings.length} mapped keys`
      : `${o.definition.grainColumnIds?.length ?? 0} grain keys · standalone`
  }
  return o.definition.measureType === 'calculated' ? 'Calculated measure' : 'Referenced measure'
}
