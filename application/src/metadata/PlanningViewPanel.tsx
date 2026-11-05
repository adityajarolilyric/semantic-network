import {
  clearViewPreferences,
  viewReference,
  type ViewRef,
  readViewPreferences,
  saveViewPreferences,
} from './planningViewPreferences'
import { usePinnedPivotColumns } from './usePinnedPivotColumns'
import { formatNumericDisplay } from '../lib/numberFormat'
import { useEffect, useRef, useState } from 'react'
import { FieldHelp } from '../components/FieldHelp'
import { PlanningEditCell } from './PlanningEditCell'
import { metadataApi } from './api'

type Ref = ViewRef
type Field = Ref & { key: string; label: string }
type Options = {
  buildId?: string | null
  dimensions: { dimensionId: string; name: string; columns: { columnId: string; name: string }[] }[]
  planningLevels?: {
    planningLevelId: string
    name: string
    columns: { columnId: string; name: string }[]
  }[]
  measures: { measureId: string; name: string }[]
}
export type PlanningViewResult = {
  fields: (Ref & { id: string; label: string; axis: 'row' | 'column' })[]
  measures: {
    measureId: string
    name: string
    calculation: string
    valueType?: string
    editable?: boolean
    editReason?: string | null
    writeLevelName?: string
  }[]
  editContext?: { generationId: string; revisionVersion: number }
  warnings?: { measureId: string; message: string }[]
  cellStates?: (string | null)[][]
  rows: (string | null)[][]
  groupCount: number
  plan: { planningLevelName: string; usesBase: boolean; explanation: string; nativeOnly?: boolean }
}
const display = (value: string | null) =>
  value === null ? '(empty)' : value === '' ? '(blank)' : value

function AxisEditor({
  name,
  selected,
  available,
  onChange,
}: {
  name: string
  selected: Field[]
  available: Field[]
  onChange: (fields: Field[]) => void
}) {
  return (
    <fieldset className="planning-axis">
      <legend>
        {name}
        <FieldHelp label={name} topic={'Planning View ' + name.toLowerCase()} />
      </legend>
      <p className="meta-hint">
        {name === 'Rows' ? 'Group records down the table.' : 'Compare groups across the table.'}
      </p>
      <label className="planning-axis-picker">
        Add {name.toLowerCase()} attribute
        <select
          value=""
          disabled={!available.length}
          onChange={(e) => {
            const field = available.find((f) => f.key === e.target.value)
            if (field) onChange([...selected, field])
          }}
        >
          <option value="">Choose an attribute…</option>
          {available.map((f) => (
            <option key={f.key} value={f.key}>
              {f.label}
            </option>
          ))}
        </select>
      </label>
      <ol className="planning-axis-fields">
        {selected.map((field, index) => (
          <li key={field.key}>
            <span>{field.label}</span>
            <div>
              <button
                type="button"
                className="planning-field-action"
                disabled={!index}
                aria-label={'Move ' + field.label + ' up in ' + name.toLowerCase()}
                onClick={() => {
                  const next = [...selected]
                  ;[next[index - 1], next[index]] = [next[index], next[index - 1]]
                  onChange(next)
                }}
              >
                ↑
              </button>
              <button
                type="button"
                className="planning-field-action"
                aria-label={'Remove ' + field.label + ' from ' + name.toLowerCase()}
                onClick={() => onChange(selected.filter((f) => f.key !== field.key))}
              >
                ×
              </button>
            </div>
          </li>
        ))}
      </ol>
      {!selected.length && (
        <p className="planning-axis-empty">
          {name === 'Rows' ? 'All selected data in one row' : 'Measures shown side by side'}
        </p>
      )}
    </fieldset>
  )
}

