import type { Binding, DimensionMembers, GrainColumn, Dimension } from './types'

/** Inherit only lookups whose complete mapped join tuple survives the projection. */
export function projectionBindings(
  parent: Dimension | undefined,
  columns: GrainColumn[],
  current: Binding[],
  dimensionMembers: DimensionMembers[],
): Binding[] {
  if (!parent) return []
  const childByParent = new Map(
    columns
      .filter((c) => c.populationRef.sourceGrainColumnId)
      .map((c) => [c.populationRef.sourceGrainColumnId, c.grainColumnId]),
  )
  const inherited = parent.definition.memberBindings.flatMap((binding) => {
    const dimension = dimensionMembers.find((d) => d.objectId === binding.memberCatalogId)
    if (
      !dimension ||
      binding.keyMappings.length === 0 ||
      !binding.keyMappings.every((m) =>
        dimension.definition.columns.some((c) => c.columnId === m.memberColumnId),
      ) ||
      !binding.keyMappings.every((m) => !!m.grainColumnId && childByParent.has(m.grainColumnId))
    )
      return []
    const existing = current.find((b) => b.memberCatalogId === binding.memberCatalogId)
    return [
      {
        ...binding,
        keyMappings: binding.keyMappings.map((m) => ({
          ...m,
          grainColumnId: childByParent.get(m.grainColumnId ?? undefined)!,
        })),
        selectedAttributeColumnIds: [
          ...(existing?.selectedAttributeColumnIds ?? binding.selectedAttributeColumnIds),
        ].filter((id) => dimension.definition.attributeColumnIds.includes(id)),
      },
    ]
  })
  const inheritedIds = new Set(inherited.map((b) => b.memberCatalogId))
  const childIds = new Set(columns.map((c) => c.grainColumnId))
  const manual = current.filter(
    (binding) =>
      !inheritedIds.has(binding.memberCatalogId) &&
      binding.keyMappings.every((m) => childIds.has(m.grainColumnId ?? '')),
  )
  return [...inherited, ...manual]
}
