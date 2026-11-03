import { formatNumericDisplay } from '../lib/numberFormat'
import { useEffect, useRef, useState } from 'react'
import { FieldHelp } from '../components/FieldHelp'
import { PlanningEditCell } from './PlanningEditCell'
import { metadataApi } from './api'

type Ref = { dimensionId: string; columnId: string }
type Field = Ref & { key: string; label: string }
type Options = {
  dimensions: { dimensionId: string; name: string; columns: { columnId: string; name: string }[] }[]
  measures: { measureId: string; name: string }[]
}
export type PlanningViewResult = {
  fields: (Ref & { id: string; label: string; axis: 'row' | 'column' })[]
  measures: {
    measureId: string
    name: string
    calculation: string
    editable?: boolean
    editReason?: string | null
    writeLevelName?: string
  }[]
  editContext?: { generationId: string; revisionVersion: number }
  warnings?: { measureId: string; message: string }[]
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
          <option value="">Choose dimension attribute…</option>
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
  const rowKeys = new Map<string, (string | null)[]>()
  const columnKeys = new Map<string, (string | null)[]>()
  const cells = new Map<string, (string | null)[]>()
  for (const record of result.rows) {
    const rows = record.slice(0, rowFields.length)
    const columns = record.slice(rowFields.length, rowFields.length + columnFields.length)
    rowKeys.set(JSON.stringify(rows), rows)
    columnKeys.set(JSON.stringify(columns), columns)
    cells.set(JSON.stringify([rows, columns]), record.slice(rowFields.length + columnFields.length))
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
      <table className="data-table planning-pivot">
        <caption className="sr-only">
          Planning View from {result.plan.planningLevelName}. Values follow the configured measure
          policies.
        </caption>
        <thead>
          <tr>
            {rowFields.length ? (
              rowFields.map((f) => (
                <th key={f.id} scope="col">
                  {f.label}
                </th>
              ))
            ) : (
              <th scope="col">Rows</th>
            )}
            <th scope="col">Measure</th>
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
                      <th rowSpan={result.measures.length} scope="rowgroup" key={index}>
                        {display(v)}
                      </th>
                    ))
                  ) : (
                    <th rowSpan={result.measures.length} scope="rowgroup">
                      All
                    </th>
                  ))}
                <th
                  scope="row"
                  className="planning-measure-name"
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
}: {
  base: string
  revisionVersion: number
  onCommitted?: (version: number) => void
}) {
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
  useEffect(() => () => editRequest.current?.abort(), [base])
  useEffect(() => {
    if (activeScope.current === base && acceptedVersion.current === revisionVersion) return
    activeScope.current = base
    acceptedVersion.current = null
    const controller = new AbortController()
    setOptions(null)
    setError('')
    setResult(null)
    setRows([])
    setColumns([])
    setMeasures([])
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
        if (!controller.signal.aborted) setOptions(data)
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message)
      })
    return () => {
      controller.abort()
      query.current?.abort()
    }
  }, [base, revisionVersion])
  const fields: Field[] =
    options?.dimensions.flatMap((d) =>
      d.columns.map((c) => ({
        dimensionId: d.dimensionId,
        columnId: c.columnId,
        key: d.dimensionId + ':' + c.columnId,
        label: d.name + ' · ' + c.name,
      })),
    ) ?? []
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
    setResult(null)
    const refs = (items: Field[]) =>
      items.map(({ dimensionId, columnId }) => ({ dimensionId, columnId }))
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
      rows: result.fields
        .filter((f) => f.axis === 'row')
        .map(({ dimensionId, columnId }) => ({ dimensionId, columnId })),
      columns: result.fields
        .filter((f) => f.axis === 'column')
        .map(({ dimensionId, columnId }) => ({ dimensionId, columnId })),
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
          <p className="meta-hint">
            Choose row and column attributes, then add measures. Values use the saved semantic
            policies.
          </p>
        </div>
        <button
          type="button"
          className="primary-button"
          disabled={!options || !measures.length || loading || saving}
          onClick={() => void run()}
        >
          {loading ? 'Loading pivot…' : 'Update pivot'}
        </button>
      </div>
      {!options && !error && <p role="status">Loading dimensions and measures…</p>}
      {options && (
        <fieldset className="planning-controls" disabled={saving}>
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
          <p className="meta-hint">
            {result.plan.explanation}
            {result.measures.some((m) => m.calculation === 'recalculated')
              ? ' AggregateFirst formulas are recalculated at this pivot grain.'
              : ''}
          </p>
          {result.warnings?.map((warning) => (
            <p key={warning.measureId} className="meta-hint" role="status">
              {warning.message}
            </p>
          ))}
          {columns.length > 0 && (
            <p className="planning-column-label">
              Columns: {columns.map((f) => f.label).join(' / ')}
            </p>
          )}
          <p className="meta-hint">
            Editable values show a pencil. Edits use configured disaggregation and aggregation
            policies. Build Semantic Network replaces manual edits from source data.
          </p>
          <PlanningPivot
            result={result}
            onEdit={result.editContext ? editCell : undefined}
            busy={saving}
          />
        </div>
      )}
    </section>
  )
}
