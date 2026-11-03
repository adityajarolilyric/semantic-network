import { useEffect, useState } from 'react'
import { metadataApi } from '../api'
import type { Measure, PlanningLevel } from '../types'

type BasePath = {
  baseLevelId: string | null
  relationship: string
  reason: string | null
  expansionAxes: ('nonTime' | 'time')[]
}
export function MeasureBaseVisibility({
  definition,
  levels,
  base,
  baseLevelId,
}: {
  definition: Measure['definition']
  levels: PlanningLevel[]
  base?: string
  baseLevelId?: string | null
}) {
  const native = definition.nativePlanningLevelId
  const visible = !!baseLevelId && !!native && native !== baseLevelId
  const path =
    visible && base
      ? base +
        '/measures/execution/base-visibility?nativePlanningLevelId=' +
        encodeURIComponent(native)
      : null
  const [result, setResult] = useState<{ path: string; value: BasePath } | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    setResult(null)
    setError('')
    if (!path) return
    const controller = new AbortController()
    void metadataApi
      .request<BasePath>(path, 'GET', undefined, undefined, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setResult({ path, value })
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message)
      })
    return () => controller.abort()
  }, [path, baseLevelId])
  if (!visible) return null
  const info =
    result?.path === path && result.value.baseLevelId === baseLevelId ? result.value : null
  const name = levels.find((l) => l.objectId === baseLevelId)?.name ?? 'Base'
  const policy = definition.aggregation
  const axes =
    policy?.order === 'timeThenNonTime'
      ? (['nonTime', 'time'] as const)
      : (['time', 'nonTime'] as const)
  return (
    <section className="meta-subsection" aria-label="Base Level Visibility policy">
      <h3>Base Level Visibility policy</h3>
      <p className="meta-hint">
        Base planning level: <strong>{name}</strong>. This describes source materialisation; the
        Dis-aggregation policy above controls edits.
      </p>
      {error ? (
        <p className="meta-error" role="alert">
          Unable to check the Base path: {error}
        </p>
      ) : !info ? (
        <p className="meta-hint">
          {result?.path === path && result.value.baseLevelId !== baseLevelId
            ? 'Base planning level changed. Reload metadata to see the current policy.'
            : base
              ? 'Checking the path to Base…'
              : 'A proven planning-level path is required to make this measure visible at Base.'}
        </p>
      ) : info.relationship === 'incompatible' ? (
        <p className="meta-hint">
          This measure cannot reach Base. It stays at its output planning level and compatible
          coarser levels. {info.reason}
        </p>
      ) : definition.measureType === 'calculated' &&
        definition.calculationType === 'AggregateFirst' ? (
        <p className="meta-hint">
          The formula is recalculated at Base from its dependencies. Each dependency must be
          available at Base; the formula result is not disaggregated.
        </p>
      ) : info.relationship === 'equivalent' || info.relationship === 'same' ? (
        <p className="meta-hint">
          The two levels have equivalent grain. Values transfer to matching Base rows without
          splitting.
        </p>
      ) : info.relationship === 'roll_up' ? (
        <p className="meta-hint">
          The output level is finer than Base. Values are aggregated to Base using the selected
          Non-time ({policy?.nonTime}) and Time ({policy?.time}) policies.
        </p>
      ) : (
        <>
          <p className="meta-hint">
            Values expand into matching Base rows, in reverse aggregation order:
          </p>
          <ol className="meta-hint">
            {axes
              .filter((axis) => info.expansionAxes.includes(axis))
              .map((axis) => (
                <li key={axis}>
                  <strong>
                    {axis === 'time' ? 'Time' : 'Non-time'} — {policy?.[axis]?.toUpperCase()}:{' '}
                  </strong>
                  {policy?.[axis] === 'sum'
                    ? 'Split equally across eligible finer combinations. For example, 100 across 4 combinations becomes 25 each.'
                    : 'Repeat the value across eligible finer combinations. For example, 100 remains 100 in each combination.'}
                </li>
              ))}
          </ol>
          <p className="meta-hint">
            Axes with unchanged grain do not distribute values. Counts use actual planning
            intersections.
          </p>
        </>
      )}
    </section>
  )
}
