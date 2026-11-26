import { withResolvedGrains } from './grainCatalog'
import { ApiError } from '../lib/api'
import {
  resources,
  type ManagedTable,
  type EngineRun,
  type Kind,
  type Model,
  type ObjectDocument,
  type ObjectInput,
  type Revision,
  type Report,
  type Snapshot,
} from './types'
export class MetadataApi {
  async request<T>(
    path: string,
    method = 'GET',
    body?: unknown,
    version?: number,
    signal?: AbortSignal,
    idempotencyKey?: string,
  ): Promise<T> {
    let response: Response
    try {
      response = await fetch('/api/v1' + path, {
        method,
        signal,
        headers: {
          ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
          ...(idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : {}),
          ...(version === undefined ? {} : { 'If-Match': `"${version}"` }),
        },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      })
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') throw error
      throw new ApiError(
        'Cannot reach the metadata service. Check the backend and retry.',
        'network_error',
        0,
      )
    }
    if (response.status === 204) return undefined as T
    const result = await response.json()
    if (!response.ok) {
      const e = result.error
      const details = (e?.details ?? [])
        .map((d: { message?: string; path?: string | string[]; objectId?: string }) =>
          [d.objectId, Array.isArray(d.path) ? d.path.join('.') : d.path, d.message]
            .filter(Boolean)
            .join(': '),
        )
        .join('\n')
      throw new ApiError(
        [e?.message ?? 'Metadata request failed.', details].filter(Boolean).join('\n'),
        e?.code ?? 'request_failed',
        response.status,
        e?.requestId,
      )
    }
    return result as T
  }
  async list<T>(path: string, signal?: AbortSignal): Promise<T[]> {
    const items: T[] = []
    let cursor: string | null = null
    do {
      const page: { items: T[]; nextCursor: string | null } = await this.request<{
        items: T[]
        nextCursor: string | null
      }>(
        path + '?limit=100' + (cursor ? '&cursor=' + encodeURIComponent(cursor) : ''),
        'GET',
        undefined,
        undefined,
        signal,
      )
      items.push(...page.items)
      if (page.nextCursor === cursor && cursor)
        throw new Error('Metadata pagination did not advance.')
      cursor = page.nextCursor
    } while (cursor)
    return items
  }
  models(signal?: AbortSignal) {
    return this.list<Model>('/models', signal)
  }
  scope(model: string, revision: string) {
    return `/models/${encodeURIComponent(model)}/revisions/${encodeURIComponent(revision)}`
  }
  async snapshot(model: string, revision: string, signal?: AbortSignal): Promise<Snapshot> {
    const base = this.scope(model, revision)
    const [sources, dimensions, transactions, measures] = await Promise.all([
      this.list<Snapshot['sources'][number]>(base + '/sources', signal),
      this.list<Snapshot['dimensions'][number]>(base + '/dimensions', signal),
      this.list<Snapshot['transactions'][number]>(base + '/transactions', signal),
      this.list<Snapshot['measures'][number]>(base + '/measures', signal),
    ])
    const dimensionMembers: Snapshot['dimensionMembers'] = dimensions.flatMap((dimension) =>
      dimension.memberCatalog && dimension.memberCatalogId
        ? [
            {
              objectId: dimension.memberCatalogId,
              kind: 'dimensionMembers' as const,
              name: dimension.name,
              displayName: dimension.name,
              description: dimension.description,
              isTimeDim: dimension.isTimeDim ?? false,
              definition: dimension.memberCatalog,
              modelId: dimension.modelId,
              revisionId: dimension.revisionId,
              version: dimension.version,
              createdAt: dimension.createdAt,
              updatedAt: dimension.updatedAt,
            },
          ]
        : [],
    )
    const managed = await this.list<ManagedTable>(base + '/managed-tables', signal)
    return withResolvedGrains({
      sources: sources.map((s) => ({
        ...s,
        managed: managed.find((m) => m.sourceId === s.objectId),
      })),
      dimensionMembers,
      dimensions,
      transactions,
      measures,
    })
  }
  revisions(model: string, signal?: AbortSignal) {
    return this.list<Revision>(`/models/${encodeURIComponent(model)}/revisions`, signal)
  }
  save(model: string, revision: string, input: ObjectInput, version?: number) {
    return this.request<ObjectDocument>(
      this.scope(model, revision) +
        '/' +
        (input.kind === 'dimensionMembers' && !version ? 'dimensions' : resources[input.kind]) +
        (version ? '/' + encodeURIComponent(input.objectId) : ''),
      version ? 'PUT' : 'POST',
      input,
      version,
    )
  }
  async managedOperation(path: string, body?: unknown, version?: number) {
    const scope = path.match(/^(\/models\/[^/]+\/revisions\/[^/]+)\//)?.[1]
    if (!scope) throw new Error('Engine operations require a revision-scoped path.')
    let run = await this.request<EngineRun>(
      path,
      'POST',
      body,
      version,
      undefined,
      crypto.randomUUID(),
    )
    while (run.state === 'queued' || run.state === 'running') {
      await new Promise((resolve) => setTimeout(resolve, 500))
      run = await this.request<EngineRun>(scope + '/engine/runs/' + run.runId)
    }
    if (run.state !== 'succeeded')
      throw new Error(
        [
          run.error?.message ?? `Build ${run.state}. Reload metadata before retrying.`,
          ...(run.error?.details ?? []).map((d) => d.message),
        ].join('\n'),
      )
    return run
  }
  async saveManaged(path: string, body: unknown, version: number) {
    const scope = path.match(/^(\/models\/[^/]+\/revisions\/[^/]+)\//)?.[1]
    if (!scope) throw new Error('Engine operations require a revision-scoped path.')
    const run = await this.request<EngineRun>(
      path,
      'PUT',
      body,
      version,
      undefined,
      crypto.randomUUID(),
    )
    let status = run
    while (status.state === 'queued' || status.state === 'running') {
      await new Promise((resolve) => setTimeout(resolve, 500))
      status = await this.request<EngineRun>(scope + '/engine/runs/' + run.runId)
    }
    if (status.state !== 'succeeded')
      throw new Error(
        [
          status.error?.message ?? `Build ${status.state}.`,
          ...(status.error?.details ?? []).map((d) => d.message),
        ].join('\n'),
      )
  }
  remove(model: string, revision: string, kind: Kind, id: string, version: number) {
    return this.request<void>(
      this.scope(model, revision) + '/' + resources[kind] + '/' + encodeURIComponent(id),
      'DELETE',
      undefined,
      version,
    )
  }
  validate(model: string, revision: string) {
    return this.request<Report>(this.scope(model, revision) + '/validate', 'POST')
  }
}
export const metadataApi = new MetadataApi()
