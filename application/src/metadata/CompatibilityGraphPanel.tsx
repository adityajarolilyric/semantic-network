import { useEffect, useId, useState } from 'react'
import { metadataApi } from './api'
import type { ViewRef } from './decisionViewPreferences'

type Status = 'ready' | 'needs_materialisation' | 'unavailable'
type Verdict = {
  status: Status
  reason: string
  operation?: string
  sourceDimensionName?: string
}
type Connection = Verdict & { dimensionId: string }
type MeasureNode = {
  measureId: string
  name: string
  nativeGrainId: string
  nativeDimensionName: string
  connections: Connection[]
}
export type CompatibilityGraph = {
  revisionVersion: number
  generationId: string | null
  measures: MeasureNode[]
  levels: { dimensionId: string; name: string }[]
  contexts: { dimensionId: string; name: string; selection: Verdict | null }[]
  selection: { measureId: string; name: string }[]
  totalMeasures: number
  hasMoreMeasures: boolean
  hasMoreLevels: boolean
}
const labels: Record<Status, string> = {
  ready: 'Ready',
  needs_materialisation: 'Needs materialisation',
  unavailable: 'Unavailable',
}
function bestStatus(connections: Verdict[]): Status {
  return connections.some((item) => item.status === 'ready')
    ? 'ready'
    : connections.some((item) => item.status === 'needs_materialisation')
      ? 'needs_materialisation'
      : 'unavailable'
}

