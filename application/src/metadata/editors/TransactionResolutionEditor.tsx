import { useEffect, useRef, useState } from 'react'
import { FieldHelp } from '../../components/FieldHelp'
import { metadataApi } from '../api'
import type {
  GrainResolution,
  PlanningLevel,
  ResolutionRule,
  ResolutionTarget,
  SourceColumn,
} from '../types'
import {
  TransactionDateRangeEditor,
  newDateRange,
  type ResolutionCalendar,
} from './TransactionDateRangeEditor'
import { Check, Checks, Select } from './Fields'

type TargetOption = { target: ResolutionTarget; label: string }
const targetKey = (target?: ResolutionTarget | null) =>
  target?.columnId ? JSON.stringify([target.dimensionId ?? null, target.columnId]) : ''
export function newResolution(level: PlanningLevel): GrainResolution {
  return {
    axes: level.definition.grain.keyIds.map((grainColumnId) => ({
      grainColumnId,
      allowAll: false,
      rules: [],
    })),
    values: [],
    orderColumnIds: [],
    partitionColumnIds: [],
    passthroughColumnIds: [],
    overlapPolicy: 'last_applied_wins',
  }
}
export function resolutionComplete(value: GrainResolution | null | undefined) {
  return (
    !!value &&
    value.values.length > 0 &&
    value.orderColumnIds.length > 0 &&
    value.axes.length > 0 &&
    (!value.axes.some((a) => a.dateRange) || value.values.every((v) => !!v.timeMode)) &&
    value.axes.every(
      (axis) =>
        (axis.dateRange
          ? !!axis.dateRange.dimensionId &&
            !!axis.dateRange.calendarKeyColumnId &&
            !!axis.dateRange.startColumnId &&
            !!axis.dateRange.endColumnId
          : axis.allowAll || axis.rules.length > 0) &&
        axis.rules.every((rule) =>
          rule.kind === 'column'
            ? !!rule.sourceColumnId && !!rule.target?.columnId
            : !!rule.selectorColumnId &&
              !!rule.valueColumnId &&
              rule.selectors.length > 0 &&
              rule.selectors.every((s) => !!s.value && !!s.target.columnId),
        ),
    )
  )
}
function makeRule(
  kind: ResolutionRule['kind'],
  ruleId = 'rule_' + crypto.randomUUID(),
): ResolutionRule {
  return kind === 'column'
    ? { ruleId, kind, sourceColumnId: '', target: null, selectors: [], blankIsAbsent: true }
    : {
        ruleId,
        kind,
        selectorColumnId: '',
        valueColumnId: '',
        selectors: [],
        blankIsAbsent: true,
      }
}

