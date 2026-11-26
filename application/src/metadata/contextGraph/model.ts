import { resolveMeasureGrain } from '../grainCatalog'
import type { Expression, Snapshot } from '../types'

export type ContextNodeKind = 'table' | 'dimension' | 'transaction' | 'grain' | 'measure'
export type ContextRelation =
  | 'population'
  | 'projection'
  | 'binding'
  | 'mapping'
  | 'output'
  | 'reference'
  | 'formula'
  | 'calendar'
export type ContextNode = {
  id: string
  name: string
  kind: ContextNodeKind
  subtitle: string
  description: string
  base: boolean
  fields: { label: string; value: string }[]
}
export type ContextEdge = {
  id: string
  source: string
  target: string
  relation: ContextRelation
  label: string
  explanation: string
}
export type ContextGraph = { nodes: ContextNode[]; edges: ContextEdge[] }
export const relationLabels: Record<ContextRelation, string> = {
  population: 'Populates',
  projection: 'Distinct projection',
  binding: 'Dimension binding',
  mapping: 'Grain mapping',
  output: 'Output grain',
  reference: 'Value source',
  formula: 'Formula input',
  calendar: 'Date resolution',
}
export const kindLabels: Record<ContextNodeKind, string> = {
  table: 'Source table',
  dimension: 'Dimension',
  transaction: 'Transaction',
  grain: 'Transaction grain',
  measure: 'Measure',
}
const nodeId = (kind: ContextNodeKind, id: string) => `${kind}:${id}`
const populationLabels = {
  source: 'From a table',
  cartesian_product: 'From multiple tables',
  distinct_projection: 'From an existing dimension',
}

