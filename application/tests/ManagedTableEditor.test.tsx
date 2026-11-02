import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { ManagedTableEditor } from '../src/metadata/editors/ManagedTableEditor'
import { metadataApi } from '../src/metadata/api'
import type { ManagedTable, Source } from '../src/metadata/types'

const saved: ManagedTable = {
  sourceId: 's',
  version: 3,
  definitionHash: 'hash',
  definition: {
    primaryKeyColumnIds: [],
    pipeline: [],
    columns: [{ columnId: 'id', name: 'product', physicalType: 'String', nullable: false }],
  },
}
const source: Source = {
  objectId: 's',
  kind: 'source',
  name: 'Products',
  description: '',
  managed: saved,
  definition: {
    tableName: 'Products',
    inScope: true,
    keyColumns: [],
    columns: saved.definition.columns,
  },
}
const preview = { columns: saved.definition.columns, rows: [['P1']] }
afterEach(() => vi.restoreAllMocks())
function setup() {
  render(
    <ManagedTableEditor
      source={source}
      base="/models/m/revisions/r"
      busy={false}
      onClose={() => {}}
      onSaved={vi.fn()}
    />,
  )
  return userEvent.setup()
}
it('previews the saved view without saving a clean configuration', async () => {
  const request = vi.spyOn(metadataApi, 'request').mockResolvedValue(preview)
  const save = vi.spyOn(metadataApi, 'saveManaged')
  const confirm = vi.spyOn(window, 'confirm')
  const user = setup()
  await user.click(screen.getByRole('button', { name: 'Preview' }))
  expect(request).toHaveBeenCalledWith('/models/m/revisions/r/managed-tables/s/preview')
  expect(save).not.toHaveBeenCalled()
  expect(confirm).not.toHaveBeenCalled()
  expect(screen.getByRole('cell', { name: 'P1' })).toBeVisible()
  expect(screen.queryByRole('button', { name: 'Refresh preview' })).not.toBeInTheDocument()
})
it('leaves unsaved configuration untouched when the preview prompt is declined', async () => {
  const request = vi.spyOn(metadataApi, 'request')
  const save = vi.spyOn(metadataApi, 'saveManaged')
  vi.spyOn(window, 'confirm').mockReturnValue(false)
  const user = setup()
  await user.click(screen.getByRole('checkbox', { name: 'product' }))
  await user.click(screen.getByRole('button', { name: 'Preview' }))
  expect(save).not.toHaveBeenCalled()
  expect(request).not.toHaveBeenCalled()
  expect(screen.getByRole('status')).toHaveTextContent('Unsaved changes.')
})
it('waits for save/build before preview and retains the returned version for another save', async () => {
  const next = {
    ...saved,
    version: 4,
    definition: { ...saved.definition, primaryKeyColumnIds: ['id'] },
  }
  const request = vi
    .spyOn(metadataApi, 'request')
    .mockImplementation(async (path) => (path.endsWith('/preview') ? preview : (next as never)))
  const save = vi.spyOn(metadataApi, 'saveManaged').mockResolvedValue(undefined)
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  const user = setup()
  await user.click(screen.getByRole('checkbox', { name: 'product' }))
  await user.click(screen.getByRole('button', { name: 'Preview' }))
  expect(save).toHaveBeenNthCalledWith(
    1,
    '/models/m/revisions/r/managed-tables/s',
    { primaryKeyColumnIds: ['id'], pipeline: [] },
    3,
  )
  expect(save.mock.invocationCallOrder[0]).toBeLessThan(request.mock.invocationCallOrder[1])
  expect(screen.getByRole('cell', { name: 'P1' })).toBeVisible()
  await user.click(screen.getByRole('button', { name: 'Save' }))
  expect(save.mock.calls[1][2]).toBe(4)
})
it('does not preview when the build fails', async () => {
  const request = vi.spyOn(metadataApi, 'request')
  vi.spyOn(metadataApi, 'saveManaged').mockRejectedValue(new Error('Duplicate pivot cells.'))
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  const user = setup()
  await user.click(screen.getByRole('checkbox', { name: 'product' }))
  await user.click(screen.getByRole('button', { name: 'Preview' }))
  expect(screen.getByRole('alert')).toHaveTextContent('Duplicate pivot cells.')
  expect(request).not.toHaveBeenCalled()
  expect(screen.queryByRole('table')).not.toBeInTheDocument()
})

