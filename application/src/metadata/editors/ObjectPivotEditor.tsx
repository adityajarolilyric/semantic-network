import { useEffect, useRef, useState } from 'react'
import { metadataApi } from '../api'
import type { AttributeValue, PivotPair, PivotStage, Source, SourceColumn } from '../types'
import { Check, Checks, Select } from './Fields'
import { PlanningResult, type PlanningTableStatus } from '../PlanningTablePanel'
const id = (prefix: string) => prefix + crypto.randomUUID().replaceAll('-', '')
const pair = (): PivotPair => ({
  pairId: id('pair_'),
  attributeColumnId: '',
  valueColumnId: '',
  outputs: [],
})
export function ObjectPivotEditor({
  objectId,
  source,
  base,
  kind,
  value,
  onChange,
  onColumns,
  disabled = false,
}: {
  objectId: string
  source?: Source
  base?: string
  kind: 'dimensions' | 'transactions'
  value?: PivotStage | null
  onChange: (value: PivotStage | null) => void
  onColumns: (columns: SourceColumn[] | null) => void
  disabled?: boolean
}) {
  const [active, setActive] = useState('')
  const [choices, setChoices] = useState<Record<string, PivotPair['outputs']>>({})
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [preview, setPreview] = useState<PlanningTableStatus | null>(null)
  const previousStage = useRef(value)
  if (value) previousStage.current = value
  const pending = useRef<AbortController | null>(null)
  useEffect(() => () => pending.current?.abort(), [])
  const latest = useRef(onColumns)
  latest.current = onColumns
  const signature = JSON.stringify(value ?? null)
  const inputSignature = JSON.stringify(source?.definition.columns ?? [])
  const complete =
    !!value?.enabled &&
    !!value.pairs.length &&
    value.pairs.every((p) => p.attributeColumnId && p.valueColumnId && p.outputs.length)
  useEffect(() => {
    setError('')
    setPreview(null)
    if (!value?.enabled) {
      latest.current(source?.definition.columns ?? [])
      return
    }
    latest.current(null)
    if (!complete || !base || !source) return
    const controller = new AbortController()
    const timer = setTimeout(() => {
      void metadataApi
        .request<{ columns: SourceColumn[] }>(
          base + '/' + kind + '/input/schema',
          'POST',
          { objectId, sourceId: source.objectId, pivot: JSON.parse(signature) },
          undefined,
          controller.signal,
        )
        .then((r) => {
          if (!controller.signal.aborted) latest.current(r.columns)
        })
        .catch((e) => {
          if (!controller.signal.aborted) setError(e.message)
        })
    }, 200)
    return () => {
      clearTimeout(timer)
      controller.abort()
    }
    // Schema depends on the serialized input and pivot, not the parent's render identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [base, kind, objectId, source?.objectId, inputSignature, signature, complete])
  const selected = value?.pairs.find((p) => p.pairId === active) ?? value?.pairs[0]
  const consumed = new Set(
    value?.pairs.flatMap((p) => [p.attributeColumnId, p.valueColumnId]) ?? [],
  )
  const columns = (source?.definition.columns ?? []).map((c) => ({
    value: c.columnId,
    label: c.name,
  }))
  const retained = columns.filter((c) => !consumed.has(c.value))
  function update(patch: Partial<PivotStage>) {
    if (!value) return
    const next = { ...value, ...patch, mode: undefined, resolvedMode: undefined }
    const consumed = new Set(next.pairs.flatMap((p) => [p.attributeColumnId, p.valueColumnId]))
    next.groupByColumnIds = next.groupByColumnIds.filter((cid) => !consumed.has(cid))
    next.recordKeyColumnIds = (next.recordKeyColumnIds ?? []).filter((cid) =>
      next.groupByColumnIds.includes(cid),
    )
    onChange(next)
  }
  function updatePair(p: PivotPair, patch: Partial<PivotPair>) {
    update({ pairs: value!.pairs.map((x) => (x.pairId === p.pairId ? { ...x, ...patch } : x)) })
  }
  async function discover(p: PivotPair) {
    if (!base || !source) return
    pending.current?.abort()
    const controller = new AbortController()
    pending.current = controller
    setBusy(true)
    setError('')
    try {
      const values: AttributeValue[] = []
      let offset: number | null = 0
      do {
        const page: { values: AttributeValue[]; nextOffset: number | null } =
          await metadataApi.request(
            base +
              '/managed-tables/' +
              encodeURIComponent(source.objectId) +
              '/attribute-values?managedOutput=true&columnId=' +
              encodeURIComponent(p.attributeColumnId) +
              '&limit=1000&offset=' +
              offset,
            'GET',
            undefined,
            undefined,
            controller.signal,
          )
        if (controller.signal.aborted) return
        values.push(...page.values)
        offset = page.nextOffset
        if (values.length > 200)
          throw new Error('More than 200 attribute values. Choose another column.')
      } while (offset !== null)
      const outputs = values.map(
        (attributeValue) =>
          (choices[p.pairId] ?? p.outputs).find(
            (o) => JSON.stringify(o.attributeValue) === JSON.stringify(attributeValue),
          ) ?? {
            attributeValue,
            name: (String(attributeValue ?? 'Null').trim() || 'Blank').slice(0, 180),
          },
      )
      const copiedOutputs = outputs.map((output) => ({ ...output }))
      const usedNames = new Set([
        ...columns
          .filter((c) => value?.groupByColumnIds.includes(c.value) && !consumed.has(c.value))
          .map((c) => c.label),
        ...(value?.pairs
          .filter((x) => x.pairId !== p.pairId)
          .flatMap((x) => x.outputs.map((o) => o.name)) ?? []),
      ])
      for (const output of copiedOutputs) {
        if (usedNames.has(output.name)) {
          const prefix = columns.find((c) => c.value === p.attributeColumnId)?.label ?? 'attribute'
          const base = (prefix + '_' + output.name).slice(0, 185)
          output.name = base
          let suffix = 2
          while (usedNames.has(output.name)) output.name = base + '_' + suffix++
        }
        usedNames.add(output.name)
      }
      setChoices((c) => ({ ...c, [p.pairId]: copiedOutputs }))
      updatePair(p, { outputs: copiedOutputs })
    } catch (e) {
      if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Discovery failed.')
    } finally {
      setBusy(false)
    }
  }
  return (
    <fieldset className="meta-subsection" disabled={disabled || busy}>
      <Check
        label="Pivot long-format columns"
        value={!!value?.enabled}
        disabled={!source}
        onChange={(enabled) =>
          onChange(
            enabled
              ? {
                  ...(previousStage.current ?? {
                    stageId: id('stage_'),
                    type: 'pivot' as const,
                    recordKeyColumnIds: [],
                    groupByColumnIds: source?.definition.columns.map((c) => c.columnId) ?? [],
                    pairs: [pair()],
                  }),
                  enabled: true,
                }
              : null,
          )
        }
      />
      {value?.enabled && (
        <>
          <p className="meta-hint">
            This pivot belongs only to this {kind === 'dimensions' ? 'Dimension' : 'Transaction'}.
            Other objects using this Managed Table keep their own configuration.
          </p>
          <div className="managed-pivot-layout">
            <div>
              <Checks
                label="Retained columns"
                selected={value.groupByColumnIds.filter((cid) =>
                  retained.some((c) => c.value === cid),
                )}
                items={retained}
                bulk
                onChange={(groupByColumnIds) => update({ groupByColumnIds })}
              />
              <Checks
                label="Record keys for separate attribute rows (optional)"
                selected={value.recordKeyColumnIds ?? value.groupByColumnIds}
                items={retained.filter((c) => value.groupByColumnIds.includes(c.value))}
                onChange={(recordKeyColumnIds) => update({ recordKeyColumnIds })}
              />
              <p className="meta-hint">
                Leave record keys empty for attribute/value pairs on the same row. Select record
                keys only when separate rows describe one record.
              </p>
            </div>
            <div className="managed-pivot-config">
              <div className="managed-pair-heading">
                <h3>Pivot configuration</h3>
                <button
                  type="button"
                  className="secondary-button"
                  disabled={value.pairs.length >= 20}
                  onClick={() => {
                    const p = pair()
                    update({ pairs: [...value.pairs, p] })
                    setActive(p.pairId)
                  }}
                >
                  Add attribute / value pair
                </button>
              </div>
              <div className="managed-pair-tabs" role="tablist" aria-label="Pivot pairs">
                {value.pairs.map((p, i) => (
                  <button
                    type="button"
                    role="tab"
                    key={p.pairId}
                    aria-selected={selected?.pairId === p.pairId}
                    onClick={() => setActive(p.pairId)}
                  >
                    <span className="managed-pair-title">Pair {i + 1}</span>
                    <span className="managed-pair-summary">
                      {columns.find((c) => c.value === p.attributeColumnId)?.label ??
                        'Choose attribute'}{' '}
                      → {columns.find((c) => c.value === p.valueColumnId)?.label ?? 'Choose value'}
                    </span>
                    <span className="managed-pair-count">{p.outputs.length} outputs</span>
                  </button>
                ))}
              </div>
              {selected && (
                <div className="meta-card managed-pair-panel">
                  <div className="managed-pair-inputs">
                    <Select
                      label="Attribute column"
                      value={selected.attributeColumnId}
                      items={columns}
                      onChange={(attributeColumnId) => {
                        setChoices((c) => ({ ...c, [selected.pairId]: [] }))
                        updatePair(selected, { attributeColumnId, outputs: [] })
                      }}
                    />
                    <Select
                      label="Value column"
                      value={selected.valueColumnId}
                      items={columns}
                      onChange={(valueColumnId) => updatePair(selected, { valueColumnId })}
                    />
                    <div className="managed-pair-actions">
                      <button
                        type="button"
                        className="meta-danger"
                        onClick={() =>
                          update({ pairs: value.pairs.filter((p) => p.pairId !== selected.pairId) })
                        }
                      >
                        Remove pair
                      </button>
                      <button
                        type="button"
                        className="secondary-button"
                        disabled={!selected.attributeColumnId || !selected.valueColumnId}
                        onClick={() => void discover(selected)}
                      >
                        Discover
                      </button>
                    </div>
                  </div>
                  <Checks
                    label="Output columns"
                    bulk
                    selected={selected.outputs.map((o) => JSON.stringify(o.attributeValue))}
                    items={(choices[selected.pairId] ?? selected.outputs).map((o) => ({
                      value: JSON.stringify(o.attributeValue),
                      label: o.name,
                    }))}
                    onChange={(keys) =>
                      updatePair(selected, {
                        outputs: (choices[selected.pairId] ?? selected.outputs).filter((o) =>
                          keys.includes(JSON.stringify(o.attributeValue)),
                        ),
                      })
                    }
                  />
                </div>
              )}
            </div>
          </div>
          {!complete && (
            <p className="meta-hint">
              Choose the attribute/value columns and Discover output columns to complete this pivot.
            </p>
          )}
          <button
            type="button"
            className="secondary-button"
            disabled={!complete || !source || !base}
            onClick={async () => {
              setBusy(true)
              setPreview(null)
              setError('')
              try {
                setPreview(
                  await metadataApi.request(base + '/' + kind + '/input/preview', 'POST', {
                    objectId,
                    sourceId: source!.objectId,
                    pivot: value,
                  }),
                )
              } catch (e) {
                setError(e instanceof Error ? e.message : 'Pivot preview failed.')
              } finally {
                setBusy(false)
              }
            }}
          >
            Preview pivot output
          </button>
        </>
      )}
      {error && (
        <p className="meta-error" role="alert">
          {error}
        </p>
      )}
      {preview && <PlanningResult data={preview} name="Pivot output" showRefresh={false} />}
    </fieldset>
  )
}
