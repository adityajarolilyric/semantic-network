import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import { TransactionEditor } from '../src/metadata/editors/TransactionEditor'
import { emptySnapshot, type Transaction } from '../src/metadata/types'
it.each([
  { expectedRowsPerGrain: 'many', editing: true },
  { expectedRowsPerGrain: 'zero_or_one', editing: true },
  { expectedRowsPerGrain: 'many', editing: false },
] as const)(
  'mapping switch preserves cardinality $expectedRowsPerGrain with editing=$editing',
  async ({ expectedRowsPerGrain, editing }) => {
    const data = emptySnapshot()
    const envelope = { modelId: 'm', revisionId: 'r', version: 1, createdAt: '', updatedAt: '' }
    data.sources = [
      {
        ...envelope,
        objectId: 's',
        kind: 'source',
        name: 'Sales',
        description: '',
        definition: {
          tableName: 'Sales',
          inScope: true,
          keyColumns: [],
          columns: [
            { columnId: 'product_id', name: 'product_id', physicalType: 'String', nullable: false },
          ],
        },
      },
    ]
    data.dimensions = [
      {
        ...envelope,
        objectId: 'pl',
        kind: 'dimension',
        name: 'P',
        displayName: 'P',
        description: '',
        definition: {
          population: { operation: 'source', sourceId: 's' },
          grain: {
            keyIds: ['product'],
            columns: [
              {
                grainColumnId: 'product',
                name: 'product_id',
                populationRef: { columnId: 'product_id' },
              },
            ],
          },
          memberBindings: [],
        },
      },
    ]
    const initial: Transaction = {
      objectId: 'tx',
      kind: 'transaction',
      name: 'Sales',
      displayName: 'Sales',
      description: '',
      definition: {
        sourceId: 's',
        dimensionId: 'pl',
        expectedRowsPerGrain,
        mappingMode: 'direct',
        unmatchedGrainPolicy: 'reject',
        keyMappings: [{ sourceColumnId: 'product_id', grainColumnId: 'product' }],
      },
    }
    const save = vi.fn(async () => {})
    render(
      <TransactionEditor
        initial={initial}
        data={data}
        editing={editing}
        busy={false}
        onSave={save}
        onCancel={() => {}}
      />,
    )
    expect(screen.getByText('Transformations', { exact: true })).toBeVisible()
    expect(screen.getByRole('region', { name: 'Grain Selection' })).toBeVisible()
    await userEvent.click(screen.getByText('Transformations', { exact: true }))
    expect(screen.getByRole('tab', { name: /^Filters$/ })).toHaveAttribute('aria-selected', 'true')
    expect(screen.queryByLabelText('Expected rows per grain')).not.toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: 'Transaction mapping' })).not.toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'Use existing population' })).toBeChecked()
    expect(screen.getByRole('radio', { name: 'Rows from this transaction' })).toBeEnabled()
    expect(screen.getByRole('tab', { name: /^Pivot Data$/ })).toBeVisible()
    expect(screen.getByRole('tab', { name: /^Date Resolution$/ })).toBeVisible()
    await userEvent.click(screen.getByRole('tab', { name: /^Date Resolution$/ }))
    expect(screen.queryByLabelText('Resolve a date range using a calendar')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Start date column')).toBeVisible()
    expect(screen.getByLabelText('End date column')).toBeVisible()
    await userEvent.selectOptions(screen.getByLabelText('Start date column'), 'product_id')
    expect(screen.getByRole('button', { name: 'Clear date resolution' })).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Clear date resolution' }))
    expect(screen.getByRole('radio', { name: 'Use existing population' })).toBeChecked()
    expect(screen.getByText(/aggregated by these native dimension keys/)).toBeVisible()
    expect(screen.getAllByRole('radio')).toHaveLength(3)
    expect(screen.queryByRole('radio', { name: 'Resolve Multi Grain' })).not.toBeInTheDocument()
    await userEvent.click(screen.getByLabelText('Resolve multi-grain data'))
    expect(screen.getByRole('radio', { name: 'Use existing population' })).toBeChecked()
    expect(screen.queryByLabelText('P.product_id')).not.toBeInTheDocument()
    await userEvent.click(screen.getByLabelText('Resolve multi-grain data'))
    expect(screen.getByLabelText('P.product_id')).toBeVisible()
    await userEvent.selectOptions(screen.getByLabelText('P.product_id'), 'product_id')

    await userEvent.click(screen.getByRole('button', { name: 'Save metadata' }))
    expect(save).toHaveBeenCalledWith(
      expect.objectContaining({
        definition: expect.objectContaining({
          expectedRowsPerGrain,
          mappingMode: 'direct',
          resolution: null,
          calendarResolution: null,
        }),
      }),
    )
  },
)

