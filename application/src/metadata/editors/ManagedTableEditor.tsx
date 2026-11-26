import { ViewSqlButton } from '../../components/ViewSqlButton'
import { FieldHelp } from '../../components/FieldHelp'
import { useEffect, useState } from 'react'
import { metadataApi } from '../api'
import type { AttributeValue, ManagedDefinition, ManagedTable, PivotPair, Source } from '../types'
import { Check, Checks, Select } from './Fields'
import { DataPreview } from '../../components/DataPreview'

const newId = (prefix: string) => prefix + crypto.randomUUID().replaceAll('-', '')
type Preview = {
  rowCount?: number
  columns: { name: string; columnId: string; physicalType: string }[]
  rows: (string | null)[][]
}
const inputOf = (definition: ManagedDefinition): ManagedDefinition => ({
  primaryKeyColumnIds: definition.primaryKeyColumnIds,
  pipeline: definition.pipeline,
})
export function ManagedTableEditor({
  source,
  base,
  busy,
  readOnly = false,
  onSaved,
  onClose,
}: {
  source: Source
  base: string
  busy: boolean
  readOnly?: boolean
  onSaved: () => void
  onClose: () => void
}) {
  const [selectedPairId, setSelectedPairId] = useState('')
  const [managed, setManaged] = useState(source.managed)
  const [savedDefinition, setSavedDefinition] = useState<ManagedDefinition | null>(() =>
    source.managed ? inputOf(source.managed.definition) : null,
  )
  const [definition, applyDefinition] = useState<ManagedDefinition>(() =>
    source.managed
      ? inputOf(source.managed.definition)
      : { primaryKeyColumnIds: source.definition.keyColumns, pipeline: [] },
  )
  const dirty = !savedDefinition || JSON.stringify(definition) !== JSON.stringify(savedDefinition)
  // Keep deselected choices available while editing; only selected outputs are persisted.
  const [outputChoices, setOutputChoices] = useState<Record<string, PivotPair['outputs']>>(() =>
    Object.fromEntries(
      (source.managed?.definition.pipeline[0]?.pairs ?? []).map((pair) => [
        pair.pairId,
        pair.outputs,
      ]),
    ),
  )
  const [working, setWorking] = useState(false)
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')
  const [preview, setPreview] = useState<Preview | null>(null)
  function setDefinition(next: ManagedDefinition) {
    setPreview(null)
    setError('')
    setStatus('')
    applyDefinition(next)
  }
  const stage = definition.pipeline[0]
  const activePair = stage?.pairs.find((pair) => pair.pairId === selectedPairId) ?? stage?.pairs[0]
  const path = base + '/managed-tables/' + source.objectId
  const columns = source.definition.columns.map((c) => ({ value: c.columnId, label: c.name }))
  const schemaKey = JSON.stringify({ pipeline: definition.pipeline })
  const [analysis, setAnalysis] = useState<{
    key: string
    columns: Preview['columns']
    error?: string
  } | null>(null)
  const explicitGrouping = stage?.recordKeyColumnIds != null
  const expandRows =
    stage?.mode === 'expand_rows' ||
    (stage?.mode === 'auto' && stage.resolvedMode === 'expand_rows')
  const recordKeys =
    stage?.recordKeyColumnIds ?? (expandRows ? [] : (stage?.groupByColumnIds ?? []))
  const consumed = new Set(
    (stage?.pairs ?? []).flatMap((pair) => [pair.attributeColumnId, pair.valueColumnId]),
  )
  const retainedChoices =
    expandRows || explicitGrouping ? columns.filter((c) => !consumed.has(c.value)) : columns
  const schemaEligible =
    !!stage?.enabled &&
    (explicitGrouping || expandRows || stage.groupByColumnIds.length > 0) &&
    stage.pairs.length > 0 &&
    stage.pairs.every((pair) => pair.attributeColumnId && pair.valueColumnId && pair.outputs.length)
  const [schemaAttempt, setSchemaAttempt] = useState(0)
  useEffect(() => {
    if (!schemaEligible) {
      setAnalysis(null)
      return
    }
    const controller = new AbortController()
    const timeout = setTimeout(() => {
      void metadataApi
        .request<{
          columns: Preview['columns']
        }>(
          path + '/schema',
          'POST',
          { ...JSON.parse(schemaKey), primaryKeyColumnIds: [] },
          undefined,
          controller.signal,
        )
        .then((result) => {
          if (!controller.signal.aborted) setAnalysis({ key: schemaKey, columns: result.columns })
        })
        .catch((failure) => {
          if (!controller.signal.aborted)
            setAnalysis({
              key: schemaKey,
              columns: [],
              error:
                failure instanceof Error ? failure.message : 'Could not resolve output columns.',
            })
        })
    }, 200)
    return () => {
      clearTimeout(timeout)
      controller.abort()
    }
  }, [path, schemaKey, schemaEligible, schemaAttempt])
  const schemaPending = schemaEligible && analysis?.key !== schemaKey
  const schemaError = analysis?.key === schemaKey ? analysis.error : undefined
  async function action(task: () => Promise<unknown>, message: string) {
    if (working || busy) return
    setWorking(true)
    setError('')
    setStatus(message)
    try {
      await task()
      setStatus('')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'The operation failed.')
      setStatus('')
    } finally {
      setWorking(false)
    }
  }
  function updateStage(update: Partial<NonNullable<typeof stage>>) {
    let next = { ...stage!, ...update }
    if (stage?.recordKeyColumnIds == null) {
      const { mode: _mode, resolvedMode: _resolved, ...saved } = next
      next = {
        ...saved,
        recordKeyColumnIds: update.recordKeyColumnIds ?? (expandRows ? [] : next.groupByColumnIds),
      }
    }
    const inputs = new Set(
      next.pairs.flatMap((pair) => [pair.attributeColumnId, pair.valueColumnId]),
    )
    next.groupByColumnIds = next.groupByColumnIds.filter((id) => !inputs.has(id))
    next.recordKeyColumnIds = next.recordKeyColumnIds?.filter((id) =>
      next.groupByColumnIds.includes(id),
    )
    setDefinition({ ...definition, pipeline: [next] })
  }
  function updatePair(index: number, update: Partial<PivotPair>) {
    updateStage({ pairs: stage!.pairs.map((p, i) => (i === index ? { ...p, ...update } : p)) })
  }
  async function discover(index: number, pair: PivotPair) {
    // Exhaust explicit pagination before applying Select all; never silently select page one.
    const values: AttributeValue[] = []
    let offset: number | null = 0
    do {
      const page: { values: AttributeValue[]; nextOffset: number | null } =
        await metadataApi.request(
          path +
            '/attribute-values?columnId=' +
            encodeURIComponent(pair.attributeColumnId) +
            '&limit=1000&offset=' +
            offset,
        )
      values.push(...page.values)
      offset = page.nextOffset
      if (values.length > 200)
        throw new Error('More than 200 distinct values. Choose another attribute column.')
    } while (offset !== null)
    const choices = values.map(
      (value) =>
        (outputChoices[pair.pairId] ?? pair.outputs).find(
          (o) => JSON.stringify(o.attributeValue) === JSON.stringify(value),
        ) ?? {
          attributeValue: value,
          name: String(value ?? 'Null'),
        },
    )
    setOutputChoices((current) => ({ ...current, [pair.pairId]: choices }))
    updatePair(index, { outputs: choices })
  }
  const outputColumns = stage?.enabled
    ? analysis?.key === schemaKey
      ? analysis.columns.map((column) => ({ value: column.columnId, label: column.name }))
      : retainedChoices.filter((c) => stage.groupByColumnIds.includes(c.value))
    : columns
  async function save() {
    if (readOnly) return
    setPreview(null)
    let current = managed
    if (!current) {
      await metadataApi.managedOperation(base + '/managed-tables/enable', {
        tableName: source.definition.tableName,
      })
      current = await metadataApi.request<ManagedTable>(path)
      setManaged(current)
    }
    await metadataApi.saveManaged(path, definition, current.version)
    const updated = await metadataApi.request<ManagedTable>(path)
    setManaged(updated)
    const saved = inputOf(updated.definition)
    setDefinition(saved)
    setSavedDefinition(saved)
    setOutputChoices((choices) =>
      Object.fromEntries(
        saved.pipeline.flatMap((stage) =>
          stage.pairs.map((pair) => [
            pair.pairId,
            (choices[pair.pairId] ?? pair.outputs).map(
              (choice) =>
                pair.outputs.find(
                  (output) =>
                    JSON.stringify(output.attributeValue) === JSON.stringify(choice.attributeValue),
                ) ?? choice,
            ),
          ]),
        ),
      ),
    )
    onSaved()
  }
  async function previewSaved() {
    if (dirty) {
      if (readOnly)
        throw new Error('This configuration has no saved view. Build it in a draft first.')
      if (
        !window.confirm(
          'This configuration has unsaved changes. Save and build the view before previewing?',
        )
      )
        return
      await save()
    }
    setPreview(await metadataApi.request<Preview>(path + '/preview'))
  }
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault()
        if (!readOnly) void action(save, 'Saving configuration and building view…')
      }}
    >
      <h2>{source.name}</h2>
      <p className="meta-hint">
        Raw data stays in its source table. Save builds the managed view used by semantic objects.
        Preview shows that saved view.
      </p>
      <fieldset disabled={busy || working}>
        <Check
          label="Pivot long-format columns"
          disabled={readOnly}
          value={stage?.enabled ?? false}
          onChange={(enabled) =>
            setDefinition({
              ...definition,
              pipeline: stage
                ? [{ ...stage, enabled }]
                : [
                    {
                      stageId: newId('stage_'),
                      type: 'pivot',
                      enabled,
                      recordKeyColumnIds: [],
                      groupByColumnIds: [],
                      pairs: [
                        {
                          pairId: newId('pair_'),
                          attributeColumnId: '',
                          valueColumnId: '',
                          outputs: [],
                        },
                      ],
                    },
                  ],
            })
          }
        />
        {stage?.enabled && (
          <div className="managed-pivot-layout">
            <div>
              <Checks
                label={
                  explicitGrouping || expandRows ? 'Retained columns' : 'Retain / group by columns'
                }
                disabled={readOnly}
                items={retainedChoices}
                selected={stage.groupByColumnIds.filter((id) =>
                  retainedChoices.some((c) => c.value === id),
                )}
                onChange={(groupByColumnIds) =>
                  updateStage({
                    groupByColumnIds,
                    ...(explicitGrouping
                      ? {
                          recordKeyColumnIds: recordKeys.filter((id) =>
                            groupByColumnIds.includes(id),
                          ),
                        }
                      : {}),
                  })
                }
                bulk
              />
              <Checks
                label="Record keys for separate attribute rows (optional)"
                disabled={readOnly}
                items={retainedChoices.filter(
                  (c) => stage.groupByColumnIds.includes(c.value) && !consumed.has(c.value),
                )}
                selected={recordKeys}
                onChange={(recordKeyColumnIds) =>
                  updateStage({ recordKeyColumnIds, mode: undefined, resolvedMode: undefined })
                }
              />
              <p className="meta-hint">
                {explicitGrouping
                  ? recordKeys.length
                    ? 'Rows sharing these keys are combined. Other retained columns must have one value per record.'
                    : 'Pair values and retained columns are grouped automatically. Identical records collapse.'
                  : 'Saved grouping is preserved until you edit the pivot configuration.'}
              </p>
            </div>
            <div className="managed-pivot-config">
              <div className="managed-pair-heading">
                <h3>
                  Pivot configuration <FieldHelp label="Attribute / value pair" />
                </h3>
                <button
                  type="button"
                  className="secondary-button"
                  disabled={readOnly}
                  onClick={() => {
                    const pair: PivotPair = {
                      pairId: newId('pair_'),
                      attributeColumnId: '',
                      valueColumnId: '',
                      outputs: [],
                    }
                    updateStage({ pairs: [...stage.pairs, pair] })
                    setSelectedPairId(pair.pairId)
                  }}
                >
                  Add attribute / value pair
                </button>
              </div>
              {stage.pairs.length > 0 ? (
                <div
                  className="managed-pair-tabs"
                  role="tablist"
                  aria-label="Pivot attribute/value pairs"
                >
                  {stage.pairs.map((pair, index) => (
                    <button
                      type="button"
                      role="tab"
                      key={pair.pairId}
                      id={'pair-tab-' + pair.pairId}
                      aria-controls={'pair-panel-' + pair.pairId}
                      aria-selected={activePair?.pairId === pair.pairId}
                      tabIndex={activePair?.pairId === pair.pairId ? 0 : -1}
                      onClick={() => setSelectedPairId(pair.pairId)}
                      onKeyDown={(event) => {
                        let next: number
                        if (event.key === 'ArrowRight') next = (index + 1) % stage.pairs.length
                        else if (event.key === 'ArrowLeft')
                          next = (index - 1 + stage.pairs.length) % stage.pairs.length
                        else if (event.key === 'Home') next = 0
                        else if (event.key === 'End') next = stage.pairs.length - 1
                        else return
                        event.preventDefault()
                        setSelectedPairId(stage.pairs[next].pairId)
                        event.currentTarget.parentElement
                          ?.querySelectorAll<HTMLButtonElement>('[role="tab"]')
                          [next]?.focus()
                      }}
                    >
                      <span className="managed-pair-title">Pair {index + 1}</span>
                      <span className="managed-pair-summary">
                        {columns.find((c) => c.value === pair.attributeColumnId)?.label ??
                          'Choose attribute'}{' '}
                        →{' '}
                        {columns.find((c) => c.value === pair.valueColumnId)?.label ??
                          'Choose value'}
                      </span>
                      <span className="managed-pair-count">{pair.outputs.length} outputs</span>
                    </button>
                  ))}
                </div>
              ) : (
                <p className="meta-hint">Add an attribute/value pair to configure pivot output.</p>
              )}
              {stage.pairs.map((pair, index) =>
                pair.pairId === activePair?.pairId ? (
                  <section
                    className="meta-card managed-pair-panel"
                    key={pair.pairId}
                    role="tabpanel"
                    id={'pair-panel-' + pair.pairId}
                    aria-labelledby={'pair-tab-' + pair.pairId}
                  >
                    <div className="managed-pair-inputs">
                      <Select
                        label="Attribute column"
                        disabled={readOnly}
                        items={columns}
                        value={pair.attributeColumnId}
                        onChange={(attributeColumnId) => {
                          setOutputChoices((current) => ({ ...current, [pair.pairId]: [] }))
                          updatePair(index, { attributeColumnId, outputs: [] })
                        }}
                      />
                      <Select
                        label="Value column"
                        disabled={readOnly}
                        items={columns}
                        value={pair.valueColumnId}
                        onChange={(valueColumnId) => updatePair(index, { valueColumnId })}
                      />
                      <div className="managed-pair-actions">
                        <button
                          type="button"
                          className="meta-danger"
                          disabled={readOnly}
                          onClick={() => {
                            setSelectedPairId(
                              stage.pairs[index - 1]?.pairId ??
                                stage.pairs[index + 1]?.pairId ??
                                '',
                            )
                            updateStage({ pairs: stage.pairs.filter((_, i) => i !== index) })
                          }}
                        >
                          Remove pair
                        </button>
                        <button
                          type="button"
                          className="secondary-button managed-discover"
                          disabled={readOnly || !pair.attributeColumnId}
                          onClick={() =>
                            void action(
                              () => discover(index, pair),
                              'Discovering all attribute values…',
                            )
                          }
                        >
                          Discover
                        </button>
                      </div>
                    </div>
                    <Checks
                      label="Output columns"
                      disabled={readOnly}
                      items={(outputChoices[pair.pairId] ?? pair.outputs).map((output) => ({
                        value: JSON.stringify(output.attributeValue),
                        label: output.name,
                      }))}
                      selected={pair.outputs.map((output) => JSON.stringify(output.attributeValue))}
                      onChange={(selected) =>
                        updatePair(index, {
                          outputs: (outputChoices[pair.pairId] ?? pair.outputs).filter((output) =>
                            selected.includes(JSON.stringify(output.attributeValue)),
                          ),
                        })
                      }
                      bulk
                    />
                  </section>
                ) : null,
              )}
            </div>
          </div>
        )}
        <Checks
          label="Optional managed output primary key"
          disabled={readOnly || schemaPending || !!schemaError}
          items={outputColumns}
          selected={definition.primaryKeyColumnIds}
          onChange={(primaryKeyColumnIds) => setDefinition({ ...definition, primaryKeyColumnIds })}
        />
        {schemaPending && (
          <p className="meta-hint" role="status">
            Deriving output columns…
          </p>
        )}
        {schemaError && (
          <p className="meta-error" role="alert">
            {schemaError}
            <button
              type="button"
              className="secondary-button"
              onClick={() => {
                setAnalysis(null)
                setSchemaAttempt((attempt) => attempt + 1)
              }}
            >
              Retry output columns
            </button>
          </p>
        )}
        {(!stage?.enabled || (schemaEligible && !schemaPending && !schemaError)) &&
          definition.primaryKeyColumnIds.some(
            (id) => !outputColumns.some((c) => c.value === id),
          ) && (
            <p role="alert">
              <button
                type="button"
                disabled={readOnly}
                onClick={() => setDefinition({ ...definition, primaryKeyColumnIds: [] })}
              >
                Clear primary key
              </button>
              A declared key is absent from these outputs. Restore the column or explicitly clear
              the key.
            </p>
          )}
      </fieldset>
      <div className="meta-actions">
        <button
          type="submit"
          className="primary-button"
          disabled={
            busy ||
            working ||
            readOnly ||
            (!!stage?.enabled && (!schemaEligible || schemaPending || !!schemaError))
          }
        >
          Save
        </button>
        <button
          type="button"
          className="secondary-button"
          disabled={
            busy ||
            working ||
            (dirty && !!stage?.enabled && (!schemaEligible || schemaPending || !!schemaError))
          }
          onClick={() => void action(previewSaved, 'Loading saved view…')}
        >
          Preview
        </button>
        <ViewSqlButton
          disabled={
            busy ||
            working ||
            (!!stage?.enabled && (!schemaEligible || schemaPending || !!schemaError))
          }
          loadSql={(signal) =>
            metadataApi.request(
              path + '/preview?sql_only=true',
              'POST',
              definition,
              undefined,
              signal,
            )
          }
        />
        <button
          type="button"
          className="secondary-button"
          disabled={busy || working}
          onClick={() => {
            if (
              !dirty ||
              readOnly ||
              window.confirm('Discard unsaved changes and return to Managed Tables?')
            )
              onClose()
          }}
        >
          Close
        </button>
      </div>
      <p role="status">{status || (dirty ? 'Unsaved changes.' : 'Configuration saved.')}</p>
      {error && (
        <p className="meta-error" role="alert">
          {error}
        </p>
      )}
      {preview && (
        <DataPreview
          table={source.name}
          sampleOnly
          showRefresh={false}
          preview={{
            table: source.name,
            columns: preview.columns.map((column) => ({
              name: column.name,
              type: column.physicalType,
            })),
            rows: preview.rows,
            total: preview.rowCount ?? preview.rows.length,
            offset: 0,
            limit: 50,
          }}
          loading={false}
          error=""
          offset={0}
          limit={50}
          onOffset={() => {}}
          onLimit={() => {}}
          onRefresh={() => {}}
        />
      )}
    </form>
  )
}
