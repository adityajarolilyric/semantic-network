import { grainCatalog, grainReferenceKey } from './grainCatalog'
import type { GrainReference, Snapshot } from './types'

export function useGrainCatalog(data: Snapshot, _base?: string) {
  const levels = grainCatalog(data)
  const boundOwner = (owner?: GrainReference): GrainReference | undefined => {
    if (owner?.kind !== 'transaction') return owner
    const transaction = data.transactions.find((candidate) => candidate.objectId === owner.objectId)
    return transaction?.definition.dimensionId
      ? { kind: 'dimension', objectId: transaction.definition.dimensionId }
      : owner
  }
  return {
    levels,
    items: levels.map((grain) => ({ value: grainReferenceKey(grain.owner), label: grain.name })),
    selection(id: string, owner?: GrainReference) {
      return owner
        ? grainReferenceKey(boundOwner(owner)!)
        : grainReferenceKey(
            levels.find((grain) => grain.objectId === id)?.owner ?? {
              kind: 'dimension',
              objectId: id,
            },
          )
    },
    async resolve(key: string) {
      const transaction = data.transactions.find(
        (candidate) =>
          grainReferenceKey({ kind: 'transaction', objectId: candidate.objectId }) === key,
      )
      const canonical = transaction?.definition.dimensionId
        ? grainReferenceKey({ kind: 'dimension', objectId: transaction.definition.dimensionId })
        : key
      const grain = levels.find(
        (grain) => grainReferenceKey(grain.owner) === canonical || grain.objectId === canonical,
      )
      if (!grain) throw new Error('Choose an available Transaction or Dimension grain.')
      return grain
    },
  }
}