function populationFixture() {
  const data = emptySnapshot()
  const envelope = { modelId: 'm', revisionId: 'r', version: 1, createdAt: '', updatedAt: '' }
  data.sources = [
    {
      ...envelope,
      objectId: 's',
      name: 'Forecast',
      kind: 'source',
      description: '',
      definition: {
        tableName: 'Forecast',
        inScope: true,
        keyColumns: [],
        columns: [{ columnId: 'pid', name: 'product_id', physicalType: 'String', nullable: false }],
      },
    },
  ]
  const transaction: Transaction = {
    objectId: 'forecast',
    name: 'Forecast',
    displayName: 'Forecast',
    kind: 'transaction',
    description: '',
    definition: {
      sourceId: 's',
      dimensionId: null,
      grainColumnIds: ['pid'],
      mappingMode: 'direct',
      keyMappings: [],
      expectedRowsPerGrain: 'many',
      unmatchedGrainPolicy: 'reject',
    },
  }
  return { data, transaction, envelope }
}

it('creates a reusable row population and Base in one save without nested forms', async () => {
  const { data, transaction } = populationFixture()
  const save = vi.fn(async () => {})
  const { container } = render(
    <TransactionEditor
      initial={transaction}
      data={data}
      editing={false}
      busy={false}
      revisionVersion={12}
      baseDimensionId={null}
      onSave={vi.fn()}
      onSavePopulation={save}
      onCancel={() => {}}
    />,
  )
  expect(screen.getByRole('radio', { name: 'Rows from this transaction' })).toBeChecked()
  expect(screen.getByLabelText('Use this population as Base')).toBeChecked()
  expect(container.querySelectorAll('form')).toHaveLength(1)
  await userEvent.click(screen.getByRole('button', { name: 'Save metadata' }))
  expect(save).toHaveBeenCalledWith(
    expect.objectContaining({
      expectedRevisionVersion: 12,
      useAsBase: true,
      population: expect.objectContaining({
        definition: expect.objectContaining({
          population: expect.objectContaining({
            operation: 'source',
            sourceId: 's',
            inputTransform: expect.objectContaining({ objectId: 'forecast' }),
          }),
          grain: expect.objectContaining({ keyIds: ['pid'] }),
        }),
      }),
      transaction: expect.objectContaining({
        definition: expect.objectContaining({
          keyMappings: [{ sourceColumnId: 'pid', grainColumnId: 'pid' }],
        }),
      }),
    }),
  )
})

