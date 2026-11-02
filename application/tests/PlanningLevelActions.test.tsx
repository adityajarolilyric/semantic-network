import { render, screen, within, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { PlanningLevelEditor } from '../src/metadata/editors/PlanningLevelEditor'
import { metadataApi } from '../src/metadata/api'
import { emptySnapshot, type PlanningLevel } from '../src/metadata/types'

const initial: PlanningLevel = {
  objectId: 'level',
  kind: 'planningLevel',
  name: 'Product',
  displayName: 'Product',
  description: '',
  definition: {
    population: { operation: 'source', sourceId: 'source' },
    grain: {
      keyIds: ['product'],
      columns: [
        { grainColumnId: 'product', name: 'product', populationRef: { columnId: 'product' } },
      ],
    },
    dimensionBindings: [],
  },
}
function data() {
  const snapshot = emptySnapshot()
  snapshot.sources = [
    {
      objectId: 'source',
      name: 'Products',
      description: '',
      kind: 'source',
      modelId: 'm',
      revisionId: 'r',
      createdAt: '',
      updatedAt: '',
      version: 1,
      definition: {
        tableName: 'Products',
        inScope: true,
        keyColumns: [],
        columns: [
          { columnId: 'product', name: 'product', physicalType: 'String', nullable: false },
        ],
      },
    },
  ]
  return snapshot
}
const ready = {
  state: 'ready',
  rowCount: 1,
  columns: [{ columnId: 'product', name: 'product', physicalType: 'String' }],
  rows: [['P1']],
}
afterEach(() => vi.restoreAllMocks())

it('has only Cancel, Preview and Save actions and reuses an unchanged built table on Save', async () => {
  const request = vi.spyOn(metadataApi, 'request').mockResolvedValue(ready)
  const build = vi.spyOn(metadataApi, 'managedOperation')
  const persisted = vi.fn()
  const user = userEvent.setup()
  render(
    <PlanningLevelEditor
      initial={initial}
      data={data()}
      editing
      busy={false}
      savedVersion={1}
      base="/models/m/revisions/r"
      onSave={vi.fn()}
      onPersisted={persisted}
      onCancel={vi.fn()}
    />,
  )
  const actions = screen.getByLabelText('Planning level actions')
  expect(
    within(actions)
      .getAllByRole('button')
      .map((b) => b.textContent),
  ).toEqual(['Cancel', 'Preview', 'Save'])
  expect(screen.queryByRole('button', { name: 'Build table' })).not.toBeInTheDocument()
  await user.click(within(actions).getByRole('button', { name: 'Save' }))
  await waitFor(() => expect(persisted).toHaveBeenCalledWith(initial, 1, true))
  expect(build).not.toHaveBeenCalled()
  expect(request.mock.calls.every((call) => call[1] === undefined || call[1] === 'GET')).toBe(true)
})

it('builds changed configuration and atomically commits it on Save', async () => {
  const user = userEvent.setup()
  const document = {
    ...initial,
    name: 'Changed',
    version: 2,
    modelId: 'm',
    revisionId: 'r',
    createdAt: '',
    updatedAt: '',
  }
  const request = vi
    .spyOn(metadataApi, 'request')
    .mockImplementation(async (path) =>
      path.endsWith('/draft/commit') ? (document as never) : (ready as never),
    )
  const build = vi
    .spyOn(metadataApi, 'managedOperation')
    .mockResolvedValue({ state: 'succeeded', runId: 'preview_1' } as never)
  const persisted = vi.fn()
  render(
    <PlanningLevelEditor
      initial={initial}
      data={data()}
      editing
      busy={false}
      savedVersion={1}
      base="/models/m/revisions/r"
      onSave={vi.fn()}
      onPersisted={persisted}
      onCancel={vi.fn()}
    />,
  )
  await user.clear(screen.getByLabelText('Name'))
  await user.type(screen.getByLabelText('Name'), 'Changed')
  await user.click(screen.getByRole('button', { name: 'Save' }))
  await waitFor(() => expect(persisted).toHaveBeenCalled())
  expect(build).toHaveBeenCalledWith('/models/m/revisions/r/planning-levels/editor/draft/build', {
    definition: expect.objectContaining({ name: 'Changed' }),
    expectedVersion: 1,
  })
  expect(request).toHaveBeenCalledWith(
    '/models/m/revisions/r/planning-levels/editor/draft/commit',
    'POST',
    { definition: expect.objectContaining({ name: 'Changed' }), runId: 'preview_1' },
  )
  expect(persisted).toHaveBeenLastCalledWith(expect.objectContaining({ name: 'Changed' }), 2, true)
})

it('Preview builds unsaved metadata once, and Save commits the same built generation', async () => {
  const user = userEvent.setup()
  const document = { ...initial, name: 'Changed', version: 2 }
  const request = vi
    .spyOn(metadataApi, 'request')
    .mockImplementation(async (path) =>
      path.endsWith('/draft/commit')
        ? (document as never)
        : path.endsWith('/preview')
          ? ({ ...ready, state: 'preview' } as never)
          : (ready as never),
    )
  const build = vi
    .spyOn(metadataApi, 'managedOperation')
    .mockResolvedValue({ state: 'succeeded', runId: 'preview_1' } as never)
  const persisted = vi.fn()
  render(
    <PlanningLevelEditor
      initial={initial}
      data={data()}
      editing
      busy={false}
      savedVersion={1}
      base="/models/m/revisions/r"
      onSave={vi.fn()}
      onPersisted={persisted}
      onCancel={vi.fn()}
    />,
  )
  await user.clear(screen.getByLabelText('Name'))
  await user.type(screen.getByLabelText('Name'), 'Changed')
  await user.click(screen.getByRole('button', { name: 'Preview' }))
  await screen.findByText('P1')
  expect(persisted).not.toHaveBeenCalled()
  expect(request.mock.calls.some((call) => call[1] === 'POST' || call[1] === 'PUT')).toBe(false)
  await user.click(screen.getByRole('button', { name: 'Preview' }))
  await screen.findByText('P1')
  expect(build).toHaveBeenCalledTimes(1)
  await user.click(screen.getByRole('button', { name: 'Save' }))
  await waitFor(() => expect(persisted).toHaveBeenCalled())
  expect(build).toHaveBeenCalledTimes(1)
})

it('Cancel leaves a built preview uncommitted', async () => {
  const user = userEvent.setup()
  const request = vi.spyOn(metadataApi, 'request').mockResolvedValue({ ...ready, state: 'preview' })
  vi.spyOn(metadataApi, 'managedOperation').mockResolvedValue({
    state: 'succeeded',
    runId: 'preview_1',
  } as never)
  const cancel = vi.fn()
  render(
    <PlanningLevelEditor
      initial={initial}
      data={data()}
      editing={false}
      busy={false}
      base="/models/m/revisions/r"
      onSave={vi.fn()}
      onCancel={cancel}
    />,
  )
  await user.click(screen.getByRole('button', { name: 'Preview' }))
  await screen.findByText('P1')
  await user.click(screen.getByRole('button', { name: 'Cancel' }))
  expect(cancel).toHaveBeenCalledTimes(1)
  expect(request.mock.calls.some((call) => call[1] === 'POST' || call[1] === 'PUT')).toBe(false)
})
