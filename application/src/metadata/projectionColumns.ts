import type { PlanningTableStatus } from './PlanningTablePanel'
import type { Dimension, PlanningLevel } from './types'

/** Show business attributes, not physical grain aliases or measure columns. */
export function projectionAttributes(
  parent: PlanningLevel | undefined,
  columns: PlanningTableStatus['columns'],
  dimensions: Dimension[],
) {
  const keys = new Set(parent?.definition.grain.keyIds ?? [])
  const attributes = columns.filter(
    (column) => !keys.has(column.columnId) && column.role !== 'key' && column.role !== 'measure',
  )
  const named = attributes.map((column) => {
    const origin = column.lineage
    const dimColumn =
      origin?.kind === 'dimension'
        ? dimensions
            .find((d) => d.objectId === origin.dimensionId)
            ?.definition.columns.find((c) => c.columnId === origin.columnId)
        : undefined
    const label =
      dimColumn?.displayName ??
      parent?.definition.populationAttributes?.find((a) => a.columnId === column.columnId)?.name ??
      column.displayName ??
      column.name
    return { value: column.columnId, label, owner: column.ownerName }
  })
  return named.map((column) => ({
    value: column.value,
    label:
      named.filter((c) => c.label === column.label).length > 1 && column.owner
        ? `${column.label} (${column.owner})`
        : column.label,
  }))
}
