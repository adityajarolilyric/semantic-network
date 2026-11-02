import { FieldHelp } from '../components/FieldHelp'
import { useCallback, useEffect, useRef, useState } from 'react'
import { metadataApi } from './api'
import { PlanningResult, type PlanningTableStatus } from './PlanningTablePanel'
import { Select } from './editors/Fields'
import type { Measure, PlanningLevel } from './types'

type Values = PlanningTableStatus & {
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
export function MaterialisePanel({
  base,
  revisionVersion,
  baseLevelId,
  levels,
  measures,
  editable,
  onBuilt,
}: {
  base: string
  revisionVersion: number
  baseLevelId: string | null
  levels: PlanningLevel[]
  measures: Measure[]
  editable: boolean
  onBuilt: () => void
}) {
  const [selected, setSelected] = useState(baseLevelId ?? levels[0]?.objectId ?? '')
  const [status, setStatus] = useState<Values | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const path = base + '/measures/execution/levels/' + selected
  const currentPath = useRef(path)
  currentPath.current = path
  const load = useCallback(async () => {
    const data = await metadataApi.request<Values>(path + '?preview=true')
    if (currentPath.current === path) setStatus(data)
  }, [path])
  useEffect(() => {
    setStatus(null)
    setError('')
    if (!selected) return
    const controller = new AbortController()
    void metadataApi
      .request<Values>(path + '?preview=true', 'GET', undefined, undefined, controller.signal)
      .then(setStatus)
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message)
      })
    return () => controller.abort()
  }, [path, revisionVersion])
  useEffect(() => {
    if (status?.state !== 'building') return
    const timer = setInterval(() => void load().catch((e) => setError(e.message)), 1000)
    return () => clearInterval(timer)
  }, [status?.state, load])
  return (
    <section className="meta-card" aria-label="Materialise">
      <h2>Materialise saved definitions</h2>
      <p className="meta-hint">
        Build measures across compatible planning levels from the saved definitions. Planning
        populations must be ready first.
      </p>
      <div className="meta-toolbar">
        {editable && (
          <button
            type="button"
            className="secondary-button"
            disabled={busy || !baseLevelId || !measures.length || status?.state === 'building'}
            onClick={async () => {
              setBusy(true)
              setError('')
              try {
                await metadataApi.managedOperation(
                  base + '/measures/execution/build',
                  undefined,
                  revisionVersion,
                )
                await load()
                onBuilt()
              } catch (e) {
                setError(e instanceof Error ? e.message : 'Measure build failed.')
                try {
                  await load()
                } catch {
                  /* Keep original error. */
                }
              } finally {
                setBusy(false)
              }
            }}
          >
            {busy ? 'Building Semantic Network…' : 'Build Semantic Network'}
          </button>
        )}
        <FieldHelp label="Build Semantic Network" />
      </div>
      <div className="meta-subsection">
        <h2>Preview materialised tables</h2>
        <p className="meta-hint">
          Choose a planning level to view its built values. This selection does not change the
          network build.
        </p>
        <Select
          label="Preview planning level"
          value={selected}
          items={levels.map((l) => ({ value: l.objectId, label: l.name }))}
          onChange={setSelected}
        />
        <p role="status">
          Network
          <FieldHelp label="Network status" />: {status?.state.replaceAll('_', ' ') ?? 'not built'}
          {status?.rowCount != null ? ' · ' + status.rowCount + ' rows' : ''}
        </p>
        {error && (
          <p className="meta-error" role="alert">
            {error}
          </p>
        )}
        {!error && status?.lastRun?.error && (
          <p className="meta-error" role="alert">
            {status.lastRun.error.message}
          </p>
        )}
        {status?.state === 'needs_rebuild' && (
          <p className="meta-hint">
            {editable
              ? 'These values were built from older inputs or numeric rules. Rebuild to use the current configuration.'
              : 'These values were built from older inputs or numeric rules. Create a new draft and build its semantic network to update them.'}
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
            onRefresh={() => void load().catch((e) => setError(e.message))}
          />
        )}
        {status && status.materialisation?.length > 0 && (
          <details className="meta-subsection" open>
            <summary>
              What gets materialised at {levels.find((l) => l.objectId === selected)?.name}
              <FieldHelp label="What gets materialised" />
            </summary>
            <p className="meta-hint">
              The build covers all compatible planning levels. This plan explains the selected
              level; data and dependency checks run during the build.
            </p>
            <div className="meta-object-list">
              {status.materialisation.map((item) => (
                <article className="meta-object-card" key={item.measureId}>
                  <div>
                    <h3>
                      {item.name}{' '}
                      <span className="meta-badge">
                        {status.unavailable[item.measureId] ? 'Unavailable' : item.operation}
                      </span>
                      {(status.unavailable[item.measureId] || item.operation === 'Unavailable') && (
                        <FieldHelp label="Unavailable" />
                      )}
                    </h3>
                    <p>
                      Native level: {item.nativeLevelName}.{' '}
                      {status.unavailable[item.measureId]?.message ?? item.explanation}
                    </p>
                    {item.aggregation && (
                      <p className="meta-hint">
                        NonTime: {item.aggregation.nonTime} · Time: {item.aggregation.time} · Order:{' '}
                        {item.aggregation.order === 'timeThenNonTime'
                          ? 'Time then NonTime'
                          : 'NonTime then Time'}
                      </p>
                    )}
                    {item.aggregation && (
                      <p className="meta-hint">
                        Disaggregation:{' '}
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
      </div>
    </section>
  )
}
