import { identity, type Dimension, type Transaction, type SourceColumn } from './types'

export type TransactionPopulationSave = {
  transaction: Transaction & { measures?: unknown[] }
  population: Dimension | null
  expectedVersion: number | null
  expectedPopulationVersion: number | null
  expectedRevisionVersion: number
  useAsBase: boolean
}

export function newTransactionPopulation(transaction: Transaction): Dimension {
  return {
    ...identity('population'),
    kind: 'dimension',
    name: transaction.name ? transaction.name + ' population' : 'Planning population',
    displayName: transaction.name ? transaction.name + ' population' : 'Planning population',
    definition: {
      population: { operation: 'source', sourceId: transaction.definition.sourceId },
      grain: { keyIds: [], columns: [] },
      memberBindings: [],
    },
  }
}

/** IDs are local to their owning table; matching ID strings alone prove nothing. */
export function suggestPopulationMappings(
  population: Dimension,
  columns: SourceColumn[],
  existing: Transaction['definition']['keyMappings'] = [],
) {
  return population.definition.grain.columns
    .filter((column) => population.definition.grain.keyIds.includes(column.grainColumnId))
    .map((key) => {
      const saved = existing.find(
        (mapping) => mapping.grainColumnId === key.grainColumnId,
      )?.sourceColumnId
      const candidates = columns.filter(
        (column) => column.name.toLowerCase() === key.name.toLowerCase(),
      )
      return {
        grainColumnId: key.grainColumnId,
        sourceColumnId:
          saved && columns.some((column) => column.columnId === saved)
            ? saved
            : candidates.length === 1
              ? candidates[0].columnId
              : '',
      }
    })
}