/** Presentation-only lineage from explicit saved references. Never infer semantic paths from names. */
export function buildContextGraph(data: Snapshot, baseDimensionId: string | null): ContextGraph {
  const nodes = new Map<string, ContextNode>()
  const edges = new Map<string, ContextEdge>()
  const addNode = (node: ContextNode) => nodes.set(node.id, node)
  const addEdge = (
    source: string | undefined,
    target: string,
    relation: ContextRelation,
    explanation: string,
  ) => {
    if (!source || source === target || !nodes.has(source) || !nodes.has(target)) return
    const id = `${relation}:${source}:${target}`
    edges.set(id, { id, source, target, relation, label: relationLabels[relation], explanation })
  }
  const dimensionOwners = new Map<string, string>()
  for (const dimension of data.dimensions) {
    dimensionOwners.set(dimension.objectId, nodeId('dimension', dimension.objectId))
    if (dimension.memberCatalogId)
      dimensionOwners.set(dimension.memberCatalogId, nodeId('dimension', dimension.objectId))
  }
  // Member catalogs are internal projections of public Dimensions, not duplicate graph nodes.
  for (const catalog of data.dimensionMembers) {
    if (dimensionOwners.has(catalog.objectId)) continue
    dimensionOwners.set(catalog.objectId, nodeId('dimension', catalog.objectId))
    addNode({
      id: nodeId('dimension', catalog.objectId),
      name: catalog.name,
      kind: 'dimension',
      subtitle: catalog.isTimeDim ? 'Time dimension' : 'Dimension',
      description: catalog.description,
      base: catalog.objectId === baseDimensionId,
      fields: [
        {
          label: 'Keys',
          value: catalog.definition.keyColumnIds
            .map(
              (id) =>
                catalog.definition.columns.find((c) => c.columnId === id)?.displayName ??
                'Unknown field',
            )
            .join(', '),
        },
      ],
    })
  }
  for (const source of data.sources)
    addNode({
      id: nodeId('table', source.objectId),
      name: source.name,
      kind: 'table',
      subtitle: 'Source table',
      description: source.description,
      base: false,
      fields: [
        {
          label: 'Columns',
          value: (source.managed?.definition.columns ?? source.definition.columns)
            .map((c) => c.name)
            .join(', '),
        },
      ],
    })
  for (const dimension of data.dimensions)
    addNode({
      id: nodeId('dimension', dimension.objectId),
      name: dimension.name,
      kind: 'dimension',
      subtitle: dimension.isTimeDim
        ? 'Time dimension'
        : populationLabels[dimension.definition.population.operation],
      description: dimension.description,
      base: dimension.objectId === baseDimensionId,
      fields: [
        { label: 'Population', value: populationLabels[dimension.definition.population.operation] },
        {
          label: 'Grain keys',
          value: dimension.definition.grain.keyIds
            .map(
              (id) =>
                dimension.definition.grain.columns.find((c) => c.grainColumnId === id)?.name ??
                'Unknown field',
            )
            .join(', '),
        },
      ],
    })
  for (const transaction of data.transactions) {
    const d = transaction.definition
    addNode({
      id: nodeId('transaction', transaction.objectId),
      name: transaction.name,
      kind: 'transaction',
      subtitle:
        d.mappingMode === 'resolve_multi_grain'
          ? 'Resolves multiple grains'
          : d.dimensionId
            ? 'Mapped to a dimension'
            : 'Standalone transaction',
      description: transaction.description,
      base: false,
      fields: [
        {
          label: 'Source table',
          value:
            data.sources.find((s) => s.objectId === d.sourceId)?.name ?? 'Missing source table',
        },
        {
          label: 'Native dimension',
          value:
            data.dimensions.find((dim) => dim.objectId === d.dimensionId)?.name ??
            (d.dimensionId ? 'Missing dimension' : 'Own transaction grain'),
        },
      ],
    })
    if (!d.dimensionId) {
      const source = data.sources.find((s) => s.objectId === d.sourceId)
      const columns = source?.managed?.definition.columns ?? source?.definition.columns ?? []
      addNode({
        id: nodeId('grain', transaction.objectId),
        name: transaction.name + ' grain',
        kind: 'grain',
        subtitle: 'Standalone output grain',
        description: 'This Transaction defines its own grain; it is not bound to a Dimension.',
        base: transaction.effectiveGrainId === baseDimensionId,
        fields: [
          {
            label: 'Grain keys',
            value: (d.grainColumnIds ?? [])
              .map((id) => columns.find((c) => c.columnId === id)?.name ?? 'Transformed field')
              .join(', '),
          },
        ],
      })
    }
  }
  const outputNode = (measure: Snapshot['measures'][number]): string | undefined => {
    const resolved = resolveMeasureGrain(measure, data)
    const dimension = dimensionOwners.get(resolved.definition.nativeGrainId)
    if (dimension) return dimension
    const definition = measure.definition
    const tx = data.transactions.find(
      (t) =>
        t.effectiveGrainId === resolved.definition.nativeGrainId ||
        (definition.measureType === 'referenced'
          ? t.objectId === definition.transactionId
          : definition.outputGrain?.kind === 'transaction' &&
            t.objectId === definition.outputGrain.objectId),
    )
    return tx && !tx.definition.dimensionId ? nodeId('grain', tx.objectId) : undefined
  }
  for (const measure of data.measures) {
    const d = measure.definition
    const output = outputNode(measure)
    const tx =
      d.measureType === 'referenced'
        ? data.transactions.find((t) => t.objectId === d.transactionId)
        : undefined
    const source = data.sources.find((s) => s.objectId === tx?.definition.sourceId)
    const valueColumn =
      d.measureType === 'referenced'
        ? (source?.managed?.definition.columns ?? source?.definition.columns ?? []).find(
            (c) => c.columnId === d.valueColumnId,
          )?.name
        : undefined
    addNode({
      id: nodeId('measure', measure.objectId),
      name: measure.name,
      kind: 'measure',
      subtitle: d.measureType === 'referenced' ? 'Referenced measure' : 'Calculated measure',
      description: measure.description,
      base: false,
      fields: [
        {
          label: 'Output grain',
          value: (output && nodes.get(output)?.name) || 'Output grain unavailable',
        },
        ...(d.measureType === 'referenced'
          ? [
              {
                label: 'Value source',
                value:
                  (tx?.name ?? 'Missing transaction') + (valueColumn ? ' · ' + valueColumn : ''),
              },
            ]
          : [
              { label: 'Calculation', value: d.calculationType },
              { label: 'Formula', value: d.formula.displayText },
            ]),
      ],
    })
  }
  for (const catalog of data.dimensionMembers) {
    const target = dimensionOwners.get(catalog.objectId)
    if (target)
      addEdge(
        nodeId('table', catalog.definition.sourceId),
        target,
        'population',
        'Source rows populate this dimension.',
      )
  }
  for (const dimension of data.dimensions) {
    const target = nodeId('dimension', dimension.objectId)
    const population = dimension.definition.population
    if (population.operation === 'source')
      addEdge(
        nodeId('table', population.sourceId),
        target,
        'population',
        'Selected source fields populate the dimension grain.',
      )
    else if (population.operation === 'cartesian_product')
      for (const factor of population.factors)
        addEdge(
          nodeId('table', factor.sourceId),
          target,
          'population',
          'A factor in the configured Cartesian population.',
        )
    else
      addEdge(
        dimensionOwners.get(population.sourceDimensionId),
        target,
        'projection',
        'A distinct projection of the selected parent keys and attributes.',
      )
    for (const binding of dimension.definition.memberBindings)
      addEdge(
        dimensionOwners.get(binding.memberCatalogId),
        target,
        'binding',
        'Explicit dimension mappings validate members or bring additional attributes; they do not change the grain.',
      )
  }
  for (const transaction of data.transactions) {
    const target = nodeId('transaction', transaction.objectId)
    const d = transaction.definition
    addEdge(
      nodeId('table', d.sourceId),
      target,
      'population',
      'The transaction reads this managed source view.',
    )
    if (d.dimensionId)
      addEdge(
        dimensionOwners.get(d.dimensionId),
        target,
        'mapping',
        'The transaction maps to this dimension’s grain; measure values align using its configured mappings.',
      )
    else
      addEdge(
        target,
        nodeId('grain', transaction.objectId),
        'mapping',
        'The transaction’s selected keys define its standalone output grain.',
      )
    if (d.calendarResolution)
      addEdge(
        dimensionOwners.get(d.calendarResolution.memberCatalogId),
        target,
        'calendar',
        'This calendar resolves the transaction’s date range before grain mapping.',
      )
  }
  const formulaInputs = (expression: Expression, target: string) => {
    if (expression.type === 'measureRef')
      addEdge(
        nodeId('measure', expression.measureId),
        target,
        'formula',
        'This calculated measure depends on the input measure; this line does not prove plotting compatibility.',
      )
    else if (expression.type === 'binary') {
      formulaInputs(expression.left, target)
      formulaInputs(expression.right, target)
    } else if (expression.type === 'call' || expression.type === 'count')
      for (const arg of expression.arguments) formulaInputs(arg, target)
  }
  for (const measure of data.measures) {
    const target = nodeId('measure', measure.objectId)
    addEdge(
      outputNode(measure),
      target,
      'output',
      'The measure is defined at this native/output grain. This line does not claim that values are already materialised.',
    )
    if (measure.definition.measureType === 'referenced')
      addEdge(
        nodeId('transaction', measure.definition.transactionId),
        target,
        'reference',
        'Dotted: the value column comes from this transaction; the solid output-grain link shows where the measure lives.',
      )
    else formulaInputs(measure.definition.formula.expression, target)
  }
  return { nodes: [...nodes.values()], edges: [...edges.values()] }
}

/** Ancestors and descendants separately, so shared siblings are not falsely included in lineage. */
export function lineageIds(graph: ContextGraph, selected: string): Set<string> {
  const found = new Set([selected])
  for (const direction of ['up', 'down']) {
    const adjacency = new Map<string, string[]>()
    for (const edge of graph.edges) {
      const from = direction === 'up' ? edge.target : edge.source
      const to = direction === 'up' ? edge.source : edge.target
      adjacency.set(from, [...(adjacency.get(from) ?? []), to])
    }
    const visited = new Set([selected])
    const pending = [selected]
    while (pending.length)
      for (const id of adjacency.get(pending.pop()!) ?? []) {
        if (!visited.has(id)) {
          visited.add(id)
          found.add(id)
          pending.push(id)
        }
      }
  }
  return found
}
