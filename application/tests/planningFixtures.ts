import type { Envelope, PlanningLevel } from '../src/metadata/types'

export function savedLevel(id: string, parent?: string): PlanningLevel & Envelope {
  return {
    objectId: id,
    kind: 'planningLevel',
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
        ? { operation: 'distinct_projection', sourcePlanningLevelId: parent }
        : { operation: 'source', sourceId: 'source' },
      grain: {
        keyIds: ['product'],
        columns: [
          { grainColumnId: 'product', name: 'product', populationRef: { columnId: 'product' } },
        ],
      },
      dimensionBindings: [],
    },
  }
}