export function TransactionResolutionEditor({
  value,
  onChange,
  columns,
  level,
  base,
  sourceId,
  calendarResolved = false,
}: {
  value: GrainResolution
  onChange: (value: GrainResolution) => void
  columns: SourceColumn[]
  level: PlanningLevel
  base?: string
  sourceId: string
  calendarResolved?: boolean
}) {
  const [targets, setTargets] = useState<TargetOption[]>([])
  const [error, setError] = useState('')
  const [calendars, setCalendars] = useState<ResolutionCalendar[]>([])
  const [timeGrainIds, setTimeGrainIds] = useState<string[]>([])
  useEffect(() => {
    if (!base) return
    const controller = new AbortController()
    setTargets([])
    setError('')
    void metadataApi
      .request<{
        targets: TargetOption[]
        calendars?: ResolutionCalendar[]
        timeGrainIds?: string[]
      }>(
        base +
          '/transactions/resolution/options?planningLevelId=' +
          encodeURIComponent(level.objectId),
        'GET',
        undefined,
        undefined,
        controller.signal,
      )
      .then((result) => {
        if (!controller.signal.aborted) {
          setTargets(result.targets)
          setCalendars(result.calendars ?? [])
          setTimeGrainIds(result.timeGrainIds ?? [])
        }
      })
      .catch((error) => {
        if (!controller.signal.aborted) setError(error.message)
      })
    return () => controller.abort()
  }, [base, level.objectId])
  const update = (patch: Partial<GrainResolution>) => onChange({ ...value, ...patch })
  const sourceOptions = columns.map((c) => ({ value: c.columnId, label: c.name }))
  const targetOptions = targets.map((t) => ({ value: targetKey(t.target), label: t.label }))
  const selectTarget = (key: string) =>
    targets.find((t) => targetKey(t.target) === key)?.target ?? null
  const hasRange = calendarResolved || value.axes.some((a) => !!a.dateRange)
  const values = new Set(value.values.map((v) => v.sourceColumnId))
  const numeric = columns.filter((c) => /^(?:U?Int|Float|Decimal)/i.test(c.physicalType))
  const orderOptions = columns
    .filter((c) => !values.has(c.columnId))
    .map((c) => ({ value: c.columnId, label: c.name }))
  return (
    <div className="transaction-resolution">
      <p className="meta-hint">
        Match assignments to actual native intersections. Rows apply in explicit source order; a
        later coarse row can overwrite detail. No source values are added together during
        resolution.
      </p>
      {error && (
        <p className="meta-error" role="alert">
          {error}
        </p>
      )}
      <div className="resolution-axes">
        {value.axes.map((axis, index) => {
          const name =
            level.definition.grain.columns.find((c) => c.grainColumnId === axis.grainColumnId)
              ?.name ?? 'Native key'
          const setAxis = (patch: Partial<typeof axis>) =>
            update({ axes: value.axes.map((a, i) => (i === index ? { ...a, ...patch } : a)) })
          const setRule = (at: number, rule: ResolutionRule) =>
            setAxis({ rules: axis.rules.map((r, i) => (i === at ? rule : r)) })
          return (
            <fieldset
              className="resolution-axis"
              key={axis.grainColumnId}
              aria-label={'Resolve ' + name}
            >
              <legend>{name}</legend>
              {!calendarResolved &&
                value.axes.some((a) => a.dateRange) &&
                (axis.dateRange ||
                  timeGrainIds.includes(axis.grainColumnId) ||
                  (calendars.length > 0 && timeGrainIds.length === 0)) && (
                  <Select
                    label={'Time resolution for ' + name}
                    value={axis.dateRange ? 'range' : 'exact'}
                    items={[
                      { value: 'exact', label: 'Exact value' },
                      { value: 'range', label: 'Date range' },
                    ]}
                    disabled={hasRange && !axis.dateRange}
                    onChange={(mode) => {
                      const axes = value.axes.map((a, i) =>
                        i === index
                          ? {
                              ...a,
                              rules: [],
                              allowAll: false,
                              dateRange:
                                mode === 'range' ? newDateRange(calendars, a.grainColumnId) : null,
                            }
                          : a,
                      )
                      const range = axes.some((a) => !!a.dateRange)
                      update({
                        axes,
                        values: value.values.map((v) => ({
                          ...v,
                          timeMode: range ? (v.timeMode ?? 'split') : null,
                        })),
                      })
                    }}
                  />
                )}
              {axis.dateRange ? (
                <TransactionDateRangeEditor
                  value={axis.dateRange}
                  onChange={(dateRange) => setAxis({ dateRange })}
                  calendars={calendars}
                  grainId={axis.grainColumnId}
                  name={name}
                  columns={columns}
                />
              ) : (
                <>
                  {axis.rules.map((rule, ri) => (
                    <fieldset
                      className="resolution-rule"
                      key={rule.ruleId}
                      aria-label={'Rule ' + (ri + 1) + ' for ' + name}
                    >
                      <Select
                        label="Match using"
                        value={rule.kind}
                        items={[
                          { value: 'column', label: 'Column equals attribute' },
                          { value: 'level_value', label: 'Level and value' },
                        ]}
                        onChange={(kind) =>
                          setRule(ri, makeRule(kind as ResolutionRule['kind'], rule.ruleId))
                        }
                      />
                      {rule.kind === 'column' ? (
                        <div className="meta-grid">
                          <Select
                            label="Match source column"
                            value={rule.sourceColumnId ?? ''}
                            items={sourceOptions}
                            onChange={(sourceColumnId) => setRule(ri, { ...rule, sourceColumnId })}
                          />
                          <Select
                            label="Match native attribute"
                            value={targetKey(rule.target)}
                            items={targetOptions}
                            onChange={(key) => setRule(ri, { ...rule, target: selectTarget(key) })}
                          />
                        </div>
                      ) : (
                        <>
                          <div className="meta-grid">
                            <Select
                              label="Level column"
                              value={rule.selectorColumnId ?? ''}
                              items={sourceOptions}
                              onChange={(selectorColumnId) =>
                                setRule(ri, { ...rule, selectorColumnId, selectors: [] })
                              }
                            />
                            <Select
                              label="Matching value column"
                              value={rule.valueColumnId ?? ''}
                              items={sourceOptions}
                              onChange={(valueColumnId) => setRule(ri, { ...rule, valueColumnId })}
                            />
                          </div>
                          <DiscoveredSelectors
                            rule={rule}
                            base={base}
                            sourceId={sourceId}
                            targets={targets}
                            onChange={(next) => setRule(ri, next)}
                          />
                        </>
                      )}
                      <div className="resolution-rule-footer">
                        <Check
                          label="Treat empty text as absent"
                          value={rule.blankIsAbsent !== false}
                          onChange={(blankIsAbsent) => setRule(ri, { ...rule, blankIsAbsent })}
                        />
                        <button
                          type="button"
                          className="meta-danger"
                          onClick={() => setAxis({ rules: axis.rules.filter((_, i) => i !== ri) })}
                        >
                          Remove rule
                        </button>
                      </div>
                    </fieldset>
                  ))}
                  <div className="meta-toolbar">
                    <button
                      type="button"
                      className="secondary-button"
                      disabled={axis.rules.length >= 8}
                      onClick={() => setAxis({ rules: [...axis.rules, makeRule('column')] })}
                    >
                      Add matching rule
                    </button>
                  </div>
                  <Check
                    label={'Allow all ' + name + ' members when blank'}
                    value={axis.allowAll}
                    onChange={(allowAll) => setAxis({ allowAll })}
                  />
                </>
              )}
            </fieldset>
          )
        })}
      </div>
      <Checks
        label="Additional columns to retain"
        selected={value.passthroughColumnIds}
        items={sourceOptions.filter(
          (c) => !values.has(c.value) && !value.partitionColumnIds.includes(c.value),
        )}
        onChange={(passthroughColumnIds) => update({ passthroughColumnIds })}
      />
      <div className="meta-grid meta-subsection">
        <div>
          <h3>Application order</h3>
          <p className="meta-hint">
            Higher values apply later. Text is ordered alphabetically; numbers and dates use their
            natural order. ClickHouse row order is never used.
          </p>
          <Select
            label="Apply-order column"
            value={value.orderColumnIds[0] ?? ''}
            items={orderOptions}
            onChange={(cid) =>
              update({
                orderColumnIds: cid ? [cid] : [],
              })
            }
          />
          <p className="meta-hint">
            Choose values that uniquely order overlapping assignments. Equal order values fail
            resolution.
          </p>
        </div>
        <div>
          <h3>
            Resolve value columns <FieldHelp label="Resolve value columns" />
          </h3>
          <p className="meta-hint">
            {hasRange
              ? 'Across members splits among non-time members; Across dates splits among calendar days.'
              : 'Split uses every original matching native cell.'}{' '}
            Overridden shares are not redistributed. Copy repeats the full value.
          </p>
          <div className="resolution-value-scroll">
            <table className="data-table resolution-values">
              <thead>
                <tr>
                  <th>Use</th>
                  <th>Value column</th>
                  <th>{hasRange ? 'Across members' : 'Policy'}</th>
                  {hasRange && <th>Across dates</th>}
                </tr>
              </thead>
              <tbody>
                {numeric.map((c) => {
                  const policy = value.values.find((v) => v.sourceColumnId === c.columnId)
                  return (
                    <tr key={c.columnId}>
                      <td>
                        <input
                          type="checkbox"
                          aria-label={'Resolve ' + c.name}
                          checked={!!policy}
                          disabled={value.orderColumnIds.includes(c.columnId)}
                          onChange={(e) =>
                            update({
                              values: e.target.checked
                                ? [
                                    ...value.values,
                                    {
                                      sourceColumnId: c.columnId,
                                      mode: 'split',
                                      ...(hasRange
                                        ? {
                                            timeMode: (calendarResolved ? 'copy' : 'split') as
                                              'split' | 'copy',
                                          }
                                        : {}),
                                    },
                                  ]
                                : value.values.filter((v) => v.sourceColumnId !== c.columnId),
                            })
                          }
                        />
                      </td>
                      <td>{c.name}</td>
                      <td>
                        {policy && (
                          <select
                            aria-label={'Resolution policy for ' + c.name}
                            value={policy.mode}
                            onChange={(e) =>
                              update({
                                values: value.values.map((v) =>
                                  v.sourceColumnId === c.columnId
                                    ? { ...v, mode: e.target.value as 'split' | 'copy' }
                                    : v,
                                ),
                              })
                            }
                          >
                            <option value="split">Split evenly</option>
                            <option value="copy">Copy</option>
                          </select>
                        )}
                      </td>
                      {hasRange && (
                        <td>
                          {policy && (
                            <select
                              aria-label={'Across dates policy for ' + c.name}
                              value={policy.timeMode ?? ''}
                              required
                              onChange={(e) =>
                                update({
                                  values: value.values.map((v) =>
                                    v.sourceColumnId === c.columnId
                                      ? { ...v, timeMode: e.target.value as 'split' | 'copy' }
                                      : v,
                                  ),
                                })
                              }
                            >
                              <option value="" disabled>
                                Select policy
                              </option>
                              <option value="split">Split evenly</option>
                              <option value="copy">Copy</option>
                            </select>
                          )}
                        </td>
                      )}
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  )
}

function DiscoveredSelectors({
  rule,
  base,
  sourceId,
  targets,
  onChange,
}: {
  rule: ResolutionRule
  base?: string
  sourceId: string
  targets: TargetOption[]
  onChange: (rule: ResolutionRule) => void
}) {
  const [refresh, setRefresh] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const latest = useRef({ rule, onChange })
  latest.current = { rule, onChange }
  useEffect(() => {
    if (!base || !sourceId || !rule.selectorColumnId) return
    const controller = new AbortController()
    setLoading(true)
    setError('')
    void metadataApi
      .request<{ values: (string | number | boolean | null)[]; nextOffset: number | null }>(
        base +
          '/managed-tables/' +
          encodeURIComponent(sourceId) +
          '/attribute-values?managedOutput=true&columnId=' +
          encodeURIComponent(rule.selectorColumnId) +
          '&limit=34',
        'GET',
        undefined,
        undefined,
        controller.signal,
      )
      .then((result) => {
        if (controller.signal.aborted) return
        const values = [...new Set(result.values.filter((v) => v !== null && v !== '').map(String))]
        if (result.nextOffset !== null || values.length > 32)
          throw new Error('More than 32 selector values. Choose another Level column.')
        if (values.some((value) => value.length > 200))
          throw new Error('Selector values must be at most 200 characters.')
        if (!values.length) throw new Error('No selector values found in this Level column.')
        const current = latest.current
        const selectors = values.map(
          (value) =>
            current.rule.selectors.find((s) => s.value === value) ?? {
              value,
              target: { columnId: '' },
            },
        )
        if (JSON.stringify(selectors) !== JSON.stringify(current.rule.selectors))
          current.onChange({ ...current.rule, selectors })
      })
      .catch((error) => {
        if (!controller.signal.aborted) setError(error.message)
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [base, sourceId, rule.selectorColumnId, refresh])
  return (
    <div className="resolution-discovery">
      <div className="meta-toolbar">
        <p className="meta-hint">
          Selector values are discovered from the saved managed table. Map each value to an
          attribute.
        </p>
        <button
          type="button"
          className="secondary-button"
          disabled={loading || !base || !rule.selectorColumnId}
          onClick={() => setRefresh((v) => v + 1)}
        >
          {loading ? 'Discovering…' : 'Discover'}
        </button>
      </div>
      {error && (
        <p className="meta-error" role="alert">
          {error}
        </p>
      )}
      {rule.selectors.map((selector, index) => (
        <div className="resolution-selector" key={selector.value}>
          <span className="resolution-selector-value">{selector.value}</span>
          <Select
            label={'Attribute for ' + selector.value}
            value={targetKey(selector.target)}
            items={targets.map((t) => ({ value: targetKey(t.target), label: t.label }))}
            onChange={(key) =>
              onChange({
                ...rule,
                selectors: rule.selectors.map((s, i) =>
                  i === index
                    ? {
                        ...s,
                        target: targets.find((t) => targetKey(t.target) === key)?.target ?? {
                          columnId: '',
                        },
                      }
                    : s,
                ),
              })
            }
          />
        </div>
      ))}
    </div>
  )
}
