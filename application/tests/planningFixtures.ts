import type { Envelope, Dimension } from '../src/metadata/types'

export function savedLevel(id: string, parent?: string): Dimension & Envelope {
  return {
    objectId: id,
    kind: 'dimension',
    name: id.toUpperCase(),
    displayName: id.toUpperCase(),
    description: '',
    modelId: 'm',
    revisionId: 'r',
    version: 3,
    createdAt: '',
    updatedAt: '',
    definition: {
      population: parent
        ? { operation: 'distinct_projection', sourceDimensionId: parent }
        : { operation: 'source', sourceId: 'source' },
      grain: {
        keyIds: ['product'],
        columns: [
          { grainColumnId: 'product', name: 'product', populationRef: { columnId: 'product' } },
        ],
      },
      memberBindings: [],
    },
  }
}
