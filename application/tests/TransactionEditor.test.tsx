import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import { TransactionEditor } from '../src/metadata/editors/TransactionEditor'
import { emptySnapshot, type Transaction } from '../src/metadata/types'
it.each(['many', 'zero_or_one'] as const)(
  'hides row cardinality and preserves the stored %s declaration while editing',
  async (expectedRowsPerGrain) => {
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
        editing={true}
        busy={false}
        onSave={save}
        onCancel={() => {}}
      />,
    )
    expect(screen.queryByLabelText('Expected rows per grain')).not.toBeInTheDocument()
    expect(screen.getByText(/aggregated by these native planning-level keys/)).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Save metadata' }))
    expect(save).toHaveBeenCalledWith(
      expect.objectContaining({ definition: expect.objectContaining({ expectedRowsPerGrain }) }),
    )
  },
)
