import type { Dimension, GrainColumn, PlanningLevel, Snapshot } from './types'

const normalizedType = (type: string): string => {
  let value = type.replace(/\s/g, '').toLowerCase()
  while (/^(nullable|lowcardinality)\(/.test(value) && value.endsWith(')'))
    value = value.slice(value.indexOf('(') + 1, -1)
  return value
}

/** Suggest an initial grain-side mapping; explicit join selections are not restricted to these candidates. */
export function dimensionKeyOptions(
  level: PlanningLevel,
  dimension: Dimension,
  dimensionColumnId: string,
  data: Pick<Snapshot, 'sources' | 'planningLevels' | 'dimensions'>,
): { value: string; label: string }[] {
  const key = dimension.definition.columns.find((c) => c.columnId === dimensionColumnId)
  if (!key) return []
  const keySourceColumnId = key.sourceColumnId
  const dimensionSource = data.sources.find((s) => s.objectId === dimension.definition.sourceId)
  const keySourceColumn = dimensionSource?.definition.columns.find(
    (c) => c.columnId === keySourceColumnId,
  )

  function matches(
    current: PlanningLevel,
    column: GrainColumn,
    ancestors: Set<string>,
    inherited: boolean,
  ): boolean {
    const marker = current.objectId + ':' + column.grainColumnId
    if (ancestors.has(marker) || ancestors.size >= 128) return false
    const seen = new Set(ancestors).add(marker)
    // A parent's recorded binding is stronger than an inferred name match.
    if (inherited) {
      const binding = current.definition.dimensionBindings.find(
        (b) => b.dimensionId === dimension.objectId,
      )
      const mappings = binding?.keyMappings.filter((m) => m.dimensionColumnId === dimensionColumnId)
      if (mappings?.length) return mappings.some((m) => m.grainColumnId === column.grainColumnId)
    }
    const population = current.definition.population
    const ref = column.populationRef
    if (population.operation === 'distinct_projection') {
      const parent = data.planningLevels.find(
        (p) => p.objectId === population.sourcePlanningLevelId,
      )
      const parentColumn = parent?.definition.grain.columns.find(
        (c) => c.grainColumnId === ref.sourceGrainColumnId,
      )
      return !!parent && !!parentColumn && matches(parent, parentColumn, seen, true)
    }
    const sourceId =
      population.operation === 'source'
        ? population.sourceId
        : population.factors.find((f) => f.factorId === ref.factorId)?.sourceId
    const source = data.sources.find((s) => s.objectId === sourceId)
    const sourceColumn = source?.definition.columns.find((c) => c.columnId === ref.columnId)
    if (!sourceColumn || !keySourceColumn) return false
    if (normalizedType(sourceColumn.physicalType) !== normalizedType(keySourceColumn.physicalType))
      return false
    if (sourceId === dimension.definition.sourceId && sourceColumn.columnId === keySourceColumnId)
      return true
    const otherDimensionKey = data.dimensions.some(
      (other) =>
        other.objectId !== dimension.objectId &&
        other.definition.sourceId === sourceId &&
        other.definition.columns.some(
          (c) =>
            other.definition.keyColumnIds.includes(c.columnId) &&
            c.sourceColumnId === sourceColumn.columnId,
        ),
    )
    return !otherDimensionKey && sourceColumn.name === keySourceColumn.name
  }

  return level.definition.grain.columns
    .filter((column) => matches(level, column, new Set(), false))
    .map((column) => ({ value: column.grainColumnId, label: column.name }))
}
