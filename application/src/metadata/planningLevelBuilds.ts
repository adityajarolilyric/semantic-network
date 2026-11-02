import { ApiError } from '../lib/api'
import { MetadataApi, metadataApi } from './api'
import type { Envelope, PlanningLevel, Revision } from './types'

type SavedLevel = PlanningLevel & Envelope
export type PlanningBuildProgress = {
  levelName: string
  completed: number
  total: number
}

/** Reuse saved-level builds, waiting for each parent before submitting its children. */
export class PlanningLevelBuilds {
  constructor(private readonly api: MetadataApi = metadataApi) {}

  private ordered(levels: SavedLevel[]): SavedLevel[] {
    const byId = new Map(levels.map((level) => [level.objectId, level]))
    const children = new Map<string, SavedLevel[]>()
    const ready: SavedLevel[] = []
    for (const level of levels) {
      const population = level.definition.population
      if (population.operation !== 'distinct_projection') ready.push(level)
      else {
        const parent = population.sourcePlanningLevelId
        if (!byId.has(parent))
          throw new Error(`${level.name}: its parent planning level is missing. Reload metadata.`)
        const siblings = children.get(parent) ?? []
        siblings.push(level)
        children.set(parent, siblings)
      }
    }
    for (let index = 0; index < ready.length; index++)
      ready.push(...(children.get(ready[index].objectId) ?? []))
    if (ready.length !== levels.length)
      throw new Error('Planning levels contain a dependency cycle. Correct it before building.')
    return ready
  }

  async buildAll(
    modelId: string,
    revisionId: string,
    onProgress: (progress: PlanningBuildProgress) => void,
  ) {
    const base = this.api.scope(modelId, revisionId)
    const revision = await this.api.request<Revision>(base)
    if (revision.approvalStatus !== 'draft')
      throw new Error('Published planning levels are read-only. Create a draft before building.')
    const levels = this.ordered(await this.api.list<SavedLevel>(base + '/planning-levels'))
    for (const [index, level] of levels.entries()) {
      onProgress({ levelName: level.name, completed: index, total: levels.length })
      try {
        await this.api.managedOperation(
          base + '/planning-levels/' + encodeURIComponent(level.objectId) + '/build',
          undefined,
          level.version,
        )
      } catch (error) {
        const message = `Could not build ${level.name}. ${index} of ${levels.length} planning levels built; remaining builds stopped.\n${error instanceof Error ? error.message : 'Build failed.'}`
        if (error instanceof ApiError)
          throw new ApiError(message, error.code, error.status, error.requestId)
        throw new Error(message, { cause: error })
      }
    }
    return levels.length
  }
}

export const planningLevelBuilds = new PlanningLevelBuilds()
