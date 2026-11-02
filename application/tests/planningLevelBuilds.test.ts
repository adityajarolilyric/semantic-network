import { expect, it, vi } from 'vitest'
import { ApiError } from '../src/lib/api'
import { MetadataApi } from '../src/metadata/api'
import { PlanningLevelBuilds } from '../src/metadata/planningLevelBuilds'
import { savedLevel } from './planningFixtures'

function setup(levels = [savedLevel('child', 'parent'), savedLevel('parent')]) {
  const api = new MetadataApi()
  const request = vi.spyOn(api, 'request').mockResolvedValue({ approvalStatus: 'draft' })
  const list = vi.spyOn(api, 'list').mockResolvedValue(levels)
  const build = vi.spyOn(api, 'managedOperation').mockResolvedValue({
    runId: 'run',
    state: 'succeeded',
  } as never)
  return { service: new PlanningLevelBuilds(api), request, list, build }
}

it('loads saved levels and waits for each parent before submitting projections', async () => {
  const { service, build, list } = setup([
    savedLevel('grandchild', 'child'),
    savedLevel('child', 'parent'),
    savedLevel('parent'),
  ])
  let finishParent!: () => void
  build.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finishParent = () => resolve({ state: 'succeeded' } as never)
      }),
  )
  const progress = vi.fn()
  const result = service.buildAll('m', 'r', progress)
  await vi.waitFor(() => expect(build).toHaveBeenCalledTimes(1))
  expect(build).toHaveBeenLastCalledWith(
    '/models/m/revisions/r/planning-levels/parent/build',
    undefined,
    3,
  )
  finishParent()
  expect(await result).toBe(3)
  expect(build.mock.calls.map(([path]) => path.split('/').at(-2))).toEqual([
    'parent',
    'child',
    'grandchild',
  ])
  expect(list).toHaveBeenCalledWith('/models/m/revisions/r/planning-levels')
  expect(progress.mock.calls.map(([value]) => value)).toEqual([
    { levelName: 'PARENT', completed: 0, total: 3 },
    { levelName: 'CHILD', completed: 1, total: 3 },
    { levelName: 'GRANDCHILD', completed: 2, total: 3 },
  ])
})

it('stops after a failed build and preserves its request ID and partial progress', async () => {
  const { service, build } = setup([
    savedLevel('child', 'parent'),
    savedLevel('parent'),
    savedLevel('other'),
  ])
  build.mockRejectedValueOnce(new ApiError('Duplicate keys', 'duplicate', 409, 'request-1'))
  await expect(service.buildAll('m', 'r', vi.fn())).rejects.toMatchObject({
    message: expect.stringContaining('Could not build PARENT. 0 of 3'),
    requestId: 'request-1',
  })
  expect(build).toHaveBeenCalledTimes(1)
})

it('reports earlier successful builds when a child fails and submits no descendants', async () => {
  const { service, build } = setup([
    savedLevel('child', 'parent'),
    savedLevel('parent'),
    savedLevel('last', 'child'),
  ])
  build
    .mockResolvedValueOnce({ state: 'succeeded' } as never)
    .mockRejectedValueOnce(new Error('Conflict'))
  await expect(service.buildAll('m', 'r', vi.fn())).rejects.toThrow('1 of 3 planning levels built')
  expect(build).toHaveBeenCalledTimes(2)
})

it.each([
  ['cycle', [savedLevel('a', 'b'), savedLevel('b', 'a')], 'dependency cycle'],
  ['missing parent', [savedLevel('child', 'missing')], 'parent planning level is missing'],
])('rejects a %s before building any level', async (_name, levels, message) => {
  const { service, build } = setup(levels)
  await expect(service.buildAll('m', 'r', vi.fn())).rejects.toThrow(message)
  expect(build).not.toHaveBeenCalled()
})

it('refuses published revisions before reading or building levels', async () => {
  const { service, request, list, build } = setup()
  request.mockResolvedValue({ approvalStatus: 'published' })
  await expect(service.buildAll('m', 'r', vi.fn())).rejects.toThrow('read-only')
  expect(list).not.toHaveBeenCalled()
  expect(build).not.toHaveBeenCalled()
})
