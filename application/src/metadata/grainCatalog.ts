import type { GrainOption, GrainReference, Measure, Snapshot } from './types'

export const grainReferenceKey = (owner: GrainReference) => owner.kind + ':' + owner.objectId

export function grainCatalog(data: Snapshot): GrainOption[] {
  const dimensions = data.dimensions.map((dimension) => ({
    ...dimension,
    owner: { kind: 'dimension' as const, objectId: dimension.objectId },
  }))
  const transactions: GrainOption[] = data.transactions.flatMap((transaction) => {
    // Bound Transactions consume their Dimension; they are not separate populations.
    if (transaction.definition.dimensionId) return []
    const contextId = transaction.effectiveGrainId
    if (!contextId) return []
    const source = data.sources.find(
      (candidate) => candidate.objectId === transaction.definition.sourceId,
    )
    const columns = source?.managed?.definition.columns ?? source?.definition.columns ?? []
    const definition = {
      population: { operation: 'source' as const, sourceId: transaction.definition.sourceId },
      grain: {
        keyIds: transaction.definition.grainColumnIds ?? [],
        columns: (transaction.definition.grainColumnIds ?? []).map((key) => ({
          grainColumnId: key,
          name: columns.find((column) => column.columnId === key)?.name ?? key,
          populationRef: { columnId: key },
        })),
      },
      populationAttributes: [],
      memberBindings: [],
      duplicateKeyPolicy: 'reject' as const,
      nullKeyPolicy: 'reject' as const,
    }
    return [
      {
        ...transaction,
        objectId: contextId,
        kind: 'transactionGrain' as const,
        name: transaction.name + ' · Transaction grain',
        owner: { kind: 'transaction' as const, objectId: transaction.objectId },
        definition,
      },
    ]
  })
  return [...dimensions, ...transactions]
}

/** Preview only the contexts where the engine actually stores measure values. */
export function materialisationCatalog(data: Snapshot, baseId: string | null): GrainOption[] {
  const targets = new Set<string>(baseId ? [baseId] : [])
  const transactionGrain = (id: string) => {
    const transaction = data.transactions.find((candidate) => candidate.objectId === id)
    return transaction?.definition.dimensionId ?? transaction?.effectiveGrainId
  }
  for (const transaction of data.transactions) {
    const id = transactionGrain(transaction.objectId)
    if (id) targets.add(id)
  }
  for (const measure of data.measures) {
    if (measure.definition.measureType !== 'calculated') continue
    const owner = measure.definition.outputGrain
    const id =
      owner?.kind === 'transaction'
        ? transactionGrain(owner.objectId)
        : owner?.kind === 'dimension'
          ? owner.objectId
          : measure.definition.nativeGrainId
    if (id) targets.add(id)
  }
  return grainCatalog(data).filter((grain) => targets.has(grain.objectId))
}

/** Rehydrate derived context after API serialization removes nativeGrainId. */
export function resolveMeasureGrain<T extends Measure>(measure: T, data: Snapshot): T {
  const definition = measure.definition
  const owner =
    definition.measureType === 'referenced'
      ? { kind: 'transaction' as const, objectId: definition.transactionId }
      : definition.outputGrain
  const transaction =
    owner?.kind === 'transaction'
      ? data.transactions.find((transaction) => transaction.objectId === owner.objectId)
      : undefined
  const nativeGrainId =
    owner?.kind === 'dimension'
      ? owner.objectId
      : (transaction?.definition.dimensionId ??
        transaction?.effectiveGrainId ??
        definition.nativeGrainId ??
        '')
  return { ...measure, definition: { ...definition, nativeGrainId } }
}

export function withResolvedGrains(data: Snapshot): Snapshot {
  return {
    ...data,
    measures: data.measures.map((measure) => resolveMeasureGrain(measure, data)),
  }
}
