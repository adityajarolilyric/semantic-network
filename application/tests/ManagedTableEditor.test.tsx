import { render, screen, within } from '@testing-library/react'
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

it('derives row-expansion output keys before Save and hides consumed input columns', async () => {
  const user = userEvent.setup()
  const names = [
    'product_attribute',
    'product_value',
    'location_attribute',
    'location_value',
    'week',
    'forecast_qty',
  ]
  const rawColumns = names.map((name, index) => ({
    columnId: `col_${index + 1}`,
    name,
    physicalType: index === 5 ? 'Int64' : 'String',
    nullable: false,
  }))
  const effective = [
    rawColumns[4],
    rawColumns[5],
    { columnId: 'pv_brand', name: 'brand', physicalType: 'String', nullable: true },
    { columnId: 'pv_zone', name: 'zone', physicalType: 'String', nullable: true },
  ]
  const raw = {
    ...source,
    managed: {
      ...saved,
      definition: { primaryKeyColumnIds: [], pipeline: [], columns: rawColumns },
    },
    definition: { ...source.definition, columns: rawColumns },
  }
  let submitted: unknown
  const save = vi.spyOn(metadataApi, 'saveManaged').mockImplementation(async (_path, body) => {
    submitted = body
  })
  const request = vi.spyOn(metadataApi, 'request').mockImplementation(async (path) => {
    if (path.endsWith('/schema'))
      return {
        columns: effective,
        detection: {
          resolvedMode: 'expand_rows',
          state: 'detected',
          reason: 'Each source row contains all selected attributes.',
        },
      } as never
    if (path.includes('/attribute-values'))
      return {
        values: [path.includes('columnId=col_1') ? 'brand' : 'zone'],
        nextOffset: null,
      } as never
    return {
      ...saved,
      version: 4,
      definition: { ...(submitted as object), columns: effective },
    } as never
  })
  render(
    <ManagedTableEditor
      source={raw}
      base="/models/m/revisions/r"
      busy={false}
      onSaved={vi.fn()}
      onClose={vi.fn()}
    />,
  )
  await user.click(screen.getByRole('checkbox', { name: 'Pivot long-format columns' }))
  expect(screen.queryByLabelText('Pivot mode')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /advanced interpretation/i })).not.toBeInTheDocument()
  const retained = screen.getByRole('group', { name: /Retain.*columns/ })
  await user.click(within(retained).getByLabelText('week'))
  await user.click(within(retained).getByLabelText('forecast_qty'))
  await user.selectOptions(screen.getByLabelText('Attribute column'), 'col_1')
  await user.selectOptions(screen.getByLabelText('Value column'), 'col_2')
  await user.click(screen.getByRole('button', { name: 'Discover' }))
  await user.click(screen.getByRole('button', { name: 'Add attribute / value pair' }))
  await user.selectOptions(screen.getByLabelText('Attribute column'), 'col_3')
  await user.selectOptions(screen.getByLabelText('Value column'), 'col_4')
  await user.click(screen.getByRole('button', { name: 'Discover' }))
  const keys = screen.getByRole('group', {
    name: 'Optional managed output primary key',
  })
  const brand = await within(keys).findByRole('checkbox', { name: 'brand' })
  expect(brand).toBeEnabled()
  expect(within(keys).getByRole('checkbox', { name: 'zone' })).toBeVisible()
  expect(within(keys).queryByLabelText('product_value')).not.toBeInTheDocument()
  expect(within(keys).queryByLabelText('location_value')).not.toBeInTheDocument()
  expect(within(retained).queryByLabelText('product_attribute')).not.toBeInTheDocument()
  expect(within(retained).queryByLabelText('product_value')).not.toBeInTheDocument()
  await user.click(brand)
  await user.click(within(keys).getByLabelText('zone'))
  await user.click(within(keys).getByLabelText('week'))
  expect(save).not.toHaveBeenCalled()
  expect(
    request.mock.calls.some(([path, method]) => path.endsWith('/schema') && method === 'POST'),
  ).toBe(true)
  await user.click(screen.getByRole('button', { name: 'Save' }))
  expect(save).toHaveBeenCalledWith(
    '/models/m/revisions/r/managed-tables/s',
    expect.objectContaining({
      primaryKeyColumnIds: ['pv_brand', 'pv_zone', 'col_5'],
      pipeline: [
        expect.objectContaining({ recordKeyColumnIds: [], groupByColumnIds: ['col_5', 'col_6'] }),
      ],
    }),
    3,
  )
})