export function PlanningPivot({
  result,
  onEdit,
  busy = false,
}: {
  result: PlanningViewResult
  busy?: boolean
  onEdit?: (mid: string, members: (string | null)[], value: number | null) => Promise<void>
}) {
  const rowFields = result.fields.filter((f) => f.axis === 'row')
  const columnFields = result.fields.filter((f) => f.axis === 'column')
  const table = useRef<HTMLTableElement>(null)
  const dimensionCount = Math.max(1, rowFields.length)
  usePinnedPivotColumns(table, dimensionCount + 1, result)
  const rowKeys = new Map<string, (string | null)[]>()
  const columnKeys = new Map<string, (string | null)[]>()
  const cells = new Map<string, (string | null)[]>()
  const states = new Map<string, (string | null)[]>()
  for (const [recordIndex, record] of result.rows.entries()) {
    const rows = record.slice(0, rowFields.length)
    const columns = record.slice(rowFields.length, rowFields.length + columnFields.length)
    rowKeys.set(JSON.stringify(rows), rows)
    columnKeys.set(JSON.stringify(columns), columns)
    const key = JSON.stringify([rows, columns])
    cells.set(key, record.slice(rowFields.length + columnFields.length))
    states.set(key, result.cellStates?.[recordIndex] ?? [])
  }
  if (!result.rows.length)
    return (
      <p className="meta-hint" role="status">
        No planning intersections match this selection.
      </p>
    )
  return (
    <div
      className="planning-pivot-scroll"
      tabIndex={0}
      role="region"
      aria-label="Planning pivot results"
    >
      <table ref={table} className="data-table planning-pivot">
        <caption className="sr-only">
          Planning View from {result.plan.planningLevelName}. Values follow the configured measure
          policies.
        </caption>
        <thead>
          <tr>
            {rowFields.length ? (
              rowFields.map((f, index) => (
                <th key={f.id} scope="col" className="planning-pinned" data-pinned-column={index}>
                  {f.label}
                </th>
              ))
            ) : (
              <th scope="col" className="planning-pinned" data-pinned-column={0}>
                Rows
              </th>
            )}
            <th
              scope="col"
              className="planning-pinned planning-pinned-edge"
              data-pinned-column={dimensionCount}
            >
              Measure
            </th>
            {[...columnKeys].map(([key, values]) => (
              <th key={key} scope="col">
                {values.length ? values.map(display).join(' / ') : 'Value'}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {[...rowKeys].flatMap(([key, values]) =>
            result.measures.map((measure, measureIndex) => (
              <tr key={key + measure.measureId}>
                {measureIndex === 0 &&
                  (values.length ? (
                    values.map((v, index) => (
                      <th
                        rowSpan={result.measures.length}
                        scope="rowgroup"
                        key={index}
                        className="planning-pinned"
                        data-pinned-column={index}
                      >
                        {display(v)}
                      </th>
                    ))
                  ) : (
                    <th
                      rowSpan={result.measures.length}
                      scope="rowgroup"
                      className="planning-pinned"
                      data-pinned-column={0}
                    >
                      All
                    </th>
                  ))}
                <th
                  scope="row"
                  className="planning-measure-name planning-pinned planning-pinned-edge"
                  data-pinned-column={dimensionCount}
                  title={
                    measure.editReason ??
                    (measure.writeLevelName ? 'Writes to ' + measure.writeLevelName : undefined)
                  }
                >
                  {measure.name}
                </th>
                {[...columnKeys.values()].map((columns) => {
                  const cell = cells.get(JSON.stringify([values, columns]))
                  return (
                    <td
                      key={JSON.stringify(columns)}
                      className="planning-value"
                      title={
                        !cell
                          ? 'No planning intersection'
                          : cell[measureIndex] === null
                            ? 'NULL value'
                            : undefined
                      }
                    >
                      {!cell ? (
                        ''
                      ) : states.get(JSON.stringify([values, columns]))?.[measureIndex] ===
                        'mixed' ? (
                        <span className="meta-hint" title="This group contains different values.">
                          Mixed
                        </span>
                      ) : measure.editable && onEdit ? (
                        <PlanningEditCell
                          value={cell[measureIndex]}
                          label={
                            measure.name + ' · ' + [...values, ...columns].map(display).join(' / ')
                          }
                          disabled={busy}
                          onSave={(value) =>
                            onEdit(measure.measureId, [...values, ...columns], value)
                          }
                        />
                      ) : cell[measureIndex] === null ? (
                        '—'
                      ) : measure.valueType === 'boolean' ? (
                        ['1', 'true'].includes(cell[measureIndex]!.toLowerCase()) ? (
                          'True'
                        ) : (
                          'False'
                        )
                      ) : measure.valueType &&
                        !['number', 'integer', 'decimal'].includes(measure.valueType) ? (
                        cell[measureIndex]
                      ) : (
                        formatNumericDisplay(cell[measureIndex]!)
                      )}
                    </td>
                  )
                })}
              </tr>
            )),
          )}
        </tbody>
      </table>
    </div>
  )
}

export function PlanningViewPanel({
  base,
  revisionVersion,
  onCommitted,
  onCreateMeasure,
  creatingMeasure = false,
}: {
  base: string
  revisionVersion: number
  onCreateMeasure?: () => void
  creatingMeasure?: boolean
  onCommitted?: (version: number) => void
}) {
  const [refreshToken, refreshOptions] = useState(0)
  const [loadedScope, setLoadedScope] = useState<string | null>(null)
  const [options, setOptions] = useState<Options | null>(null)
  const [rows, setRows] = useState<Field[]>([])
  const [columns, setColumns] = useState<Field[]>([])
  const [measures, setMeasures] = useState<string[]>([])
  const [result, setResult] = useState<PlanningViewResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const editRequest = useRef<AbortController | null>(null)
  const acceptedVersion = useRef<number | null>(null)
  const activeScope = useRef(base)
  const query = useRef<AbortController | null>(null)
  useEffect(() => {
    if (!options || saving) return
    let pending: AbortController | null = null
    const focus = () => {
      pending?.abort()
      const controller = new AbortController()
      pending = controller
      void metadataApi
        .request<Options>(
          base + '/planning-view/options',
          'GET',
          undefined,
          undefined,
          controller.signal,
        )
        .then((fresh) => {
          if (!controller.signal.aborted && (fresh.buildId ?? null) !== (options.buildId ?? null)) {
            acceptedVersion.current = null
            refreshOptions((n) => n + 1)
          }
        })
        .catch(() => {
          /* Update pivot can report connectivity errors; keep the current layout. */
        })
    }
    window.addEventListener('focus', focus)
    return () => {
      pending?.abort()
      window.removeEventListener('focus', focus)
    }
  }, [base, options, saving])
  useEffect(() => () => editRequest.current?.abort(), [base])
  useEffect(() => {
    if (activeScope.current === base && acceptedVersion.current === revisionVersion) return
    activeScope.current = base
    acceptedVersion.current = null
    const controller = new AbortController()
    setOptions(null)
    setLoadedScope(null)
    setError('')
    setResult(null)
    setLoading(false)
    setSaving(false)
    void metadataApi
      .request<Options>(
        base + '/planning-view/options',
        'GET',
        undefined,
        undefined,
        controller.signal,
      )
      .then((data) => {
        if (controller.signal.aborted) return
        const saved = readViewPreferences(base, data.buildId ?? null)
        const catalog = fieldsFor(data)
        const used = new Set<string>()
        const restore = (refs: Ref[]) =>
          refs.flatMap((ref) => {
            const field = catalog.find(
              (f) =>
                f.dimensionId === ref.dimensionId &&
                f.planningLevelId === ref.planningLevelId &&
                f.columnId === ref.columnId,
            )
            if (!field || used.has(field.key)) return []
            used.add(field.key)
            return [field]
          })
        const nextRows = restore(saved?.rows ?? [])
        const nextColumns = restore(saved?.columns ?? [])
        const nextMeasures = [...new Set(saved?.measures ?? [])].filter((id) =>
          data.measures.some((m) => m.measureId === id),
        )
        setOptions(data)
        setRows(nextRows)
        setColumns(nextColumns)
        setMeasures(nextMeasures)
        setLoadedScope(base)
        if (nextMeasures.length) {
          query.current = controller
          void loadPivot(nextRows, nextColumns, nextMeasures, controller)
        }
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message)
      })
    return () => {
      controller.abort()
      query.current?.abort()
    }
  }, [base, revisionVersion, refreshToken])
  useEffect(() => {
    if (!options || loadedScope !== base) return
    saveViewPreferences(base, { buildId: options.buildId ?? null, rows, columns, measures })
  }, [base, loadedScope, options, rows, columns, measures])
  function fieldsFor(data: Options): Field[] {
    return [
      ...data.dimensions.flatMap((d) =>
        d.columns.map((c) => ({
          dimensionId: d.dimensionId,
          columnId: c.columnId,
          key: d.dimensionId + ':' + c.columnId,
          label: d.name + ' · ' + c.name,
        })),
      ),
      ...(data.planningLevels ?? []).flatMap((level) =>
        level.columns.map((column) => ({
          planningLevelId: level.planningLevelId,
          columnId: column.columnId,
          key: 'level:' + level.planningLevelId + ':' + column.columnId,
          label: level.name + ' · ' + column.name,
        })),
      ),
    ]
  }
  const fields = options ? fieldsFor(options) : []
  const selected = new Set([...rows, ...columns].map((f) => f.key))
  const available = selected.size >= 8 ? [] : fields.filter((f) => !selected.has(f.key))
  function change(action: () => void) {
    query.current?.abort()
    setLoading(false)
    setResult(null)
    setError('')
    action()
  }
  async function run() {
    query.current?.abort()
    const controller = new AbortController()
    query.current = controller
    setLoading(true)
    setError('')
    try {
      const fresh = await metadataApi.request<Options>(
        base + '/planning-view/options',
        'GET',
        undefined,
        undefined,
        controller.signal,
      )
      if (controller.signal.aborted) return
      if ((fresh.buildId ?? null) !== (options?.buildId ?? null)) {
        setResult(null)
        acceptedVersion.current = null
        refreshOptions((n) => n + 1)
        return
      }
      await loadPivot(rows, columns, measures, controller)
    } catch (e) {
      if (!controller.signal.aborted)
        setError(e instanceof Error ? e.message : 'Could not load the planning view.')
    } finally {
      if (!controller.signal.aborted) setLoading(false)
    }
  }
  async function loadPivot(
    rows: Field[],
    columns: Field[],
    measures: string[],
    controller: AbortController,
  ) {
    setLoading(true)
    setError('')
    setResult(null)
    const refs = (items: Field[]) => items.map(viewReference)
    try {
      const data = await metadataApi.request<PlanningViewResult>(
        base + '/planning-view/query',
        'POST',
        { rows: refs(rows), columns: refs(columns), measures },
        undefined,
        controller.signal,
      )
      if (!controller.signal.aborted) {
        setResult(data)
        if (data.editContext) {
          acceptedVersion.current = data.editContext.revisionVersion
          onCommitted?.(data.editContext.revisionVersion)
        }
      }
    } catch (e) {
      if (!controller.signal.aborted)
        setError(e instanceof Error ? e.message : 'Could not load the planning view.')
    } finally {
      if (!controller.signal.aborted) setLoading(false)
    }
  }
  async function editCell(
    measureId: string,
    memberValues: (string | null)[],
    value: number | null,
  ) {
    if (!result?.editContext || saving) return
    const context = result.editContext
    const view = {
      rows: result.fields.filter((f) => f.axis === 'row').map(viewReference),
      columns: result.fields.filter((f) => f.axis === 'column').map(viewReference),
      measures: result.measures.map((m) => m.measureId),
    }
    const controller = new AbortController()
    editRequest.current = controller
    setSaving(true)
    setError('')
    let applied = false
    try {
      let run = await metadataApi.request<{
        runId: string
        state: string
        error?: { message: string } | null
      }>(
        base + '/planning-view/edits',
        'POST',
        { view, measureId, memberValues, value, ...context },
        undefined,
        controller.signal,
        'edit_' + crypto.randomUUID(),
      )
      const deadline = Date.now() + 600_000
      while (run.state === 'queued' || run.state === 'running') {
        await new Promise<void>((resolve, reject) => {
          const abort = () => {
            clearTimeout(timer)
            reject(new DOMException('Cancelled', 'AbortError'))
          }
          const timer = setTimeout(() => {
            controller.signal.removeEventListener('abort', abort)
            resolve()
          }, 500)
          if (controller.signal.aborted) abort()
          else controller.signal.addEventListener('abort', abort, { once: true })
        })
        if (Date.now() > deadline)
          throw new Error(
            'The edit is still processing. Refresh after the engine run completes; do not submit it again.',
          )
        run = await metadataApi.request(
          base + '/engine/runs/' + run.runId,
          'GET',
          undefined,
          undefined,
          controller.signal,
        )
      }
      if (run.state !== 'succeeded')
        throw new Error(
          run.error?.message ?? 'The edit was not applied. Refresh the pivot and retry.',
        )
      applied = true
      const next = await metadataApi.request<PlanningViewResult>(
        base + '/planning-view/query',
        'POST',
        view,
        undefined,
        controller.signal,
      )
      if (!controller.signal.aborted) {
        setResult(next)
        if (next.editContext) {
          acceptedVersion.current = next.editContext.revisionVersion
          onCommitted?.(next.editContext.revisionVersion)
        }
      }
    } catch (error) {
      if (!controller.signal.aborted) {
        if (applied) {
          setResult(null)
          setError(
            'The edit was applied, but the pivot could not be refreshed. Click Update pivot before editing again.',
          )
        } else throw error
      }
    } finally {
      if (!controller.signal.aborted) setSaving(false)
    }
  }
  return (
    <section className="meta-card planning-view" aria-label="Planning View">
      <div className="planning-view-heading">
        <div>
          <h2>Explore your planning data</h2>
        </div>
        <div className="meta-toolbar">
          {onCreateMeasure && (
            <button
              type="button"
              className="secondary-button"
              disabled={saving || creatingMeasure || loading}
              onClick={onCreateMeasure}
            >
              {creatingMeasure ? 'Creating measure…' : 'Create Measure'}
            </button>
          )}
          <button
            type="button"
            className="secondary-button"
            disabled={saving || creatingMeasure}
            onClick={() => {
              query.current?.abort()
              setLoading(false)
              setResult(null)
              if (options) setError('')
              clearViewPreferences(base)
              setRows([])
              setColumns([])
              setMeasures([])
            }}
          >
            Clear selections
          </button>
          <button
            type="button"
            className="primary-button"
            disabled={!options || !measures.length || loading || saving || creatingMeasure}
            onClick={() => void run()}
          >
            {loading ? 'Loading pivot…' : 'Update pivot'}
          </button>
        </div>
      </div>
      {!options && !error && <p role="status">Loading dimensions and measures…</p>}
      {options && (
        <fieldset className="planning-controls" disabled={saving || creatingMeasure}>
          <div className="planning-view-selectors">
            <AxisEditor
              name="Rows"
              selected={rows}
              available={available}
              onChange={(next) => change(() => setRows(next))}
            />
            <AxisEditor
              name="Columns"
              selected={columns}
              available={available}
              onChange={(next) => change(() => setColumns(next))}
            />
            <fieldset className="planning-axis">
              <legend>
                Values
                <FieldHelp label="Values" topic="Planning View values" />
              </legend>
              <p className="meta-hint">Select up to 12 measures. Each measure appears as a row.</p>
              <div className="planning-measure-list">
                {options.measures.map((m) => (
                  <label key={m.measureId}>
                    <input
                      type="checkbox"
                      checked={measures.includes(m.measureId)}
                      disabled={!measures.includes(m.measureId) && measures.length >= 12}
                      onChange={(e) =>
                        change(() =>
                          setMeasures(
                            e.target.checked
                              ? [...measures, m.measureId]
                              : measures.filter((id) => id !== m.measureId),
                          ),
                        )
                      }
                    />
                    {m.name}
                  </label>
                ))}
              </div>
              <ol className="planning-axis-fields" aria-label="Measure order">
                {measures.map((id, index) => {
                  const name = options.measures.find((m) => m.measureId === id)?.name ?? id
                  return (
                    <li key={id}>
                      <span>{name}</span>
                      <div>
                        <button
                          type="button"
                          className="planning-field-action"
                          disabled={!index}
                          aria-label={'Move ' + name + ' up in values'}
                          onClick={() =>
                            change(() =>
                              setMeasures((current) => {
                                const next = [...current]
                                ;[next[index - 1], next[index]] = [next[index], next[index - 1]]
                                return next
                              }),
                            )
                          }
                        >
                          ↑
                        </button>
                        <button
                          type="button"
                          className="planning-field-action"
                          aria-label={'Remove ' + name + ' from values'}
                          onClick={() =>
                            change(() => setMeasures(measures.filter((m) => m !== id)))
                          }
                        >
                          ×
                        </button>
                      </div>
                    </li>
                  )
                })}
              </ol>
              {!options.measures.length && (
                <p className="planning-axis-empty">
                  Create measures, then build the Semantic Network in Materialise.
                </p>
              )}
            </fieldset>
          </div>
        </fieldset>
      )}
      {saving && (
        <p className="meta-hint" role="status">
          Applying edit and recomputing compatible planning levels…
        </p>
      )}
      {error && (
        <p className="meta-error" role="alert">
          {error}
        </p>
      )}
      {result && (
        <div className="planning-view-results">
          <div className="planning-view-source" role="status">
            <strong>Data from {result.plan.planningLevelName}</strong>
            <span>
              {result.plan.usesBase
                ? 'Base planning level'
                : result.plan.nativeOnly
                  ? 'Native planning level'
                  : 'Coarser planning level'}{' '}
              · {result.groupCount.toLocaleString()} groups
            </span>
            <FieldHelp label="Planning View source" />
          </div>
          {result.measures.some((m) => m.calculation === 'recalculated') && (
            <p className="meta-hint">
              AggregateFirst formulas are recalculated at this pivot grain.
            </p>
          )}
          {result.warnings?.map((warning) => (
            <p key={warning.measureId} className="meta-hint" role="status">
              {warning.message}
            </p>
          ))}

          <PlanningPivot
            result={result}
            onEdit={result.editContext ? editCell : undefined}
            busy={saving || creatingMeasure}
          />
        </div>
      )}
    </section>
  )
}