/** Read-only graph. Every status and whole-selection decision comes from the server. */
export function CompatibilityGraphPanel({
  base,
  revisionVersion,
  dimensionId,
  measureIds = [],
  view,
  refreshKey,
  onSelectionChange,
  selectionDisabled = false,
}: {
  base: string
  revisionVersion?: number
  dimensionId?: string
  measureIds?: string[]
  view?: { rows: ViewRef[]; columns: ViewRef[] }
  refreshKey?: string | null
  onSelectionChange?: (ids: string[]) => void
  selectionDisabled?: boolean
}) {
  const [selected, setSelected] = useState<string[]>(measureIds)
  const [level, setLevel] = useState(dimensionId ?? '')
  const [search, setSearch] = useState('')
  const [measureOffset, setMeasureOffset] = useState(0)
  const [levelOffset, setLevelOffset] = useState(0)
  const [refresh, setRefresh] = useState(0)
  const [graph, setGraph] = useState<CompatibilityGraph | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [inspected, inspect] = useState<string | null>(null)
  const panelId = useId()
  const selectionKey = JSON.stringify(measureIds)
  const viewKey = JSON.stringify(view)
  useEffect(() => {
    setSelected(measureIds)
    setLevel(dimensionId ?? '')
    setMeasureOffset(0)
    setLevelOffset(0)
    inspect(null)
    setGraph(null)
    setError('')
  }, [base, dimensionId])
  useEffect(() => {
    setSelected(measureIds)
  }, [selectionKey])
  useEffect(() => {
    const focus = () => setRefresh((current) => current + 1)
    window.addEventListener('focus', focus)
    return () => window.removeEventListener('focus', focus)
  }, [])
  useEffect(() => {
    const controller = new AbortController()
    const timer = setTimeout(() => {
      setLoading(true)
      setError('')
      void metadataApi
        .request<CompatibilityGraph>(
          base + '/compatibility',
          'POST',
          {
            measures: selected,
            dimensionId: view ? null : level || null,
            view: view ? { ...view, measures: selected } : null,
            search,
            measureOffset,
            levelOffset,
          },
          undefined,
          controller.signal,
        )
        .then((result) => {
          if (!controller.signal.aborted) setGraph(result)
        })
        .catch((error) => {
          if (!controller.signal.aborted) setError(error.message)
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false)
        })
    }, 150)
    return () => {
      clearTimeout(timer)
      controller.abort()
    }
  }, [
    base,
    revisionVersion,
    refreshKey,
    refresh,
    JSON.stringify(selected),
    level,
    viewKey,
    search,
    measureOffset,
    levelOffset,
  ])
  function choose(ids: string[]) {
    if (selectionDisabled) return
    setSelected(ids)
    onSelectionChange?.(ids)
  }
  function toggle(mid: string) {
    inspect(mid)
    choose(
      selected.includes(mid)
        ? selected.filter((id) => id !== mid)
        : selected.length < 12
          ? [...selected, mid]
          : selected,
    )
  }
  const active = graph?.measures.find((measure) => measure.measureId === inspected)
  const contexts = graph?.contexts ?? []
  const nodes = graph?.measures ?? []
  const height = Math.max(180, Math.max(nodes.length, contexts.length) * 64 + 30)
  const nodeY = (index: number) => 32 + index * 64
  const hubY = (index: number) =>
    contexts.length === 1
      ? Math.min(height / 2, 220)
      : 32 + (index * (height - 64)) / Math.max(1, contexts.length - 1)
  const selectionStatus = bestStatus(
    contexts.map((context) => context.selection).filter((value): value is Verdict => !!value),
  )
  return (
    <section
      className="compatibility-panel"
      aria-label="Measure compatibility graph"
      aria-busy={loading}
    >
      <div className="compatibility-heading">
        <div>
          <h2>Measure compatibility</h2>
          <p>
            Choose measures to find shared plotting contexts. Connections apply to the complete
            selection.
          </p>
        </div>
        <button
          type="button"
          className="secondary-button"
          onClick={() => setRefresh((current) => current + 1)}
          disabled={loading}
        >
          Refresh graph
        </button>
      </div>
      <div className="compatibility-controls">
        <label>
          Find measures
          <input
            type="search"
            value={search}
            placeholder="Search by measure name"
            onChange={(event) => {
              setSearch(event.target.value)
              setMeasureOffset(0)
            }}
          />
        </label>
        {!view && !dimensionId && (
          <label>
            Plotting context
            <select
              value={level}
              onChange={(event) => {
                setLevel(event.target.value)
                setLevelOffset(0)
              }}
            >
              <option value="">Browse Dimensions</option>
              {graph?.levels.map((item) => (
                <option key={item.dimensionId} value={item.dimensionId}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>
        )}
        {(view || dimensionId) && (
          <p className="meta-hint">
            {view
              ? 'Using the current pivot dimensionMembers.'
              : 'Using the formula’s Output Dimension.'}
          </p>
        )}
      </div>
      <div className="compatibility-legend">
        <span className="compatibility-dot ready" />
        Ready <span className="compatibility-dot needs_materialisation" />
        Needs materialisation <span className="compatibility-dot unavailable" />
        Unavailable
      </div>
      {!!selected.length && (
        <div className="compatibility-selected" aria-label="Selected graph measures">
          {selected.map((mid) => (
            <button
              type="button"
              key={mid}
              className="meta-tag"
              onClick={() => choose(selected.filter((id) => id !== mid))}
              aria-label={
                'Remove ' +
                (graph?.selection.find((item) => item.measureId === mid)?.name ?? 'measure') +
                ' from graph'
              }
            >
              {graph?.selection.find((item) => item.measureId === mid)?.name ?? 'Loading…'} ×
            </button>
          ))}
          <button type="button" className="compatibility-clear" onClick={() => choose([])}>
            Clear selection
          </button>
        </div>
      )}
      {error && (
        <p className="meta-error" role="alert">
          {error}
        </p>
      )}
      {loading && (
        <p className="meta-hint" role="status">
          Checking compatibility…
        </p>
      )}
      {graph && !error && (
        <>
          {!!selected.length && (
            <p className={`compatibility-summary ${selectionStatus}`} role="status">
              {selectionStatus === 'ready'
                ? 'The full selection can be plotted together in a shown context.'
                : selectionStatus === 'needs_materialisation'
                  ? 'A shared context is structurally possible. Prepare the required planning populations and materialisation.'
                  : 'No shared plotting context is available in the shown contexts.'}
            </p>
          )}
          {!nodes.length ? (
            <p className="meta-hint">
              {search
                ? 'No measures match this search.'
                : 'Create measures to explore their compatibility.'}
            </p>
          ) : !contexts.length ? (
            <p className="meta-hint">Create a DimensionMembers to explore plotting contexts.</p>
          ) : (
            <div className="compatibility-body">
              <div
                className="compatibility-canvas-scroll"
                tabIndex={0}
                aria-label="Compatibility graph canvas"
              >
                <div className="compatibility-lane-labels">
                  <strong>Measures</strong>
                  <strong>Dimensions</strong>
                </div>
                <div className="compatibility-canvas" style={{ height }}>
                  <svg viewBox={`0 0 720 ${height}`} preserveAspectRatio="none" aria-hidden="true">
                    {nodes.flatMap((measure, index) =>
                      measure.connections
                        .filter(
                          (connection) =>
                            connection.status !== 'unavailable' &&
                            (selected.includes(measure.measureId) ||
                              inspected === measure.measureId ||
                              connection.dimensionId === measure.nativeGrainId),
                        )
                        .map((connection) => {
                          const target = contexts.findIndex(
                            (context) => context.dimensionId === connection.dimensionId,
                          )
                          if (target < 0) return null
                          return (
                            <path
                              key={measure.measureId + connection.dimensionId}
                              className={connection.status}
                              d={`M 238 ${nodeY(index)} C 350 ${nodeY(index)}, 370 ${hubY(target)}, 482 ${hubY(target)}`}
                            />
                          )
                        }),
                    )}
                  </svg>
                  {nodes.map((measure, index) => (
                    <button
                      key={measure.measureId}
                      type="button"
                      aria-label={'Select ' + measure.name + ' for compatibility'}
                      aria-pressed={selected.includes(measure.measureId)}
                      disabled={selectionDisabled}
                      aria-controls={panelId}
                      className={`compatibility-measure ${bestStatus(measure.connections)} ${selected.includes(measure.measureId) ? 'is-selected' : ''}`}
                      style={{ top: nodeY(index) }}
                      onClick={() => toggle(measure.measureId)}
                      onFocus={() => inspect(measure.measureId)}
                      title={measure.name}
                    >
                      <strong>{measure.name}</strong>
                      <small>{measure.nativeDimensionName}</small>
                    </button>
                  ))}
                  {contexts.map((context, index) => (
                    <button
                      key={context.dimensionId}
                      type="button"
                      className={`compatibility-hub ${context.selection?.status ?? ''}`}
                      style={{ top: hubY(index) }}
                      onClick={() => {
                        if (!view && !dimensionId) {
                          setLevel(context.dimensionId)
                          setLevelOffset(0)
                        }
                      }}
                      title={context.name}
                      aria-label={'Inspect Dimension ' + context.name}
                    >
                      <strong>{context.name}</strong>
                      <small>
                        {context.selection ? labels[context.selection.status] : 'Plotting context'}
                      </small>
                    </button>
                  ))}
                </div>
              </div>
              <aside
                className="compatibility-details"
                id={panelId}
                aria-label="Compatibility explanations"
              >
                <h3>{active?.name ?? 'Explore a measure'}</h3>
                {!active ? (
                  <p>
                    Select a measure to see its paths and reasons. Colours describe compatibility in
                    the shown contexts.
                  </p>
                ) : (
                  <>
                    <p>
                      Output level: <strong>{active.nativeDimensionName}</strong>
                    </p>
                    {active.connections.map((connection) => (
                      <div key={connection.dimensionId} className="compatibility-reason">
                        <strong>
                          {
                            contexts.find(
                              (context) => context.dimensionId === connection.dimensionId,
                            )?.name
                          }
                        </strong>
                        <span className={'compatibility-status ' + connection.status}>
                          {labels[connection.status]}
                        </span>
                        <p>{connection.reason}</p>
                        {connection.sourceDimensionName && (
                          <small>Source: {connection.sourceDimensionName}</small>
                        )}
                      </div>
                    ))}
                  </>
                )}
              </aside>
            </div>
          )}
          <div className="compatibility-paging">
            <div>
              <button
                type="button"
                className="secondary-button"
                disabled={!measureOffset}
                onClick={() => setMeasureOffset(Math.max(0, measureOffset - 24))}
              >
                Previous measures
              </button>
              <span>
                {nodes.length ? measureOffset + 1 : 0}–{measureOffset + nodes.length} of{' '}
                {graph.totalMeasures}
              </span>
              <button
                type="button"
                className="secondary-button"
                disabled={!graph.hasMoreMeasures}
                onClick={() => setMeasureOffset(measureOffset + 24)}
              >
                Next measures
              </button>
            </div>
            {!view && !level && !dimensionId && (
              <div>
                <button
                  type="button"
                  className="secondary-button"
                  disabled={!levelOffset}
                  onClick={() => setLevelOffset(Math.max(0, levelOffset - 8))}
                >
                  Previous levels
                </button>
                <button
                  type="button"
                  className="secondary-button"
                  disabled={!graph.hasMoreLevels}
                  onClick={() => setLevelOffset(levelOffset + 8)}
                >
                  Next levels
                </button>
              </div>
            )}
          </div>
        </>
      )}
    </section>
  )
}
