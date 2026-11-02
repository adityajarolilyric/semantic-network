import { expect, it, vi } from 'vitest'
import { MetadataApi } from '../src/metadata/api'

it('polls network builds at the revision engine endpoint even if the model name is measures', async () => {
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ runId: 'run1', state: 'queued' }), { status: 202 }),
    )
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ runId: 'run1', state: 'succeeded' }), { status: 200 }),
    )
  vi.stubGlobal('fetch', fetch)
  const run = await new MetadataApi().managedOperation(
    '/models/measures/revisions/r/measures/execution/build',
    undefined,
    7,
  )
  expect(run.state).toBe('succeeded')
  expect(fetch.mock.calls[1][0]).toBe('/api/v1/models/measures/revisions/r/engine/runs/run1')
})

it('polls saved managed views at the shared engine endpoint', async () => {
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ runId: 'pivot_run', state: 'queued' }), { status: 202 }),
    )
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ runId: 'pivot_run', state: 'succeeded' }), { status: 200 }),
    )
  vi.stubGlobal('fetch', fetch)
  await new MetadataApi().saveManaged('/models/m/revisions/r/managed-tables/source', {}, 3)
  expect(fetch.mock.calls[1][0]).toBe('/api/v1/models/m/revisions/r/engine/runs/pivot_run')
})
