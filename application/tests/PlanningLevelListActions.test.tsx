import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'
import { MetadataWorkspace } from '../src/metadata/MetadataWorkspace'
import { useMetadata } from '../src/metadata/useMetadata'
import { planningLevelBuilds } from '../src/metadata/planningLevelBuilds'
import { emptySnapshot } from '../src/metadata/types'
import { savedLevel } from './planningFixtures'

vi.mock('../src/metadata/useMetadata', () => ({ useMetadata: vi.fn() }))
function setup(status: 'draft' | 'published' = 'draft', empty = false) {
  const refresh = vi.fn()
  vi.mocked(useMetadata).mockReturnValue({
    data: { ...emptySnapshot(), planningLevels: empty ? [] : [savedLevel('parent')] },
    models: [],
    revisions: [],
    loading: false,
    error: '',
    refresh,
    choose: vi.fn(),
    updateRevisionVersion: vi.fn(),
    model: {
      modelId: 'm',
      name: 'Model',
      displayName: 'Model',
      description: '',
      draftRevisionId: 'r',
      publishedRevisionId: null,
      version: 1,
      revisionCounter: 1,
    },
    revision: {
      modelId: 'm',
      revisionId: 'r',
      version: 1,
      revisionNumber: 1,
      approvalStatus: status,
      basePlanningLevelId: null,
      changeReason: '',
      parentRevisionId: null,
    },
  })
  render(
    <MetadataWorkspace tab="levels" onTab={vi.fn()}>
      {() => null}
    </MetadataWorkspace>,
  )
  return refresh
}
beforeEach(() => vi.restoreAllMocks())

it('places Build all next to Reload and displays progress while controls are disabled', async () => {
  const user = userEvent.setup()
  const refresh = setup()
  let finish!: () => void
  vi.spyOn(planningLevelBuilds, 'buildAll').mockImplementation((_model, _revision, progress) => {
    progress({ levelName: 'PARENT', completed: 0, total: 1 })
    return new Promise((resolve) => {
      finish = () => resolve(1)
    })
  })
  const build = screen.getByRole('button', { name: 'Build all planning levels' })
  expect(screen.getByRole('button', { name: 'Reload metadata' }).nextElementSibling).toBe(build)
  await user.click(build)
  expect(await screen.findByText('Building 1 of 1: PARENT…')).toBeVisible()
  expect(build).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Reload metadata' })).toBeDisabled()
  finish()
  await screen.findByText('All planning levels built. Rebuild measure values in Materialise.')
  expect(refresh).toHaveBeenCalledOnce()
})

it.each([
  ['published' as const, false],
  ['draft' as const, true],
])('disables bulk build for %s revisions or empty lists', (status, empty) => {
  setup(status, empty)
  expect(screen.getByRole('button', { name: 'Build all planning levels' })).toBeDisabled()
})

it('refreshes metadata after partial failure without claiming success', async () => {
  const user = userEvent.setup()
  const refresh = setup()
  vi.spyOn(planningLevelBuilds, 'buildAll').mockRejectedValue(
    new Error('CHILD failed; 1 of 2 built'),
  )
  await user.click(screen.getByRole('button', { name: 'Build all planning levels' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('1 of 2 built')
  await waitFor(() => expect(refresh).toHaveBeenCalledOnce())
  expect(screen.queryByText(/All planning levels built/)).not.toBeInTheDocument()
})
