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
    data.planningLevels = [
      {
        ...envelope,
        objectId: 'pl',
        kind: 'planningLevel',
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
          dimensionBindings: [],
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
        planningLevelId: 'pl',
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
    if (!editing) {
      expect(screen.getByRole('radio', { name: 'No Planning Level' })).toBeChecked()
      expect(screen.getByRole('tab', { name: /^Pivot Data$/ })).toBeVisible()
      expect(screen.getByRole('tab', { name: /^Date Resolution$/ })).toBeVisible()
      expect(screen.queryByLabelText('Native planning level')).not.toBeInTheDocument()
      await userEvent.click(screen.getByRole('radio', { name: 'Map to Planning Level' }))
      await userEvent.selectOptions(screen.getByLabelText('P.product_id'), 'product_id')
    } else {
      expect(screen.getByRole('radio', { name: 'No Planning Level' })).toBeDisabled()
    }
    expect(screen.getByRole('radio', { name: 'Map to Planning Level' })).toBeChecked()
    expect(screen.getByText(/aggregated by these native planning-level keys/)).toBeVisible()
    expect(screen.getAllByRole('radio')).toHaveLength(2)
    expect(screen.queryByRole('radio', { name: 'Resolve Multi Grain' })).not.toBeInTheDocument()
    await userEvent.click(screen.getByLabelText('Resolve multi-grain data'))
    expect(screen.getByRole('radio', { name: 'Map to Planning Level' })).toBeChecked()
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
        }),
      }),
    )
  },
)
