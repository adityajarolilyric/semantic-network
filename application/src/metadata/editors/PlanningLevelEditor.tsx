import { usePopulationInputColumns } from './usePopulationInputColumns'
import { FilterEditor } from './FilterEditor'
import { useFilterColumns } from './useFilterColumns'
import { projectionAttributes } from '../projectionColumns'
import { FieldHelp } from '../../components/FieldHelp'
import { useEffect, useRef, useState } from 'react'
import { metadataApi } from '../api'
import { PlanningResult, type PlanningTableStatus } from '../PlanningTablePanel'
import { projectionBindings } from '../projectionBindings'
import { dimensionKeyOptions } from '../dimensionKeyOptions'
import {
  inputOf,
  type Envelope,
  type Binding,
  type GrainColumn,
  type PlanningLevel,
  type Population,
} from '../types'
import { Check, Checks, IdentityFields, options, Select, type EditorProps } from './Fields'
export function PlanningLevelEditor({
  initial,
  data,
  editing,
  onSave,
  onCancel,
  busy,
  base,
  savedVersion,
  onBuilt = () => {},
  onPersisted,
  readOnly = false,
}: EditorProps<PlanningLevel> & {
  base?: string
  savedVersion?: number
  onBuilt?: () => void
  onPersisted?: (input: PlanningLevel, version: number, close: boolean) => void
  readOnly?: boolean
}) {
  const [value, set] = useState(initial)
  const form = useRef<HTMLFormElement>(null)
  const baseline = useRef(initial)
  const currentVersion = useRef(savedVersion)
  const [tableStatus, setTableStatus] = useState<PlanningTableStatus | null>(null)
  const actionInFlight = useRef(false)
  const draftBuild = useRef<{ hash: string; runId: string } | null>(null)

  const d = value.definition
  const update = (patch: Partial<typeof d>) => set({ ...value, definition: { ...d, ...patch } })
  const p = d.population
  const cols = d.grain.columns
  const attrs = d.populationAttributes ?? []
  const [remapPopulation, setRemapPopulation] = useState(false)
  const [activeDimension, setActiveDimension] = useState(
    initial.definition.dimensionBindings[0]?.dimensionId ?? '',
  )
  const activeDimensionId = d.dimensionBindings.some((b) => b.dimensionId === activeDimension)
    ? activeDimension
    : d.dimensionBindings[0]?.dimensionId
  const [preview, setPreview] = useState<PlanningTableStatus | null>(null)
  const [previewError, setPreviewError] = useState('')
  const [previewBusy, setPreviewBusy] = useState(false)
  const [parentSchema, setParentSchema] = useState<PlanningTableStatus['columns']>([])
  const setColumns = (columns: GrainColumn[]) =>
    update({
      grain: { columns, keyIds: columns.map((c) => c.grainColumnId) },
      dimensionBindings:
        d.population.operation === 'distinct_projection'
          ? projectionBindings(parent, columns, d.dimensionBindings, data.dimensions)
          : [],
    })
  const populationInput = usePopulationInputColumns(
    p,
    base,
    p.operation === 'source'
      ? (data.sources.find((s) => s.objectId === p.sourceId)?.definition.columns ?? [])
      : [],
  )
  const sourceCols = (sourceId: string) =>
    p.operation === 'source' && p.sourceId === sourceId && populationInput.configured
      ? populationInput.columns
      : (data.sources.find((s) => s.objectId === sourceId)?.definition.columns ?? [])
  const parent =
    p.operation === 'distinct_projection'
      ? data.planningLevels.find((l) => l.objectId === p.sourcePlanningLevelId)
      : undefined
  useEffect(() => {
    setParentSchema([])
    if (!base || !parent) return
    let active = true
    void metadataApi
      .request<PlanningTableStatus>(base + '/planning-levels/' + parent.objectId + '/table')
      .then((data) => {
        if (active) setParentSchema(data.columns)
      })
      .catch(() => {})
    return () => {
      active = false
    }
  }, [base, parent?.objectId])
  const filterColumns = useFilterColumns(
    base ? base + '/planning-levels/schema' : undefined,
    { ...value, definition: { ...d, filters: null } },
    !!d.filters,
  )
  const parentAttributes = projectionAttributes(parent, parentSchema, data.dimensions)
  function populationAttributes(sourceId: string, factorId?: string) {
    const selected = attrs.filter((a) => (a.populationRef.factorId ?? undefined) === factorId)
    return (
      <Checks
        label={
          'Population attributes from ' +
          (data.sources.find((s) => s.objectId === sourceId)?.name ?? 'table')
        }
        bulk
        items={sourceCols(sourceId).map((c) => ({
          value: c.columnId,
          label: c.name,
          disabled: cols.some(
            (k) =>
              (k.populationRef.factorId ?? undefined) === factorId &&
              k.populationRef.columnId === c.columnId,
          ),
        }))}
        selected={selected.map((a) => a.populationRef.columnId!)}
        onChange={(ids) => {
          const next = [
            ...attrs.filter((a) => (a.populationRef.factorId ?? undefined) !== factorId),
            ...ids.map(
              (id) =>
                selected.find((a) => a.populationRef.columnId === id) ?? {
                  columnId: 'pa_' + crypto.randomUUID().replaceAll('-', ''),
                  name: sourceCols(sourceId).find((c) => c.columnId === id)!.name,
                  populationRef: { columnId: id, ...(factorId ? { factorId } : {}) },
                },
            ),
          ]
          update({
            populationAttributes: next,
            ...(p.operation === 'cartesian_product'
              ? {
                  population: {
                    ...p,
                    factors: p.factors.map((f) => ({
                      ...f,
                      selectedColumnIds: [
                        ...cols
                          .filter((c) => c.populationRef.factorId === f.factorId)
                          .map((c) => c.populationRef.columnId!),
                        ...next
                          .filter((a) => a.populationRef.factorId === f.factorId)
                          .map((a) => a.populationRef.columnId!),
                      ],
                    })),
                  },
                }
              : {}),
          })
        }}
      />
    )
  }
  function setBinding(binding: Binding) {
    update({
      dimensionBindings: d.dimensionBindings.some((b) => b.dimensionId === binding.dimensionId)
        ? d.dimensionBindings.map((b) => (b.dimensionId === binding.dimensionId ? binding : b))
        : [...d.dimensionBindings, binding],
    })
  }
  useEffect(() => {
    if (!base || !savedVersion) return
    const controller = new AbortController()
    void metadataApi
      .request<PlanningTableStatus>(
        base + '/planning-levels/' + initial.objectId + '/table',
        'GET',
        undefined,
        undefined,
        controller.signal,
      )
      .then((data) => {
        if (!controller.signal.aborted) setTableStatus(data)
      })
      .catch((error) => {
        if (!controller.signal.aborted) setPreviewError(error.message)
      })
    return () => controller.abort()
  }, [base, savedVersion, initial.objectId])
  const canonical = (item: PlanningLevel) =>
    JSON.stringify(inputOf(item), (_key, val) =>
      val && typeof val === 'object' && !Array.isArray(val)
        ? Object.fromEntries(
            Object.keys(val)
              .sort()
              .map((k) => [k, val[k]]),
          )
        : val,
    )
  useEffect(() => {
    setPreview(null)
  }, [value])
  async function persist(previewRequested: boolean) {
    if (actionInFlight.current || !form.current?.reportValidity()) return
    if (!base) {
      if (!previewRequested) await onSave(value)
      return
    }
    actionInFlight.current = true
    setPreviewBusy(true)
    setPreviewError('')
    setPreview(null)
    try {
      const hash = canonical(value)
      const changed = !currentVersion.current || hash !== canonical(baseline.current)
      const path = base + '/planning-levels/' + value.objectId
      if (!changed) {
        let status = await metadataApi.request<PlanningTableStatus>(path + '/table')
        const deadline = Date.now() + 180_000
        while (status.state === 'building') {
          if (Date.now() > deadline)
            throw new Error('The planning table is still building. Retry shortly.')
          await new Promise((resolve) => setTimeout(resolve, 500))
          status = await metadataApi.request<PlanningTableStatus>(path + '/table')
        }
        if (status.state === 'ready') {
          setTableStatus(status)
          if (previewRequested)
            setPreview(await metadataApi.request<PlanningTableStatus>(path + '/table?preview=true'))
          else if (onPersisted) onPersisted(baseline.current, currentVersion.current!, true)
          else onCancel()
          return
        }
      }
      if (readOnly)
        throw new Error('This configuration needs a build. Create a draft to rebuild it.')
      if (draftBuild.current?.hash !== hash) {
        const run = await metadataApi.managedOperation(
          base + '/planning-levels/editor/draft/build',
          {
            definition: inputOf(value),
            expectedVersion: currentVersion.current ?? null,
          },
        )
        draftBuild.current = { hash, runId: run.runId }
      }
      if (previewRequested) {
        const built = await metadataApi.request<PlanningTableStatus>(
          base + '/planning-levels/editor/draft/' + draftBuild.current.runId + '/preview',
        )
        setPreview(built)
        setTableStatus(built)
      } else {
        const document = await metadataApi.request<PlanningLevel & Envelope>(
          base + '/planning-levels/editor/draft/commit',
          'POST',
          { definition: inputOf(value), runId: draftBuild.current.runId },
        )
        const saved = inputOf(document)
        baseline.current = saved
        currentVersion.current = document.version
        draftBuild.current = null
        set(saved)
        if (onPersisted) onPersisted(saved, document.version, true)
        else {
          onBuilt()
          onCancel()
        }
      }
    } catch (error) {
      draftBuild.current = null
      setPreviewError(error instanceof Error ? error.message : 'Planning table operation failed.')
    } finally {
      setPreviewBusy(false)
      actionInFlight.current = false
    }
  }
  function changePopulation(population: Population) {
    setRemapPopulation(false)
    update({
      population,
      grain: { keyIds: [], columns: [] },
      populationAttributes: [],
      dimensionBindings: [],
    })
  }
  return (
    <form
      ref={form}
      onSubmit={(e) => {
        e.preventDefault()
        void persist(false)
      }}
    >
      <fieldset disabled={readOnly || busy || previewBusy}>
        <IdentityFields value={value} editing={editing} onChange={(v) => set({ ...value, ...v })} />
        <fieldset className="measure-type-control">
          <legend>
            Population method <FieldHelp label="Population method" />
          </legend>
          <div className="measure-type-switch">
            {(
              [
                ['source', 'From a Table'],
                ['cartesian_product', 'From Multiple Tables'],
                ['distinct_projection', 'From Existing Planning Level'],
              ] as const
            ).map(([operation, label]) => (
              <label className="measure-type-option" key={operation}>
                <input
                  type="radio"
                  name="population-method"
                  checked={p.operation === operation}
                  onChange={() =>
                    changePopulation(
                      operation === 'source'
                        ? { operation, sourceId: '' }
                        : operation === 'cartesian_product'
                          ? { operation, factors: [] }
                          : { operation, sourcePlanningLevelId: '' },
                    )
                  }
                />
                <span>{label}</span>
              </label>
            ))}
          </div>
        </fieldset>
        <p className="meta-hint">
          Preview builds without saving metadata. Save commits the configuration and its built
          table. An unchanged, current table is reused.
        </p>
        <p className="meta-hint">
          At least one grain key is required. Together, these keys identify a planning row and must
          be mapped by every transaction at this level.
        </p>
        {p.operation === 'source' && (
          <>
            {p.inputTransform && (
              <p className="meta-hint">
                This population applies the Pivot and Date resolution configuration captured at
                creation, then selects distinct grain keys. Choosing a different population table
                replaces that input pipeline.
              </p>
            )}
            {populationInput.error && (
              <p className="meta-error" role="alert">
                {populationInput.error}
              </p>
            )}
            <Select
              label="Population table"
              value={p.sourceId}
              items={options(data.sources.filter((s) => s.definition.inScope))}
              onChange={(sourceId) => {
                if (editing && cols.length) {
                  setRemapPopulation(true)
                  update({
                    population: { operation: 'source', sourceId },
                    grain: {
                      ...d.grain,
                      columns: cols.map((c) => ({ ...c, populationRef: { columnId: '' } })),
                    },
                    populationAttributes: attrs.map((a) => ({
                      ...a,
                      populationRef: { columnId: '' },
                    })),
                  })
                } else changePopulation({ operation: 'source', sourceId })
              }}
            />
            {remapPopulation ? (
              <div className="meta-subsection">
                <h3>Map existing fields to the new population</h3>
                <p className="meta-hint">
                  Map each field explicitly. Grain identities, Transaction mappings and Dimension
                  bindings are preserved.
                </p>
                <div className="meta-grid">
                  {cols.map((column, i) => (
                    <Select
                      key={column.grainColumnId}
                      label={'Population column for ' + column.name}
                      value={column.populationRef.columnId ?? ''}
                      items={sourceCols(p.sourceId)
                        .filter((c) => !c.nullable)
                        .map((c) => ({ value: c.columnId, label: c.name }))}
                      onChange={(columnId) =>
                        update({
                          grain: {
                            ...d.grain,
                            columns: cols.map((c, index) =>
                              index === i
                                ? {
                                    ...c,
                                    name: sourceCols(p.sourceId).find(
                                      (source) => source.columnId === columnId,
                                    )!.name,
                                    populationRef: { columnId },
                                  }
                                : c,
                            ),
                          },
                        })
                      }
                    />
                  ))}
                  {attrs.map((attribute, i) => (
                    <Select
                      key={attribute.columnId}
                      label={'Population column for attribute ' + attribute.name}
                      value={attribute.populationRef.columnId ?? ''}
                      items={sourceCols(p.sourceId).map((c) => ({
                        value: c.columnId,
                        label: c.name,
                      }))}
                      onChange={(columnId) =>
                        update({
                          populationAttributes: attrs.map((a, index) =>
                            index === i
                              ? {
                                  ...a,
                                  name: sourceCols(p.sourceId).find(
                                    (source) => source.columnId === columnId,
                                  )!.name,
                                  populationRef: { columnId },
                                }
                              : a,
                          ),
                        })
                      }
                    />
                  ))}
                </div>
              </div>
            ) : (
              <div className="meta-grid">
                <Checks
                  label="Grain columns"
                  items={sourceCols(p.sourceId).map((c) => ({
                    value: c.columnId,
                    label: c.name,
                    disabled:
                      (!populationInput.configured && c.nullable) ||
                      attrs.some(
                        (a) => !a.populationRef.factorId && a.populationRef.columnId === c.columnId,
                      ),
                  }))}
                  selected={cols.map((c) => c.populationRef.columnId!)}
                  onChange={(ids) =>
                    setColumns(
                      ids.map((id) => ({
                        grainColumnId:
                          cols.find((c) => c.populationRef.columnId === id)?.grainColumnId ?? id,
                        name: sourceCols(p.sourceId).find((c) => c.columnId === id)!.name,
                        populationRef: { columnId: id },
                      })),
                    )
                  }
                />
                {populationAttributes(p.sourceId)}
              </div>
            )}
          </>
        )}
        {p.operation === 'cartesian_product' && (
          <>
            <div className="meta-factors">
              {data.sources
                .filter((s) => s.definition.inScope)
                .map((source) => {
                  const factor = p.factors.find((f) => f.sourceId === source.objectId)
                  return (
                    <div className="meta-subsection" key={source.objectId}>
                      <Check
                        label={'Include ' + source.name}
                        value={!!factor}
                        onChange={(include) => {
                          const factors = include
                            ? [
                                ...p.factors,
                                {
                                  factorId:
                                    'f_' + crypto.randomUUID().replaceAll('-', '').slice(0, 12),
                                  sourceId: source.objectId,
                                  selectedColumnIds: [],
                                },
                              ]
                            : p.factors.filter((f) => f.sourceId !== source.objectId)
                          update({
                            population: { ...p, factors },
                            grain: {
                              columns: cols.filter(
                                (c) => c.populationRef.factorId !== factor?.factorId,
                              ),
                              keyIds: d.grain.keyIds.filter(
                                (id) =>
                                  cols.find((c) => c.grainColumnId === id)?.populationRef
                                    .factorId !== factor?.factorId,
                              ),
                            },
                            dimensionBindings: [],
                            populationAttributes: attrs.filter(
                              (a) => a.populationRef.factorId !== factor?.factorId,
                            ),
                          })
                        }}
                      />
                      {factor && (
                        <div className="meta-grid">
                          <Checks
                            label={'Keys from ' + source.name}
                            items={source.definition.columns.map((c) => ({
                              value: c.columnId,
                              label: c.name,
                              disabled:
                                c.nullable ||
                                attrs.some(
                                  (a) =>
                                    a.populationRef.factorId === factor.factorId &&
                                    a.populationRef.columnId === c.columnId,
                                ),
                            }))}
                            selected={cols
                              .filter((c) => c.populationRef.factorId === factor.factorId)
                              .map((c) => c.populationRef.columnId!)}
                            onChange={(ids) => {
                              const columns = [
                                ...cols.filter((c) => c.populationRef.factorId !== factor.factorId),
                                ...ids.map((id) => ({
                                  grainColumnId:
                                    cols.find(
                                      (c) =>
                                        c.populationRef.factorId === factor.factorId &&
                                        c.populationRef.columnId === id,
                                    )?.grainColumnId ??
                                    'g_' + crypto.randomUUID().replaceAll('-', '').slice(0, 12),
                                  name: source.definition.columns.find((c) => c.columnId === id)!
                                    .name,
                                  populationRef: { factorId: factor.factorId, columnId: id },
                                })),
                              ]
                              update({
                                population: {
                                  ...p,
                                  factors: p.factors.map((f) =>
                                    f.factorId === factor.factorId
                                      ? {
                                          ...f,
                                          selectedColumnIds: [
                                            ...ids,
                                            ...attrs
                                              .filter(
                                                (a) => a.populationRef.factorId === f.factorId,
                                              )
                                              .map((a) => a.populationRef.columnId!),
                                          ],
                                        }
                                      : f,
                                  ),
                                },
                                grain: { columns, keyIds: columns.map((c) => c.grainColumnId) },
                                dimensionBindings: [],
                              })
                            }}
                          />
                          {populationAttributes(source.objectId, factor.factorId)}
                        </div>
                      )}
                    </div>
                  )
                })}
            </div>
            <p className="meta-hint">
              For PLT, select Product–Location columns from the sourcing matrix and Date from Time.
            </p>
          </>
        )}
        {p.operation === 'distinct_projection' && (
          <>
            <Select
              label="Parent planning level"
              value={p.sourcePlanningLevelId}
              items={options(data.planningLevels.filter((l) => l.objectId !== value.objectId))}
              onChange={(sourcePlanningLevelId) =>
                changePopulation({ operation: 'distinct_projection', sourcePlanningLevelId })
              }
            />
            <div className="planning-parent-fields">
              <Checks
                label="Select keys from Parent keys"
                items={(parent?.definition.grain.columns ?? [])
                  .filter((c) => parent?.definition.grain.keyIds.includes(c.grainColumnId))
                  .map((c) => ({
                    value: c.grainColumnId,
                    label: c.name,
                    disabled: attrs.some(
                      (a) => a.populationRef.sourceOutputColumnId === c.grainColumnId,
                    ),
                  }))}
                selected={cols
                  .filter((c) => c.populationRef.sourceGrainColumnId)
                  .map((c) => c.populationRef.sourceGrainColumnId!)}
                onChange={(ids) =>
                  setColumns([
                    ...cols.filter((c) => c.populationRef.sourceOutputColumnId),
                    ...ids.map((id) => ({
                      grainColumnId:
                        cols.find((c) => c.populationRef.sourceGrainColumnId === id)
                          ?.grainColumnId ?? id,
                      name: parent!.definition.grain.columns.find((c) => c.grainColumnId === id)!
                        .name,
                      populationRef: { sourceGrainColumnId: id },
                    })),
                  ])
                }
              />
              <Checks
                label="Make Parent attribute as a grain key"
                bulk
                items={parentAttributes.map((c) => ({
                  ...c,
                  disabled: attrs.some((a) => a.populationRef.sourceOutputColumnId === c.value),
                }))}
                selected={cols
                  .filter((c) => c.populationRef.sourceOutputColumnId)
                  .map((c) => c.populationRef.sourceOutputColumnId!)}
                onChange={(ids) =>
                  setColumns([
                    ...cols.filter((c) => c.populationRef.sourceGrainColumnId),
                    ...ids.map((id) => ({
                      grainColumnId:
                        cols.find((c) => c.populationRef.sourceOutputColumnId === id)
                          ?.grainColumnId ?? 'g_' + crypto.randomUUID().replaceAll('-', ''),
                      name: parentAttributes.find((c) => c.value === id)!.label,
                      populationRef: { sourceOutputColumnId: id },
                    })),
                  ])
                }
              />
              <Checks
                label="Select attributes from Parent"
                bulk
                items={parentAttributes.map((c) => ({
                  value: c.value,
                  label: c.label,
                  disabled: cols.some(
                    (k) =>
                      (k.populationRef.sourceGrainColumnId ??
                        k.populationRef.sourceOutputColumnId) === c.value,
                  ),
                }))}
                selected={attrs.map((a) => a.populationRef.sourceOutputColumnId!)}
                onChange={(ids) =>
                  update({
                    populationAttributes: ids.map(
                      (id) =>
                        attrs.find((a) => a.populationRef.sourceOutputColumnId === id) ?? {
                          columnId: 'pa_' + crypto.randomUUID().replaceAll('-', ''),
                          name: parentAttributes.find((c) => c.value === id)!.label,
                          populationRef: { sourceOutputColumnId: id },
                        },
                    ),
                  })
                }
              />
            </div>
          </>
        )}
        {cols.length > 0 && (
          <>
            <h3>
              Dimension bindings (optional)
              <FieldHelp label="Dimension bindings (optional)" />
            </h3>
            <p className="meta-hint">
              Add a dimension only to validate member mappings or bring in dimension attributes. A
              planning table can be built without any bindings.
            </p>
            {p.operation === 'distinct_projection' && (
              <p className="meta-hint">
                Bindings are inherited when all of their mapped planning-level columns are retained.
              </p>
            )}
            <Select
              label="Add dimension"
              required={false}
              value=""
              items={options(
                data.dimensions.filter(
                  (dim) => !d.dimensionBindings.some((b) => b.dimensionId === dim.objectId),
                ),
              )}
              onChange={(dimensionId) => {
                const dim = data.dimensions.find((dim) => dim.objectId === dimensionId)
                if (!dim) return
                setBinding({
                  dimensionId,
                  lookupCardinality: 'many_to_one',
                  keyMappings: dim.definition.keyColumnIds.map((dimensionColumnId) => {
                    const candidates = dimensionKeyOptions(value, dim, dimensionColumnId, data)
                    return {
                      dimensionColumnId,
                      grainColumnId: candidates.length === 1 ? candidates[0].value : '',
                    }
                  }),
                  selectedAttributeColumnIds: [],
                })
                setActiveDimension(dimensionId)
              }}
            />
            {d.dimensionBindings.length === 0 && (
              <p className="meta-hint">
                Add a dimension to configure its lookup and selected attributes.
              </p>
            )}
            {d.dimensionBindings.length > 0 && (
              <div role="tablist" aria-label="Dimension bindings" className="meta-toolbar">
                {d.dimensionBindings.map((binding) => (
                  <button
                    type="button"
                    role="tab"
                    key={binding.dimensionId}
                    aria-selected={activeDimensionId === binding.dimensionId}
                    tabIndex={activeDimensionId === binding.dimensionId ? 0 : -1}
                    className={
                      activeDimensionId === binding.dimensionId
                        ? 'primary-button'
                        : 'secondary-button'
                    }
                    onClick={() => setActiveDimension(binding.dimensionId)}
                    onKeyDown={(event) => {
                      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
                      event.preventDefault()
                      const tabs = Array.from(
                        event.currentTarget.parentElement!.querySelectorAll<HTMLButtonElement>(
                          '[role="tab"]',
                        ),
                      )
                      const current = tabs.indexOf(event.currentTarget)
                      tabs[
                        event.key === 'Home'
                          ? 0
                          : event.key === 'End'
                            ? tabs.length - 1
                            : (current + (event.key === 'ArrowRight' ? 1 : tabs.length - 1)) %
                              tabs.length
                      ].click()
                      tabs[
                        event.key === 'Home'
                          ? 0
                          : event.key === 'End'
                            ? tabs.length - 1
                            : (current + (event.key === 'ArrowRight' ? 1 : tabs.length - 1)) %
                              tabs.length
                      ].focus()
                    }}
                  >
                    {data.dimensions.find((dim) => dim.objectId === binding.dimensionId)?.name ??
                      'Dimension'}
                  </button>
                ))}
              </div>
            )}
            {data.dimensions
              .filter((dim) => dim.objectId === activeDimensionId)
              .map((dim) => {
                const b = d.dimensionBindings.find((b) => b.dimensionId === dim.objectId)
                return (
                  <div
                    className="meta-subsection"
                    role="tabpanel"
                    aria-label={dim.name + ' binding'}
                    key={dim.objectId}
                  >
                    {b && (
                      <>
                        <div className="planning-binding-fields">
                          <div>
                            <div className="dimension-join-mappings">
                              {b.keyMappings.map((m, i) => (
                                <div className="dimension-join-row" key={i}>
                                  <Select
                                    label={`${dim.name} planning-level column ${i + 1}`}
                                    value={m.grainColumnId ?? m.populationColumnId ?? ''}
                                    items={[
                                      ...cols.map((c) => ({
                                        value: c.grainColumnId,
                                        label: c.name,
                                      })),
                                      ...attrs.map((a) => ({
                                        value: a.columnId,
                                        label: a.name + ' (attribute)',
                                      })),
                                    ]}
                                    onChange={(grainColumnId) =>
                                      setBinding({
                                        ...b,
                                        keyMappings: b.keyMappings.map((row, j) =>
                                          j === i
                                            ? {
                                                dimensionColumnId: row.dimensionColumnId,
                                                ...(cols.some(
                                                  (c) => c.grainColumnId === grainColumnId,
                                                )
                                                  ? { grainColumnId }
                                                  : { populationColumnId: grainColumnId }),
                                              }
                                            : row,
                                        ),
                                      })
                                    }
                                  />
                                  <Select
                                    label={`${dim.name} dimension column ${i + 1}`}
                                    value={m.dimensionColumnId}
                                    items={dim.definition.columns.map((c) => ({
                                      value: c.columnId,
                                      label: c.displayName,
                                    }))}
                                    onChange={(dimensionColumnId) =>
                                      setBinding({
                                        ...b,
                                        keyMappings: b.keyMappings.map((row, j) =>
                                          j === i ? { ...row, dimensionColumnId } : row,
                                        ),
                                      })
                                    }
                                  />
                                  {b.keyMappings.length > 1 && (
                                    <button
                                      type="button"
                                      className="meta-danger"
                                      aria-label={`Remove ${dim.name} mapping ${i + 1}`}
                                      onClick={() =>
                                        setBinding({
                                          ...b,
                                          keyMappings: b.keyMappings.filter((_, j) => j !== i),
                                        })
                                      }
                                    >
                                      Remove
                                    </button>
                                  )}
                                </div>
                              ))}
                            </div>
                            <div className="meta-toolbar">
                              <button
                                type="button"
                                className="text-button"
                                disabled={b.keyMappings.length >= 32}
                                onClick={() =>
                                  setBinding({
                                    ...b,
                                    keyMappings: [
                                      ...b.keyMappings,
                                      { grainColumnId: '', dimensionColumnId: '' },
                                    ],
                                  })
                                }
                              >
                                Add {dim.name} mapping
                              </button>
                            </div>
                            <p className="meta-hint">
                              Without selected attributes, the mapping only checks that the values
                              exist. Multiple dimension members may match. Selected attributes must
                              have one distinct combination for each mapped value; they do not
                              change the grain.
                            </p>
                          </div>
                          <Checks
                            label={'Attributes from ' + dim.name}
                            bulk
                            items={dim.definition.columns
                              .filter((c) => dim.definition.attributeColumnIds.includes(c.columnId))
                              .map((c) => ({ value: c.columnId, label: c.displayName }))}
                            selected={b.selectedAttributeColumnIds}
                            onChange={(selectedAttributeColumnIds) =>
                              setBinding({ ...b, selectedAttributeColumnIds })
                            }
                          />{' '}
                        </div>
                      </>
                    )}
                    <div className="meta-actions" style={{ justifyContent: 'flex-end' }}>
                      <button
                        type="button"
                        className="meta-danger"
                        onClick={() =>
                          update({
                            dimensionBindings: d.dimensionBindings.filter(
                              (binding) => binding.dimensionId !== dim.objectId,
                            ),
                          })
                        }
                      >
                        Remove {dim.name} binding
                      </button>
                    </div>
                  </div>
                )
              })}
          </>
        )}
        <FilterEditor
          value={d.filters}
          onChange={(filters) => update({ filters })}
          {...filterColumns}
        />
      </fieldset>
      {base && (
        <div className="meta-subsection" aria-label={'Planning table ' + value.name}>
          <p role="status">
            Table:{' '}
            {previewBusy
              ? 'saving / building…'
              : (tableStatus?.state.replaceAll('_', ' ') ??
                (currentVersion.current ? 'loading…' : 'not built'))}
            {tableStatus?.rowCount != null
              ? ' · ' + tableStatus.rowCount.toLocaleString() + ' rows'
              : ''}
          </p>
        </div>
      )}
      {previewError && (
        <p className="meta-error" role="alert">
          {previewError}
        </p>
      )}
      {preview && (
        <>
          <p className="meta-hint">
            {preview.state === 'preview' ? 'Built preview (metadata not saved)' : 'Built table'}:{' '}
            {preview.rowCount?.toLocaleString()} rows. Showing up to 50 records.
          </p>
          <PlanningResult
            data={preview}
            name={value.name || 'Planning-level preview'}
            showRefresh={false}
          />
        </>
      )}
      <div className="meta-actions" aria-label="Planning level actions">
        <button
          type="button"
          className="secondary-button"
          disabled={busy || previewBusy}
          onClick={onCancel}
        >
          Cancel
        </button>
        <button
          type="button"
          className="secondary-button"
          disabled={busy || previewBusy || !base || !d.grain.keyIds.length}
          onClick={() => void persist(true)}
        >
          Preview
        </button>
        <button
          type="submit"
          className="primary-button"
          disabled={busy || previewBusy || readOnly || !d.grain.keyIds.length}
        >
          Save
        </button>
      </div>
    </form>
  )
}
