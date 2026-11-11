import { render, screen, within, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { DimensionEditor } from '../src/metadata/editors/DimensionEditor'
import { metadataApi } from '../src/metadata/api'
import { emptySnapshot, type Dimension } from '../src/metadata/types'

const initial: Dimension = {
  objectId: 'level',
  kind: 'dimension',
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
    memberBindings: [],
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

it('Save commits metadata without building a physical table', async () => {
  const build = vi.spyOn(metadataApi, 'managedOperation')
  const save = vi.fn(async () => {})
  render(
    <DimensionEditor
      initial={initial}
      data={data()}
      editing
      busy={false}
      base="/models/m/revisions/r"
      onSave={save}
      onCancel={vi.fn()}
    />,
  )
  const actions = screen.getByLabelText('Dimension actions')
  expect(
    within(actions)
      .getAllByRole('button')
      .map((b) => b.textContent),
  ).toEqual(['Cancel', 'Preview', 'View SQL', 'Save'])
  await userEvent.click(within(actions).getByRole('button', { name: 'Save' }))
  await waitFor(() => expect(save).toHaveBeenCalledWith(initial))
  expect(build).not.toHaveBeenCalled()
})

it('Preview reads the saved view without saving unchanged metadata', async () => {
  const request = vi.spyOn(metadataApi, 'request').mockResolvedValue(ready)
  const build = vi.spyOn(metadataApi, 'managedOperation')
  const save = vi.fn(async () => {})
  render(
    <DimensionEditor
      initial={initial}
      data={data()}
      editing
      busy={false}
      base="/models/m/revisions/r"
      onSave={save}
      onCancel={vi.fn()}
    />,
  )
  await userEvent.click(screen.getByRole('button', { name: 'Preview' }))
  await waitFor(() =>
    expect(request).toHaveBeenCalledWith('/models/m/revisions/r/dimensions/level/preview', 'GET'),
  )
  expect(await screen.findByRole('cell', { name: 'P1' })).toBeVisible()
  expect(save).not.toHaveBeenCalled()
  expect(build).not.toHaveBeenCalled()
})

it('Cancel does not persist the proposed Dimension', async () => {
  const save = vi.fn(async () => {}),
    cancel = vi.fn()
  render(
    <DimensionEditor
      initial={initial}
      data={data()}
      editing
      busy={false}
      onSave={save}
      onCancel={cancel}
    />,
  )
  await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
  expect(cancel).toHaveBeenCalledOnce()
  expect(save).not.toHaveBeenCalled()
})

it('shows creation-style key and attribute selection after changing to multiple tables', async () => {
  const save = vi.fn(async (_value: Dimension) => {})
  const snapshot = data()
  snapshot.sources[0].definition.columns.push({
    columnId: 'label',
    name: 'label',
    physicalType: 'String',
    nullable: false,
  })
  render(
    <DimensionEditor
      initial={initial}
      data={snapshot}
      editing
      busy={false}
      onSave={save}
      onCancel={vi.fn()}
    />,
  )
  const user = userEvent.setup()
  expect(screen.getByRole('radio', { name: 'From a Table' })).toBeChecked()
  await user.click(screen.getByRole('radio', { name: 'From Multiple Tables' }))
  await user.click(screen.getByLabelText('Include Products'))
  expect(screen.queryByText('Map existing fields to the new population')).not.toBeInTheDocument()
  const keys = screen.getByRole('group', { name: 'Keys from Products' })
  const attributes = screen.getByRole('group', { name: 'Population attributes from Products' })
  expect(within(keys).getByLabelText('product')).not.toBeChecked()
  await user.click(within(keys).getByLabelText('product'))
  await user.click(within(attributes).getByLabelText('label'))
  await user.click(screen.getByRole('button', { name: 'Save' }))
  expect(save).toHaveBeenCalledOnce()
  const saved = save.mock.calls[0][0] as unknown as Dimension
  expect(saved.objectId).toBe(initial.objectId)
  expect(saved.definition.grain.keyIds).toEqual(['product'])
  expect(saved.definition.grain.columns[0].grainColumnId).toBe('product')
  expect(saved.definition.population.operation).toBe('cartesian_product')
  if (saved.definition.population.operation === 'cartesian_product') {
    expect(saved.definition.population.factors[0].selectedColumnIds).toEqual(['product', 'label'])
    expect(saved.definition.populationAttributes?.[0].populationRef.columnId).toBe('label')
    expect(saved.definition.grain.columns[0].populationRef.factorId).toBe(
      saved.definition.population.factors[0].factorId,
    )
  }
  expect(initial.definition.population.operation).toBe('source')
})

it('shows creation-style parent selections when changing an edited Dimension strategy', async () => {
  const snapshot = data()
  snapshot.dimensions = [
    {
      ...initial,
      objectId: 'parent',
      name: 'Parent',
      modelId: 'm',
      revisionId: 'r',
      version: 1,
      createdAt: '',
      updatedAt: '',
    },
  ]
  const save = vi.fn(async (_value: Dimension) => {})
  render(
    <DimensionEditor
      initial={initial}
      data={snapshot}
      editing
      busy={false}
      onSave={save}
      onCancel={vi.fn()}
    />,
  )
  const user = userEvent.setup()
  await user.click(screen.getByRole('radio', { name: 'From Existing Dimension' }))
  await user.selectOptions(screen.getByLabelText('Parent Dimension'), 'parent')
  expect(screen.queryByText('Map existing fields to the new population')).not.toBeInTheDocument()
  await user.click(
    within(screen.getByRole('group', { name: 'Select keys from Parent keys' })).getByLabelText(
      'product',
    ),
  )
  await user.click(screen.getByRole('button', { name: 'Save' }))
  expect(save).toHaveBeenCalledWith(
    expect.objectContaining({
      objectId: 'level',
      definition: expect.objectContaining({
        population: { operation: 'distinct_projection', sourceDimensionId: 'parent' },
        grain: {
          keyIds: ['product'],
          columns: [
            {
              grainColumnId: 'product',
              name: 'product',
              populationRef: { sourceGrainColumnId: 'product' },
            },
          ],
        },
      }),
    }),
  )
})

it('asks to save changed metadata before reading the saved view', async () => {
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
  const request = vi.spyOn(metadataApi, 'request').mockResolvedValue(ready)
  const saveForPreview = vi.fn(async () => {})
  render(
    <DimensionEditor
      initial={initial}
      data={data()}
      editing
      busy={false}
      base="/models/m/revisions/r"
      onSave={vi.fn()}
      onSaveForPreview={saveForPreview}
      onCancel={vi.fn()}
    />,
  )
  await userEvent.clear(screen.getByLabelText('Name'))
  await userEvent.type(screen.getByLabelText('Name'), 'Changed Product')
  await userEvent.click(screen.getByRole('button', { name: 'Preview' }))
  expect(confirm).toHaveBeenCalledOnce()
  await waitFor(() => expect(saveForPreview).toHaveBeenCalledOnce())
  expect(request).toHaveBeenCalledWith('/models/m/revisions/r/dimensions/level/preview', 'GET')
  expect(saveForPreview.mock.invocationCallOrder[0]).toBeLessThan(
    request.mock.invocationCallOrder.at(-1)!,
  )
})

it('does not preview unsaved changes when saving is declined', async () => {
  vi.spyOn(window, 'confirm').mockReturnValue(false)
  const request = vi.spyOn(metadataApi, 'request').mockResolvedValue(ready)
  const saveForPreview = vi.fn(async () => {})
  render(
    <DimensionEditor
      initial={initial}
      data={data()}
      editing
      busy={false}
      base="/models/m/revisions/r"
      onSave={vi.fn()}
      onSaveForPreview={saveForPreview}
      onCancel={vi.fn()}
    />,
  )
  await userEvent.clear(screen.getByLabelText('Name'))
  await userEvent.type(screen.getByLabelText('Name'), 'Changed Product')
  const calls = request.mock.calls.length
  await userEvent.click(screen.getByRole('button', { name: 'Preview' }))
  expect(saveForPreview).not.toHaveBeenCalled()
  expect(request.mock.calls.length).toBe(calls)
})
