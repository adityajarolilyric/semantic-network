import { ViewSqlButton } from '../../components/ViewSqlButton'
import { useDimensionCatalog } from '../useDimensionCatalog'
import { usePopulationInputColumns } from './usePopulationInputColumns'
import { FilterEditor } from './FilterEditor'
import { TransformationsPanel } from './TransformationsPanel'
import { useFilterColumns } from './useFilterColumns'
import { projectionAttributes } from '../projectionColumns'
import { FieldHelp } from '../../components/FieldHelp'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { metadataApi } from '../api'
import { PlanningResult, type DimensionTableStatus } from '../DimensionTablePanel'
import { projectionBindings } from '../projectionBindings'
import { dimensionKeyOptions } from '../dimensionKeyOptions'
import { PopulationRemapping, type PopulationMappingOption } from './PopulationRemapping'
import { inputOf, type Binding, type GrainColumn, type Dimension, type Population } from '../types'
import { Check, Checks, IdentityFields, Text, options, Select, type EditorProps } from './Fields'
export function DimensionEditor({
  initial,
  data,
  editing,
  onSave,
  onCancel,
  busy,
  base,
  savedVersion,
  readOnly = false,
  embedded = false,
  fixedSource = false,
  inputColumns,
  onDraftChange,
}: EditorProps<Dimension> & {
  base?: string
  savedVersion?: number
  onBuilt?: () => void
  onPersisted?: (input: Dimension, version: number, close: boolean) => void
  readOnly?: boolean
  fixedSource?: boolean
  inputColumns?: import('../types').SourceColumn[]
  embedded?: boolean
  onDraftChange?: (value: Dimension) => void
}) {
  const dimensionMembers = useDimensionCatalog(data, base)
  const [value, set] = useState(initial)
  useEffect(() => {
    onDraftChange?.(value)
  }, [value, onDraftChange])
  const form = useRef<HTMLFormElement>(null)
  const [tableStatus, setTableStatus] = useState<DimensionTableStatus | null>(null)
  const actionInFlight = useRef(false)

  const d = value.definition
  const update = (patch: Partial<typeof d>) => set({ ...value, definition: { ...d, ...patch } })
  const p = d.population
  const savedDimension =
    data.dimensions.find((dimension) => dimension.objectId === initial.objectId) ?? initial
  function savedSourceKey(sourceId: string, columnId: string) {
    const population = savedDimension.definition.population
    return savedDimension.definition.grain.columns.find((column) => {
      const source =
        population.operation === 'source'
          ? population.sourceId
          : population.operation === 'cartesian_product'
            ? population.factors.find((factor) => factor.factorId === column.populationRef.factorId)
                ?.sourceId
            : undefined
      return source === sourceId && column.populationRef.columnId === columnId
    })?.grainColumnId
  }
  const cols = d.grain.columns
  const attrs = d.populationAttributes ?? []
  const [remapPopulation, setRemapPopulation] = useState(
    () =>
      editing &&
      initial.definition.grain.columns.some(
        (column) =>
          !column.populationRef.columnId &&
          !column.populationRef.sourceGrainColumnId &&
          !column.populationRef.sourceOutputColumnId,
      ),
  )
  const [freshPopulation, setFreshPopulation] = useState(
    initial.definition.grain.columns.length === 0,
  )
  const [activeDimension, setActiveDimension] = useState(
    initial.definition.memberBindings[0]?.memberCatalogId ?? '',
  )
  const activeDimensionId = d.memberBindings.some((b) => b.memberCatalogId === activeDimension)
    ? activeDimension
    : d.memberBindings[0]?.memberCatalogId
  const [preview, setPreview] = useState<DimensionTableStatus | null>(null)
  const [previewError, setPreviewError] = useState('')
  const [previewBusy, setPreviewBusy] = useState(false)
  const [parentSchema, setParentSchema] = useState<DimensionTableStatus['columns']>([])
  const [parentSchemaError, setParentSchemaError] = useState('')
  const [parentSchemaLoading, setParentSchemaLoading] = useState(false)
  const setColumns = (columns: GrainColumn[]) =>
    update({
      grain: { columns, keyIds: columns.map((c) => c.grainColumnId) },
      memberBindings:
        d.population.operation === 'distinct_projection'
          ? projectionBindings(parent, columns, d.memberBindings, data.dimensionMembers)
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
    fixedSource && inputColumns
      ? inputColumns
      : p.operation === 'source' && p.sourceId === sourceId && populationInput.configured
        ? populationInput.columns
        : (data.sources.find((s) => s.objectId === sourceId)?.definition.columns ?? [])
  const parent =
    p.operation === 'distinct_projection'
      ? dimensionMembers.levels.find((l) => l.objectId === p.sourceDimensionId)
      : undefined
  const parentSignature = parent ? JSON.stringify(inputOf(parent)) : ''
  useEffect(() => {
    setParentSchema([])
    setParentSchemaError('')
    setParentSchemaLoading(false)
    if (!base || !parentSignature) return
    const controller = new AbortController()
    setParentSchemaLoading(true)
    void metadataApi
      .request<Pick<DimensionTableStatus, 'columns'>>(
        base + '/dimensions/schema',
        'POST',
        JSON.parse(parentSignature),
        undefined,
        controller.signal,
      )
      .then((data) => {
        if (!controller.signal.aborted) setParentSchema(data.columns)
      })
      .catch((error) => {
        if (!controller.signal.aborted)
          setParentSchemaError(
            error instanceof Error ? error.message : 'Could not load parent attributes.',
          )
      })
      .finally(() => {
        if (!controller.signal.aborted) setParentSchemaLoading(false)
      })
    return () => {
      controller.abort()
    }
  }, [base, parentSignature])
  const filterColumns = useFilterColumns(
    base ? base + '/dimensions/schema' : undefined,
    { ...value, definition: { ...d, filters: null } },
    !!d.filters,
  )
  const parentAttributes = projectionAttributes(parent, parentSchema, data.dimensionMembers)
  function populationAttributes(sourceId: string, factorId?: string) {
    const selected = attrs.filter((a) => (a.populationRef.factorId ?? undefined) === factorId)
    const selection = (
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
    return embedded ? (
      <details className="population-details">
        <summary>
          Additional attributes <span className="meta-hint">{selected.length} selected</span>
        </summary>
        {selection}
      </details>
    ) : (
      selection
    )
  }
  function setBinding(binding: Binding) {
    update({
      memberBindings: d.memberBindings.some((b) => b.memberCatalogId === binding.memberCatalogId)
        ? d.memberBindings.map((b) => (b.memberCatalogId === binding.memberCatalogId ? binding : b))
        : [...d.memberBindings, binding],
    })
  }
  useEffect(() => {
    if (!base || !savedVersion) return
    const controller = new AbortController()
    void metadataApi
      .request<DimensionTableStatus>(
        base + '/dimensions/' + initial.objectId + '/table',
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
  useEffect(() => {
    setPreview(null)
  }, [value])
  async function persist(previewRequested: boolean) {
    if (actionInFlight.current || !form.current?.reportValidity()) return
    if (!previewRequested) {
      await onSave(value)
      return
    }
    if (!base) return
    actionInFlight.current = true
    setPreviewBusy(true)
    setPreviewError('')
    try {
      setPreview(
        await metadataApi.request<DimensionTableStatus>(
          base + '/dimensions/preview/population',
          'POST',
          inputOf(value),
        ),
      )
    } catch (error) {
      setPreviewError(error instanceof Error ? error.message : 'Dimension preview failed.')
    } finally {
      actionInFlight.current = false
      setPreviewBusy(false)
    }
  }
  function changePopulation(population: Population) {
    setRemapPopulation(false)
    setFreshPopulation(true)
    update({
      population,
      grain: { keyIds: [], columns: [] },
      populationAttributes: [],
      memberBindings: [],
      filters: null,
    })
  }
  const mappingOptions: PopulationMappingOption[] =
    p.operation === 'source'
      ? sourceCols(p.sourceId).map((column) => ({
          label: column.name,
          nullable: column.nullable,
          reference: { columnId: column.columnId },
        }))
      : p.operation === 'cartesian_product'
        ? p.factors.flatMap((factor) =>
            sourceCols(factor.sourceId).map((column) => ({
              label:
                (data.sources.find((source) => source.objectId === factor.sourceId)?.name ?? '') +
                ' · ' +
                column.name,
              nullable: column.nullable,
              reference: { factorId: factor.factorId, columnId: column.columnId },
            })),
          )
        : [
            ...(parent?.definition.grain.columns ?? []).map((column) => ({
              label: column.name,
              reference: { sourceGrainColumnId: column.grainColumnId },
            })),
            ...parentAttributes.map((column) => ({
              label: column.label,
              reference: { sourceOutputColumnId: column.value },
            })),
          ]
  const Container = embedded ? 'div' : 'form'
  return (
    <Container
      ref={(node: HTMLDivElement | HTMLFormElement | null) => {
        if (!embedded) form.current = node as HTMLFormElement | null
      }}
      onSubmit={(e) => {
        if (embedded) return
        e.preventDefault()
        void persist(false)
      }}
    >
      <fieldset disabled={readOnly || busy || previewBusy}>
        {!embedded && (
          <IdentityFields
            value={value}
            editing={editing}
            onChange={(v) => set({ ...value, ...v })}
          />
        )}
        {embedded && (
          <Text
            label="Population name"
            value={value.name}
            onChange={(name) => set({ ...value, name, displayName: name })}
          />
        )}
        {!fixedSource && (
          <fieldset className="measure-type-control">
            <legend>
              Population method <FieldHelp label="Population method" />
            </legend>
            <div className="measure-type-switch">
              {(
                [
                  ['source', embedded ? 'From a table' : 'From a Table'],
                  ['cartesian_product', embedded ? 'Combine tables' : 'From Multiple Tables'],
                  [
                    'distinct_projection',
                    embedded ? 'From an existing population' : 'From Existing Dimension',
                  ],
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
                            : { operation, sourceDimensionId: '' },
                      )
                    }
                  />
                  <span>{label}</span>
                </label>
              ))}
            </div>
          </fieldset>
        )}
        {!embedded && (
          <p className="meta-hint">
            {p.operation === 'source'
              ? 'Save stores the Dimension definition. Build Semantic Network in Decision View materialises its table.'
              : 'Save stores the Dimension definition and builds its table, including required parent tables.'}{' '}
            Preview reads its population without creating a table.
          </p>
        )}
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
            {!fixedSource && (
              <Select
                label="Population table"
                value={p.sourceId}
                items={options(data.sources.filter((s) => s.definition.inScope))}
                onChange={(sourceId) => {
                  if (editing && !freshPopulation && cols.length) {
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
            )}
            {!remapPopulation && (
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
                          cols.find((c) => c.populationRef.columnId === id)?.grainColumnId ??
                          savedSourceKey(p.sourceId, id) ??
                          id,
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
            {embedded && (
              <Select
                label="Add population table"
                required={false}
                value=""
                items={options(
                  data.sources.filter(
                    (source) =>
                      source.definition.inScope &&
                      !p.factors.some((factor) => factor.sourceId === source.objectId),
                  ),
                )}
                onChange={(sourceId) => {
                  if (!sourceId) return
                  update({
                    population: {
                      ...p,
                      factors: [
                        ...p.factors,
                        {
                          factorId: 'f_' + crypto.randomUUID().replaceAll('-', '').slice(0, 12),
                          sourceId,
                          selectedColumnIds: [],
                        },
                      ],
                    },
                  })
                }}
              />
            )}
            <div className="meta-factors">
              {data.sources
                .filter(
                  (s) =>
                    s.definition.inScope &&
                    (!embedded || p.factors.some((factor) => factor.sourceId === s.objectId)),
                )
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
                            grain: remapPopulation
                              ? {
                                  ...d.grain,
                                  columns: cols.map((column) =>
                                    !include && column.populationRef.factorId === factor?.factorId
                                      ? { ...column, populationRef: {} }
                                      : column,
                                  ),
                                }
                              : {
                                  columns: cols.filter(
                                    (column) => column.populationRef.factorId !== factor?.factorId,
                                  ),
                                  keyIds: d.grain.keyIds.filter(
                                    (id) =>
                                      cols.find((column) => column.grainColumnId === id)
                                        ?.populationRef.factorId !== factor?.factorId,
                                  ),
                                },
                            memberBindings: remapPopulation ? d.memberBindings : [],
                            populationAttributes: remapPopulation
                              ? attrs.map((column) =>
                                  !include && column.populationRef.factorId === factor?.factorId
                                    ? { ...column, populationRef: {} }
                                    : column,
                                )
                              : attrs.filter(
                                  (column) => column.populationRef.factorId !== factor?.factorId,
                                ),
                          })
                        }}
                      />
                      {factor && !remapPopulation && (
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
                                    savedSourceKey(source.objectId, id) ??
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
                                memberBindings: [],
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
              label="Parent Dimension"
              value={dimensionMembers.selection(p.sourceDimensionId)}
              items={dimensionMembers.items.filter(
                (l) => l.value !== value.objectId && l.value !== value.memberCatalogId,
              )}
              onChange={async (id) => {
                try {
                  const level = await dimensionMembers.resolve(id)
                  changePopulation({
                    operation: 'distinct_projection',
                    sourceDimensionId: level.objectId,
                  })
                } catch (error) {
                  setPreviewError(
                    error instanceof Error ? error.message : 'Could not select Dimension.',
                  )
                }
              }}
            />
            {parentSchemaLoading && (
              <p className="meta-hint" role="status">
                Loading parent attributes…
              </p>
            )}
            {parentSchemaError && (
              <p className="meta-error" role="alert">
                {parentSchemaError}
              </p>
            )}
            {!remapPopulation && (
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
            )}
          </>
        )}
        {remapPopulation && (
          <PopulationRemapping
            value={value}
            options={mappingOptions}
            onChange={(definition) => set({ ...value, definition })}
          />
        )}
        {cols.length > 0 && (
          <PopulationRelationships embedded={embedded} count={d.memberBindings.length}>
            <h3>
              Dimension relationships (optional)
              <FieldHelp label="Dimension bindings (optional)" />
            </h3>
            <p className="meta-hint">
              Add a dimension only to validate member mappings or bring in dimension attributes. A
              planning table can be built without any bindings.
            </p>
            {p.operation === 'distinct_projection' && (
              <p className="meta-hint">
                Bindings are inherited when all of their mapped Dimension columns are retained.
              </p>
            )}
            <Select
              label="Add dimension"
              required={false}
              value=""
              items={options(
                data.dimensionMembers.filter(
                  (dim) => !d.memberBindings.some((b) => b.memberCatalogId === dim.objectId),
                ),
              )}
              onChange={(memberCatalogId) => {
                const dim = data.dimensionMembers.find((dim) => dim.objectId === memberCatalogId)
                if (!dim) return
                setBinding({
                  memberCatalogId,
                  lookupCardinality: 'many_to_one',
                  keyMappings: dim.definition.keyColumnIds.map((memberColumnId) => {
                    const candidates = dimensionKeyOptions(value, dim, memberColumnId, data)
                    return {
                      memberColumnId,
                      grainColumnId: candidates.length === 1 ? candidates[0].value : '',
                    }
                  }),
                  selectedAttributeColumnIds: [],
                })
                setActiveDimension(memberCatalogId)
              }}
            />
            {d.memberBindings.length === 0 && (
              <p className="meta-hint">
                Add a dimension to configure its lookup and selected attributes.
              </p>
            )}
            {d.memberBindings.length > 0 && (
              <div role="tablist" aria-label="Dimension bindings" className="meta-toolbar">
                {d.memberBindings.map((binding) => (
                  <button
                    type="button"
                    role="tab"
                    key={binding.memberCatalogId}
                    aria-selected={activeDimensionId === binding.memberCatalogId}
                    tabIndex={activeDimensionId === binding.memberCatalogId ? 0 : -1}
                    className={
                      activeDimensionId === binding.memberCatalogId
                        ? 'primary-button'
                        : 'secondary-button'
                    }
                    onClick={() => setActiveDimension(binding.memberCatalogId)}
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
                    {data.dimensionMembers.find((dim) => dim.objectId === binding.memberCatalogId)
                      ?.name ?? 'Dimension'}
                  </button>
                ))}
              </div>
            )}
            {data.dimensionMembers
              .filter((dim) => dim.objectId === activeDimensionId)
              .map((dim) => {
                const b = d.memberBindings.find((b) => b.memberCatalogId === dim.objectId)
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
                                    label={`${dim.name} Dimension column ${i + 1}`}
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
                                                memberColumnId: row.memberColumnId,
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
                                    value={m.memberColumnId}
                                    items={dim.definition.columns.map((c) => ({
                                      value: c.columnId,
                                      label: c.displayName,
                                    }))}
                                    onChange={(memberColumnId) =>
                                      setBinding({
                                        ...b,
                                        keyMappings: b.keyMappings.map((row, j) =>
                                          j === i ? { ...row, memberColumnId } : row,
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
                                      { grainColumnId: '', memberColumnId: '' },
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
                            memberBindings: d.memberBindings.filter(
                              (binding) => binding.memberCatalogId !== dim.objectId,
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
          </PopulationRelationships>
        )}
        <TransformationsPanel
          label="Dimension transformations"
          tabs={[
            {
              label: 'Filters',
              configured: !!d.filters,
              content: (
                <FilterEditor
                  embedded
                  value={d.filters}
                  onChange={(filters) => update({ filters })}
                  {...filterColumns}
                />
              ),
            },
          ]}
        />
      </fieldset>
      {!embedded && base && (
        <div className="meta-subsection" aria-label={'Dimension population ' + value.name}>
          <p role="status">
            Table:{' '}
            {previewBusy
              ? 'loading preview…'
              : (tableStatus?.state.replaceAll('_', ' ') ??
                (savedVersion ? 'loading…' : 'not built'))}
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
            name={value.name || 'Dimension preview'}
            showRefresh={false}
          />
        </>
      )}
      {!embedded && (
        <div className="meta-actions" aria-label="Dimension actions">
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
          <ViewSqlButton
            disabled={busy || previewBusy || !base || !d.grain.keyIds.length}
            loadSql={(signal) => {
              if (!form.current?.reportValidity())
                throw new Error('Complete the required fields first.')
              return metadataApi.request(
                base + '/dimensions/preview/population?sql_only=true',
                'POST',
                inputOf(value),
                undefined,
                signal,
              )
            }}
          />
          <button
            type="submit"
            className="primary-button"
            disabled={busy || previewBusy || readOnly || !d.grain.keyIds.length}
          >
            Save
          </button>
        </div>
      )}
    </Container>
  )
}

function PopulationRelationships({
  embedded,
  count,
  children,
}: {
  embedded: boolean
  count: number
  children: ReactNode
}) {
  return embedded ? (
    <details className="population-details">
      <summary>
        Connect dimensions{' '}
        <span className="meta-hint">{count ? `${count} connected` : 'Optional'}</span>
      </summary>
      {children}
    </details>
  ) : (
    <>{children}</>
  )
}
