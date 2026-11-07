import { ViewSqlButton } from '../components/ViewSqlButton'
import { FieldHelp } from '../components/FieldHelp'
import { useEffect, useState } from 'react'
import { metadataApi } from './api'
import { PlanningResult, type DimensionTableStatus } from './DimensionTablePanel'
import { DecisionViewPanel } from './DecisionViewPanel'
import { Select } from './editors/Fields'
import type { Measure, GrainOption } from './types'

type Values = DimensionTableStatus & {
  incompleteMeasures?: Record<string, number>
  generationId?: string
  unavailable: Record<string, { message: string }>
  materialisation: {
    measureId: string
    name: string
    nativeLevelName: string
    operation: string
    explanation: string
    aggregation: { nonTime: string; time: string; order: string } | null
    disaggregation: { mode: string; basisMeasureName: string | null }
  }[]
  lastRun?: { state: string; error?: { message: string } | null }
}
export function DecisionViewNetwork({
  base,
  revisionVersion,
  baseLevelId,
  levels,
  measures,
  editable,
  onBuilt,
  onCommitted,
  onCreateMeasure,
  creatingMeasure = false,
}: {
  base: string
  revisionVersion: number
  baseLevelId: string | null
  levels: Pick<GrainOption, 'objectId' | 'name'>[]
  measures: Measure[]
  editable: boolean
  onBuilt: () => void
  onCommitted?: (version: number) => void
  onCreateMeasure?: () => void
  creatingMeasure?: boolean
}) {
  const [selected, setSelected] = useState('')
  const [catalog, setCatalog] = useState<Record<string, Values>>({})
  const [catalogLoading, setCatalogLoading] = useState(false)
  const [catalogToken, refreshCatalog] = useState(0)
  const [previewToken, refreshPreview] = useState(0)
  const [status, setStatus] = useState<Values | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [mode, setMode] = useState<'incremental' | 'full' | null>(null)
  const [decisionBusy, setDecisionBusy] = useState(false)
  const [error, setError] = useState('')
  const [catalogError, setCatalogError] = useState('')
  const [previewError, setPreviewError] = useState('')
  const [notice, setNotice] = useState('')
  const [buildRequiredMessage, setBuildRequiredMessage] = useState('')
  const levelKey = JSON.stringify(levels.map((level) => level.objectId))
  const networkBuilding = Object.values(catalog).some((table) => table.state === 'building')
  const busy = mode !== null || networkBuilding

  useEffect(() => {
    const ids: string[] = JSON.parse(levelKey)
    const controller = new AbortController()
    setCatalogLoading(true)
    const result: Record<string, Values> = {}
    const pending = [...ids]
    const failures: string[] = []
    async function worker() {
      while (pending.length && !controller.signal.aborted) {
        const id = pending.shift()!
        try {
          result[id] = await metadataApi.request<Values>(
            base + '/measures/execution/levels/' + encodeURIComponent(id) + '?preview=false',
            'GET',
            undefined,
            undefined,
            controller.signal,
          )
        } catch (error) {
          if (!controller.signal.aborted)
            failures.push(
              error instanceof Error ? error.message : 'Cannot load materialised tables.',
            )
        }
      }
    }
    void Promise.all(Array.from({ length: Math.min(4, ids.length) }, worker)).then(() => {
      if (controller.signal.aborted) return
      setCatalog(result)
      setCatalogLoading(false)
      setCatalogError(failures[0] ?? '')
    })
    return () => controller.abort()
  }, [base, levelKey, revisionVersion, catalogToken])

  useEffect(() => {
    if (!networkBuilding || mode) return
    const controller = new AbortController()
    const id = (
      baseLevelId && catalog[baseLevelId]?.state === 'building'
        ? baseLevelId
        : Object.keys(catalog).find((id) => catalog[id].state === 'building')
    )!
    const timer = setInterval(() => {
      void metadataApi
        .request<Values>(
          base + '/measures/execution/levels/' + encodeURIComponent(id) + '?preview=false',
          'GET',
          undefined,
          undefined,
          controller.signal,
        )
        .then((table) => {
          if (!controller.signal.aborted && table.state !== 'building') {
            refreshCatalog((value) => value + 1)
            refreshPreview((value) => value + 1)
            onBuilt()
          }
        })
        .catch(() => {
          /* Preserve the current table catalog until connectivity recovers. */
        })
    }, 1000)
    return () => {
      clearInterval(timer)
      controller.abort()
    }
  }, [base, baseLevelId, catalog, networkBuilding, mode, onBuilt])

  useEffect(() => {
    if (!catalogLoading && selected && catalog[selected]?.rowCount == null) setSelected('')
  }, [catalog, catalogLoading, selected])

  useEffect(() => {
    setStatus(null)
    setPreviewError('')
    if (!selected) {
      setPreviewLoading(false)
      return
    }
    const controller = new AbortController()
    setPreviewLoading(true)
    void metadataApi
      .request<Values>(
        base + '/measures/execution/levels/' + encodeURIComponent(selected) + '?preview=true',
        'GET',
        undefined,
        undefined,
        controller.signal,
      )
      .then((table) => {
        if (!controller.signal.aborted) setStatus(table)
      })
      .catch((error) => {
        if (!controller.signal.aborted) setPreviewError(error.message)
      })
      .finally(() => {
        if (!controller.signal.aborted) setPreviewLoading(false)
      })
    return () => controller.abort()
  }, [base, selected, revisionVersion, previewToken])

  async function build(nextMode: 'incremental' | 'full') {
    if (
      nextMode === 'full' &&
      !window.confirm(
        'Full Semantic Refresh reloads all measures from sources and replaces manual edits. Continue?',
      )
    )
      return
    setMode(nextMode)
    setError('')
    setNotice('')
    try {
      const run = await metadataApi.managedOperation(
        base + '/measures/execution/build',
        { mode: nextMode },
        revisionVersion,
      )
      setNotice(
        run?.reused
          ? 'Already up to date. Existing values were reused.'
          : nextMode === 'full'
            ? 'Full Semantic Refresh completed.'
            : 'Semantic Network updated.',
      )
      onBuilt()
    } catch (error) {
      setError(error instanceof Error ? error.message : 'Semantic Network build failed.')
    } finally {
      setMode(null)
      refreshCatalog((value) => value + 1)
      refreshPreview((value) => value + 1)
    }
  }
  const buildDisabled =
    busy || decisionBusy || creatingMeasure || !levels.length || !measures.length
  const networkActions = (
    <div className="decision-network-actions">
      <div className="meta-toolbar">
        {editable && (
          <>
            <button
              type="button"
              className="primary-button"
              disabled={buildDisabled}
              title={buildRequiredMessage || undefined}
              onClick={() => void build('incremental')}
            >
              {mode === 'incremental' ? 'Building Semantic Network…' : 'Build Semantic Network'}
              {buildRequiredMessage && (
                <span
                  className="semantic-build-indicator"
                  role="img"
                  aria-label="Semantic Network build required"
                >
                  ●
                </span>
              )}
            </button>
            <FieldHelp label="Build Semantic Network" />
            <button
              type="button"
              className="secondary-button"
              disabled={buildDisabled}
              onClick={() => void build('full')}
            >
              {mode === 'full' ? 'Refreshing Semantic Network…' : 'Full Semantic Refresh'}
            </button>
            <FieldHelp label="Full Semantic Refresh" />
          </>
        )}
        {networkBuilding && !mode && <span role="status">Semantic Network build in progress…</span>}
      </div>
      {notice && (
        <p className="meta-notice" role="status">
          {notice}
        </p>
      )}
      {(error || catalogError) && (
        <p className="meta-error" role="alert">
          {error || catalogError}
        </p>
      )}
    </div>
  )
  const materialisedPreview = (
    <section className="materialised-table-preview" aria-label="Preview Materialised Table">
      <h2>Preview Materialised Table</h2>
      <div className="materialised-preview-toolbar">
        <Select
          label="Materialised table"
          value={selected}
          items={levels
            .filter((level) => catalog[level.objectId]?.rowCount != null)
            .map((level) => ({ value: level.objectId, label: level.name }))}
          required={false}
          onChange={setSelected}
          disabled={catalogLoading || busy}
        />
        {selected &&
          (['output', 'build'] as const).map((kind) => (
            <ViewSqlButton
              key={kind}
              label={kind === 'output' ? 'View Output SQL' : 'View Build SQL'}
              heading={kind === 'output' ? 'Output SQL' : 'Build SQL'}
              description={
                kind === 'output'
                  ? 'Reads the saved materialised table · Up to 50 rows'
                  : 'Recorded build statements, including writes · Viewing does not execute them'
              }
              disabled={!status || previewLoading || busy}
              loadSql={(signal) =>
                metadataApi.request(
                  base +
                    '/measures/execution/levels/' +
                    encodeURIComponent(selected) +
                    '/' +
                    kind +
                    '-sql',
                  'GET',
                  undefined,
                  undefined,
                  signal,
                )
              }
            />
          ))}
      </div>
      {catalogLoading && <p role="status">Loading materialised tables…</p>}
      {!catalogLoading && !Object.values(catalog).some((table) => table.rowCount != null) && (
        <p className="meta-hint">Build Semantic Network to create materialised tables.</p>
      )}
      {previewLoading && <p role="status">Loading table preview…</p>}
      {previewError && (
        <p className="meta-error" role="alert">
          {previewError}
        </p>
      )}
      {selected && status && (
        <>
          <p role="status">
            {status.state.replaceAll('_', ' ')}
            {status.rowCount != null ? ' · ' + status.rowCount + ' rows' : ''}
          </p>
          {status?.state === 'needs_rebuild' && (
            <p className="meta-hint">
              {editable
                ? 'These values were built from older inputs or numeric rules. Rebuild to use the current configuration.'
                : 'These values were built from older inputs or numeric rules. Create a new draft and build its semantic network to update them.'}
            </p>
          )}
          {status && Object.keys(status.incompleteMeasures ?? {}).length > 0 && (
            <p className="meta-hint" role="status">
              {Object.entries(status.incompleteMeasures ?? {})
                .map(
                  ([id, count]) =>
                    `${measures.find((m) => m.objectId === id)?.name ?? 'Measure'}: ${count} finer cells unavailable`,
                )
                .join('; ')}
              . Allocation weights are missing or total zero. Native values remain available; no
              equal fallback was applied.
            </p>
          )}
          {status && Object.keys(status.unavailable ?? {}).length > 0 && (
            <ul>
              {Object.entries(status.unavailable).map(([id, item]) => (
                <li key={id}>
                  {measures.find((m) => m.objectId === id)?.name ?? 'Measure'}: {item.message}
                </li>
              ))}
            </ul>
          )}
          {status && status.rowCount != null && (
            <PlanningResult
              data={status}
              name={levels.find((l) => l.objectId === selected)?.name ?? 'Measure values'}
              onRefresh={() => refreshPreview((value) => value + 1)}
            />
          )}
          {status && status.materialisation?.length > 0 && (
            <details className="meta-subsection">
              <summary>
                What gets materialised at {levels.find((l) => l.objectId === selected)?.name}
                <FieldHelp label="What gets materialised" />
              </summary>
              <p className="meta-hint">Measure placement and policies for this table.</p>
              <div className="meta-object-list">
                {status.materialisation.map((item) => (
                  <article className="meta-object-card" key={item.measureId}>
                    <div>
                      <h3>
                        {item.name}{' '}
                        <span className="meta-badge">
                          {status.unavailable[item.measureId] ? 'Unavailable' : item.operation}
                        </span>
                        {(status.unavailable[item.measureId] ||
                          item.operation === 'Unavailable') && <FieldHelp label="Unavailable" />}
                      </h3>
                      <p>
                        DimensionMembers: {item.nativeLevelName}.{' '}
                        {status.unavailable[item.measureId]?.message ?? item.explanation}
                      </p>
                      {item.aggregation && (
                        <p className="meta-hint">
                          NonTime: {item.aggregation.nonTime} · Time: {item.aggregation.time} ·
                          Order:{' '}
                          {item.aggregation.order === 'timeThenNonTime'
                            ? 'Time then NonTime'
                            : 'NonTime then Time'}
                        </p>
                      )}
                      {item.aggregation && (
                        <p className="meta-hint">
                          Edit disaggregation:{' '}
                          {item.disaggregation.mode === 'none'
                            ? 'None — repeat value'
                            : item.disaggregation.mode.replaceAll('_', ' ')}
                          {item.disaggregation.basisMeasureName
                            ? ' using ' + item.disaggregation.basisMeasureName
                            : ''}
                        </p>
                      )}
                    </div>
                  </article>
                ))}
              </div>
            </details>
          )}
        </>
      )}
    </section>
  )
  return (
    <DecisionViewPanel
      base={base}
      revisionVersion={revisionVersion}
      onCommitted={onCommitted}
      onCreateMeasure={onCreateMeasure}
      creatingMeasure={creatingMeasure}
      networkActions={networkActions}
      materialisedPreview={materialisedPreview}
      networkBusy={busy}
      onBusyChange={setDecisionBusy}
      onBuildRequiredChange={editable ? setBuildRequiredMessage : undefined}
    />
  )
}