it('keeps legacy saved pivots in merge mode and reports schema analysis errors without saving', async () => {
  const pivot = {
    ...source,
    managed: {
      ...saved,
      definition: {
        ...saved.definition,
        pipeline: [
          {
            stageId: 'stage',
            type: 'pivot' as const,
            enabled: true,
            groupByColumnIds: ['id'],
            pairs: [
              {
                pairId: 'pair',
                attributeColumnId: 'id',
                valueColumnId: 'id',
                outputs: [{ attributeValue: 'name', name: 'name' }],
              },
            ],
          },
        ],
      },
    },
  }
  vi.spyOn(metadataApi, 'request').mockRejectedValue(
    new Error('Output columns have conflicting names.'),
  )
  const save = vi.spyOn(metadataApi, 'saveManaged')
  render(
    <ManagedTableEditor
      source={pivot}
      base="/models/m/revisions/r"
      busy={false}
      onSaved={vi.fn()}
      onClose={vi.fn()}
    />,
  )
  expect(screen.queryByLabelText('Pivot mode')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /advanced interpretation/i })).not.toBeInTheDocument()
  expect(await screen.findByRole('alert')).toHaveTextContent('conflicting names')
  expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
  expect(save).not.toHaveBeenCalled()
})

it('adopts explicit keys only after editing a legacy pivot and can return to value grouping', async () => {
  const rawColumns = ['customer', 'attribute', 'value'].map((name, index) => ({
    columnId: `c${index}`,
    name,
    physicalType: 'String',
    nullable: false,
  }))
  const legacy = {
    ...source,
    definition: { ...source.definition, columns: rawColumns },
    managed: {
      ...saved,
      definition: {
        primaryKeyColumnIds: [],
        columns: rawColumns,
        pipeline: [
          {
            stageId: 'saved_stage',
            type: 'pivot' as const,
            enabled: true,
            mode: 'auto' as const,
            resolvedMode: 'expand_rows' as const,
            groupByColumnIds: ['c0'],
            pairs: [
              {
                pairId: 'saved_pair',
                attributeColumnId: 'c1',
                valueColumnId: 'c2',
                outputs: [{ attributeValue: 'Channel', name: 'Channel' }],
              },
            ],
          },
        ],
      },
    },
  }
  const request = vi
    .spyOn(metadataApi, 'request')
    .mockResolvedValue({ columns: rawColumns } as never)
  render(
    <ManagedTableEditor
      source={legacy}
      base="/models/m/revisions/r"
      busy={false}
      onSaved={vi.fn()}
      onClose={vi.fn()}
    />,
  )
  await screen.findByRole('button', { name: 'Save' })
  expect(
    screen.getByText('Saved grouping is preserved until you edit the pivot configuration.'),
  ).toBeVisible()
  const recordKeys = screen.getByRole('group', {
    name: 'Record keys for separate attribute rows (optional)',
  })
  const user = userEvent.setup()
  await user.click(within(recordKeys).getByLabelText('customer'))
  expect(
    screen.getByText(
      'Rows sharing these keys are combined. Other retained columns must have one value per record.',
    ),
  ).toBeVisible()
  await user.click(within(recordKeys).getByLabelText('customer'))
  expect(
    screen.getByText(
      'Pair values and retained columns are grouped automatically. Identical records collapse.',
    ),
  ).toBeVisible()
  await vi.waitFor(() => {
    const body = request.mock.calls.filter(([path]) => path.endsWith('/schema')).at(-1)?.[2] as
      ManagedTable['definition'] | undefined
    expect(body?.pipeline[0].recordKeyColumnIds).toEqual([])
    expect(body?.pipeline[0].mode).toBeUndefined()
    expect(body?.pipeline[0].resolvedMode).toBeUndefined()
  })
  expect(legacy.managed.definition.pipeline[0].mode).toBe('auto')
})
