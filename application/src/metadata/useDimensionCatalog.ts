import { dimensionCatalog, dimensionGrain } from './dimensionCatalog'
import type { Dimension, Snapshot } from './types'

/** Dimensions already own their member metadata; selecting one never creates another object. */
export function useDimensionCatalog(data: Snapshot, _base?: string) {
  return {
    levels: data.dimensions,
    items: dimensionCatalog(data).map((dimension) => ({
      value: dimension.objectId,
      label: dimension.name,
    })),
    selection: (id: string) =>
      data.dimensions.find((dimension) => dimension.objectId === id)?.memberCatalogId ?? id,
    async resolve(id: string): Promise<Dimension> {
      const dimension = dimensionGrain(data, id)
      if (!dimension) throw new Error('The selected Dimension is unavailable.')
      return dimension
    },
  }
}
