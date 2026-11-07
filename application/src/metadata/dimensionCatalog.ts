import type { DimensionMembers, Dimension, Envelope, Snapshot } from './types'

type DimensionSnapshot = { dimensionMembers: DimensionMembers[]; dimensions: Dimension[] }

/** One public DimensionMembers; source-backed objects retain an internal engine grain. */
export function dimensionGrain(
  data: DimensionSnapshot,
  memberCatalogId: string,
): Dimension | undefined {
  return data.dimensions.find(
    (level) => level.objectId === memberCatalogId || level.memberCatalogId === memberCatalogId,
  )
}
export function dimensionCatalog(data: Snapshot) {
  return [...data.dimensionMembers, ...data.dimensions.filter((level) => !level.memberCatalogId)]
}
export type CatalogDimension = (DimensionMembers | Dimension) & Partial<Envelope>