it('recommends same-key populations and reuses them without another population definition', async () => {
  const { data, transaction, envelope } = populationFixture()
  data.dimensions = [
    {
      ...envelope,
      objectId: 'shared',
      kind: 'dimension',
      name: 'Active Products',
      displayName: 'Active Products',
      description: '',
      definition: {
        population: { operation: 'source', sourceId: 's' },
        grain: {
          keyIds: ['product'],
          columns: [
            { grainColumnId: 'product', name: 'product_id', populationRef: { columnId: 'pid' } },
          ],
        },
        memberBindings: [],
      },
    },
  ]
  const save = vi.fn(async () => {})
  render(
    <TransactionEditor
      initial={transaction}
      data={data}
      editing={false}
      busy={false}
      revisionVersion={3}
      baseDimensionId="shared"
      onSave={vi.fn()}
      onSavePopulation={save}
      onCancel={() => {}}
    />,
  )
  await userEvent.click(screen.getByRole('button', { name: 'Use Active Products' }))
  expect(screen.getByRole('radio', { name: 'Use existing population' })).toBeChecked()
  expect(screen.getByLabelText('Active Products.product_id')).toHaveValue('pid')
  await userEvent.click(screen.getByRole('button', { name: 'Save metadata' }))
  expect(save).toHaveBeenCalledWith(
    expect.objectContaining({
      population: null,
      transaction: expect.objectContaining({
        definition: expect.objectContaining({ dimensionId: 'shared' }),
      }),
    }),
  )
})

it('custom population fields are inside the transaction and retain key mappings for shared edits', async () => {
  const { data, transaction, envelope } = populationFixture()
  data.dimensions = [
    {
      ...envelope,
      objectId: 'shared',
      kind: 'dimension',
      name: 'Active Products',
      displayName: 'Active Products',
      description: '',
      definition: {
        population: { operation: 'source', sourceId: 's' },
        grain: {
          keyIds: ['product'],
          columns: [
            { grainColumnId: 'product', name: 'product_id', populationRef: { columnId: 'pid' } },
          ],
        },
        memberBindings: [],
      },
    },
  ]
  transaction.definition.dimensionId = 'shared'
  transaction.definition.keyMappings = [{ grainColumnId: 'product', sourceColumnId: 'pid' }]
  const save = vi.fn(async () => {})
  render(
    <TransactionEditor
      initial={transaction}
      data={data}
      editing
      busy={false}
      savedVersion={4}
      revisionVersion={8}
      baseDimensionId="shared"
      onSave={vi.fn()}
      onSavePopulation={save}
      onCancel={() => {}}
    />,
  )
  await userEvent.click(screen.getByRole('button', { name: 'Edit shared population' }))
  expect(screen.getByRole('radio', { name: 'Define population' })).toBeChecked()
  expect(screen.getByLabelText('Population name')).toHaveValue('Active Products')
  await userEvent.click(screen.getByRole('button', { name: 'Save metadata' }))
  expect(save).toHaveBeenCalledWith(
    expect.objectContaining({
      expectedVersion: 4,
      expectedPopulationVersion: 1,
      population: expect.objectContaining({ objectId: 'shared' }),
      transaction: expect.objectContaining({
        definition: expect.objectContaining({
          keyMappings: [{ grainColumnId: 'product', sourceColumnId: 'pid' }],
        }),
      }),
    }),
  )
})

it('does not infer key mappings from unrelated table-local column IDs', async () => {
  const { suggestPopulationMappings } = await import('../src/metadata/transactionPopulation')
  const population = {
    objectId: 'p',
    kind: 'dimension' as const,
    name: 'Products',
    displayName: 'Products',
    description: '',
    definition: {
      population: { operation: 'source' as const, sourceId: 'other' },
      grain: {
        keyIds: ['col_1'],
        columns: [
          { grainColumnId: 'col_1', name: 'product_id', populationRef: { columnId: 'col_1' } },
        ],
      },
      memberBindings: [],
    },
  }
  expect(
    suggestPopulationMappings(population, [
      { columnId: 'col_1', name: 'week_id', physicalType: 'String', nullable: false },
    ]),
  ).toEqual([{ grainColumnId: 'col_1', sourceColumnId: '' }])
  expect(
    suggestPopulationMappings(population, [
      { columnId: 'col_8', name: 'product_id', physicalType: 'String', nullable: false },
    ]),
  ).toEqual([{ grainColumnId: 'col_1', sourceColumnId: 'col_8' }])
})
