import { useEffect, useState } from 'react'
import { metadataApi } from '../api'
import { Select } from './Fields'
import type { Measure, GrainOption } from '../types'

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
  levels: Pick<GrainOption, 'objectId' | 'name'>[]
  base?: string
  baseLevelId?: string | null
}) {
  const native = definition.nativeGrainId
  const visible = !!baseLevelId && !!native && native !== baseLevelId
  const path =
    visible && base
      ? base + '/measures/execution/base-visibility?nativeGrainId=' + encodeURIComponent(native)
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
        Base dimension: <strong>{name}</strong>. This describes source materialisation; the
        Dis-aggregation policy above controls edits.
      </p>
      {error ? (
        <p className="meta-error" role="alert">
          Unable to check the Base path: {error}
        </p>
      ) : !info ? (
        <p className="meta-hint">
          {result?.path === path && result.value.baseLevelId !== baseLevelId
            ? 'Base dimension changed. Reload metadata to see the current policy.'
            : base
              ? 'Checking the path to Base…'
              : 'A proven dimension path is required to make this measure visible at Base.'}
        </p>
      ) : info.relationship === 'incompatible' ? (
        <p className="meta-hint">
          This measure cannot reach Base. It stays at its output dimension and compatible coarser
          levels. {info.reason}
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
          <div className="meta-grid">
            {axes
              .filter((axis) => info.expansionAxes.includes(axis))
              .map((axis) => (
                <div key={axis}>
                  <Select
                    label={(axis === 'time' ? 'Time' : 'Non-time') + ' Base visibility policy'}
                    value={policy?.[axis] === 'sum' ? 'equal' : 'repeat'}
                    items={[
                      { value: 'repeat', label: 'None – Repeat Values' },
                      { value: 'equal', label: 'Equal' },
                    ]}
                    onChange={() => {}}
                    disabled
                  />
                  <p className="meta-hint">
                    Derived from {axis === 'time' ? 'Time' : 'Non-time'} aggregation (
                    {policy?.[axis]?.toUpperCase()}).{' '}
                    {policy?.[axis] === 'sum'
                      ? '100 across 4 eligible combinations becomes 25 each.'
                      : '100 remains 100 in each eligible combination.'}
                  </p>
                </div>
              ))}
          </div>
          <p className="meta-hint">
            Axes with unchanged grain do not distribute values. Counts use actual planning
            intersections.
          </p>
        </>
      )}
    </section>
  )
}