it('keeps multiple pairs compact and preserves selections when switching, adding and removing', async () => {
  const user = userEvent.setup()
  render(
    <ManagedTableEditor
      source={source}
      base="/models/m/revisions/r"
      busy={false}
      onClose={() => {}}
      onSaved={() => {}}
    />,
  )
  await user.click(screen.getByRole('checkbox', { name: 'Pivot long-format columns' }))
  await user.selectOptions(screen.getByRole('combobox', { name: 'Attribute column' }), 'id')
  await user.selectOptions(screen.getByRole('combobox', { name: 'Value column' }), 'id')
  await user.click(screen.getByRole('button', { name: 'Add attribute / value pair' }))
  expect(screen.getAllByRole('tabpanel')).toHaveLength(1)
  expect(screen.getAllByRole('tab')).toHaveLength(2)
  expect(screen.getByRole('combobox', { name: 'Attribute column' })).toHaveValue('')
  await user.click(screen.getByRole('tab', { name: /Pair 1/ }))
  expect(screen.getByRole('combobox', { name: 'Attribute column' })).toHaveValue('id')
  expect(screen.getByRole('combobox', { name: 'Value column' })).toHaveValue('id')
  await user.keyboard('{End}')
  expect(screen.getByRole('tab', { name: /Pair 2/ })).toHaveAttribute('aria-selected', 'true')
  await user.click(screen.getByRole('button', { name: 'Remove pair' }))
  expect(screen.getAllByRole('tab')).toHaveLength(1)
  expect(screen.getByRole('combobox', { name: 'Attribute column' })).toHaveValue('id')
})

it('lets users browse pair tabs on read-only definitions without enabling editing', async () => {
  const pair = { pairId: 'pair1', attributeColumnId: 'id', valueColumnId: 'id', outputs: [] }
  const readonlySource = {
    ...source,
    managed: {
      ...saved,
      definition: {
        ...saved.definition,
        pipeline: [
          {
            stageId: 'stage1',
            type: 'pivot' as const,
            enabled: true,
            groupByColumnIds: [],
            pairs: [pair, { ...pair, pairId: 'pair2' }],
          },
        ],
      },
    },
  }
  const user = userEvent.setup()
  render(
    <ManagedTableEditor
      source={readonlySource}
      readOnly
      base="/models/m/revisions/r"
      busy={false}
      onClose={() => {}}
      onSaved={() => {}}
    />,
  )
  await user.click(screen.getByRole('tab', { name: /Pair 2/ }))
  expect(screen.getByRole('tab', { name: /Pair 2/ })).toHaveAttribute('aria-selected', 'true')
  expect(screen.getByRole('combobox', { name: 'Attribute column' })).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Add attribute / value pair' })).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Remove pair' })).toBeDisabled()
})

it('closes immediately when saved and confirms before discarding unsaved changes', async () => {
  const close = vi.fn()
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
  const user = userEvent.setup()
  render(
    <ManagedTableEditor
      source={source}
      base="/models/m/revisions/r"
      busy={false}
      onSaved={() => {}}
      onClose={close}
    />,
  )
  await user.click(screen.getByRole('button', { name: 'Close' }))
  expect(close).toHaveBeenCalledTimes(1)
  expect(confirm).not.toHaveBeenCalled()
  await user.click(screen.getByRole('checkbox', { name: 'product' }))
  await user.click(screen.getByRole('button', { name: 'Close' }))
  expect(close).toHaveBeenCalledTimes(1)
  confirm.mockReturnValue(true)
  await user.click(screen.getByRole('button', { name: 'Close' }))
  expect(close).toHaveBeenCalledTimes(2)
})
