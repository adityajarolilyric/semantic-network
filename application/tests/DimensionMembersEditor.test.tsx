import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import { DimensionMembersEditor } from '../src/metadata/editors/DimensionMembersEditor'
import { emptySnapshot, type DimensionMembers } from '../src/metadata/types'
const initial: DimensionMembers = {
  objectId: 'd_product',
  kind: 'dimensionMembers',
  name: 'Product',
  displayName: 'Legacy label',
  description: '',
  isTimeDim: false,
  definition: {
    sourceId: 'source_1',
    columns: [],
    keyColumnIds: [],
    labelColumnId: '',
    attributeColumnIds: [],
    time: null,
  },
}
function setup(input: DimensionMembers = initial) {
  const data = emptySnapshot()
  data.sources.push({
    objectId: 'source_1',
    kind: 'source',
    name: 'Products',
    description: '',
    definition: {
      tableName: 'Products',
      inScope: true,
      keyColumns: [],
      columns: [
        { columnId: 'col_1', name: 'product_id', physicalType: 'String', nullable: false },
        { columnId: 'col_2', name: 'product_name', physicalType: 'String', nullable: true },
      ],
    },
    modelId: 'm',
    revisionId: 'r',
    version: 1,
    createdAt: '',
    updatedAt: '',
  })
  const save = vi.fn(async () => {})
  const cancel = vi.fn()
  render(
    <DimensionMembersEditor
      initial={input}
      data={data}
      editing={false}
      onSave={save}
      onCancel={cancel}
      busy={false}
    />,
  )
  return { save, cancel, user: userEvent.setup() }
}
it('uses one column grid, exposes keys automatically, and saves user column labels', async () => {
  const { save, user } = setup()
  await user.selectOptions(screen.getByLabelText('Managed table'), 'source_1')
  expect(screen.queryByLabelText('Object ID')).not.toBeInTheDocument()
  expect(screen.queryByRole('textbox', { name: 'Display name' })).not.toBeInTheDocument()
  expect(screen.getByLabelText('Display name for product_id')).toHaveValue('product_id')
  expect(screen.getByLabelText('Key product_name')).toBeDisabled()
  await user.click(screen.getByLabelText('Key product_id'))
  expect(screen.getByLabelText('Expose product_id')).toBeChecked()
  expect(screen.getByLabelText('Expose product_id')).toBeDisabled()
  await user.clear(screen.getByLabelText('Display name for product_id'))
  await user.type(screen.getByLabelText('Display name for product_id'), 'Product ID')
  expect(screen.getByLabelText('Expose product_name')).toBeChecked()
  await user.click(screen.getByRole('button', { name: 'Save metadata' }))
  expect(save).toHaveBeenCalledWith(
    expect.objectContaining({
      name: 'Product',
      displayName: 'Product',
      definition: expect.objectContaining({
        keyColumnIds: ['col_1'],
        attributeColumnIds: ['col_1', 'col_2'],
        columns: expect.arrayContaining([
          expect.objectContaining({ name: 'product_id', displayName: 'Product ID' }),
        ]),
      }),
    }),
  )
})
it('allows cancelling before keys are selected and makes removed keys exposure editable', async () => {
  const { cancel, user } = setup()
  await user.selectOptions(screen.getByLabelText('Managed table'), 'source_1')
  expect(screen.getByRole('button', { name: 'Save metadata' })).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Cancel' })).toBeEnabled()
  await user.click(screen.getByLabelText('Key product_id'))
  await user.click(screen.getByLabelText('Key product_id'))
  expect(screen.getByLabelText('Expose product_id')).toBeEnabled()
  await user.click(screen.getByRole('button', { name: 'Cancel' }))
  expect(cancel).toHaveBeenCalled()
})

it('defaults all columns to exposed and lets users deselect non-key attributes', async () => {
  const { save, user } = setup()
  await user.selectOptions(screen.getByLabelText('Managed table'), 'source_1')
  expect(screen.getByLabelText('Expose product_id')).toBeChecked()
  expect(screen.getByLabelText('Expose product_name')).toBeChecked()
  await user.click(screen.getByLabelText('Key product_id'))
  await user.click(screen.getByLabelText('Expose product_name'))
  await user.click(screen.getByRole('button', { name: 'Save metadata' }))
  expect(save).toHaveBeenCalledWith(
    expect.objectContaining({
      definition: expect.objectContaining({ attributeColumnIds: ['col_1'] }),
    }),
  )
})

it('keeps saved attribute deselections when reopening a dimension', async () => {
  const saved: DimensionMembers = {
    ...initial,
    definition: {
      ...initial.definition,
      columns: [
        {
          columnId: 'col_1',
          sourceColumnId: 'col_1',
          name: 'product_id',
          displayName: 'Product ID',
        },
        {
          columnId: 'col_2',
          sourceColumnId: 'col_2',
          name: 'product_name',
          displayName: 'Product name',
        },
      ],
      keyColumnIds: ['col_1'],
      labelColumnId: 'col_1',
      attributeColumnIds: ['col_1'],
    },
  }
  const { save, user } = setup(saved)
  expect(screen.getByLabelText('Expose product_name')).not.toBeChecked()
  await user.click(screen.getByRole('button', { name: 'Save metadata' }))
  expect(save).toHaveBeenCalledWith(
    expect.objectContaining({
      definition: expect.objectContaining({ attributeColumnIds: ['col_1'] }),
    }),
  )
})
