import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import { MeasureEditor, defaultAggregation } from '../src/metadata/editors/MeasureEditor'
import { referencedResultType } from '../src/metadata/measureTypes'
import { emptySnapshot, type Measure } from '../src/metadata/types'
function setup(resolved = false) {
  const data = emptySnapshot()
  const envelope = { modelId: 'm', revisionId: 'r', version: 1, createdAt: '', updatedAt: '' }
  data.sources = [
    {
      ...envelope,
      objectId: 'source',
      kind: 'source',
      name: 'Sales',
      description: '',
      definition: {
        tableName: 'Sales',
        inScope: true,
        keyColumns: [],
        columns: [
          {
            columnId: 'decimal',
            name: 'decimal_qty',
            physicalType: 'Decimal(18, 3)',
            nullable: false,
          },
          { columnId: 'integer', name: 'integer_qty', physicalType: 'Int64', nullable: false },
          { columnId: 'float', name: 'float_qty', physicalType: 'Float32', nullable: true },
        ],
      },
    },
  ]
  data.transactions = [
    {
      ...envelope,
      objectId: 'tx',
      kind: 'transaction',
      name: 'Sales',
      displayName: 'Sales',
      description: '',
      definition: {
        sourceId: 'source',
        planningLevelId: 'pt',
        mappingMode: 'direct',
        keyMappings: [{ sourceColumnId: 'integer', grainColumnId: 'key' }],
        expectedRowsPerGrain: 'many',
        unmatchedGrainPolicy: 'reject',
      },
    },
  ]
  if (resolved) {
    data.transactions[0].definition.mappingMode = 'resolve_multi_grain'
    data.transactions[0].definition.keyMappings = []
    data.transactions[0].definition.resolution = {
      axes: [{ grainColumnId: 'key', allowAll: true, rules: [] }],
      values: [{ sourceColumnId: 'decimal', mode: 'split' }],
      orderColumnIds: ['integer'],
      partitionColumnIds: [],
      passthroughColumnIds: [],
      overlapPolicy: 'last_applied_wins',
    }
  }
  const initial: Measure = {
    objectId: 'measure',
    kind: 'measure',
    name: 'SQTY',
    displayName: 'SQTY',
    description: '',
    business: { unit: 'EA', aliases: ['Existing alias'] },
    definition: {
      measureType: 'referenced',
      transactionId: 'tx',
      valueColumnId: 'decimal',
      nativePlanningLevelId: 'pt',
      resultType: { type: 'decimal', precision: 38, scale: 2, nullable: true },
      aggregation: defaultAggregation,
      allocation: { mode: 'none' },
      missingTransactionValue: 'null',
      sourceNullPolicy: 'reject',
      editable: false,
    },
  }
  const save = vi.fn(async () => {})
  render(
    <MeasureEditor
      initial={initial}
      data={data}
      editing={true}
      busy={false}
      onSave={save}
      onCancel={() => {}}
    />,
  )
  return { save, user: userEvent.setup() }
}
it('places transaction/value below measure type, hides numeric types and moves disaggregation last', async () => {
  const { save, user } = setup()
  const type = screen.getByLabelText('Measure type'),
    tx = screen.getByLabelText('Transaction'),
    column = screen.getByLabelText('Value column')
  expect(type.compareDocumentPosition(tx) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  expect(tx.compareDocumentPosition(column) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  expect(screen.queryByLabelText('Unit')).not.toBeInTheDocument()
  expect(screen.queryByLabelText('Aliases (comma separated)')).not.toBeInTheDocument()
  expect(screen.queryByLabelText('Data type')).not.toBeInTheDocument()
  expect(screen.queryByLabelText('Numeric type')).not.toBeInTheDocument()
  expect(screen.queryByLabelText('Precision')).not.toBeInTheDocument()
  expect(screen.queryByLabelText('Scale')).not.toBeInTheDocument()
  const aggregation = screen.getByRole('heading', { name: 'Aggregation policies' }),
    disaggregation = screen.getByRole('heading', { name: 'Dis-Aggregation policies' })
  expect(
    aggregation.compareDocumentPosition(disaggregation) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy()
  expect(screen.queryByLabelText('Allocation')).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Save metadata' }))
  expect(save).toHaveBeenCalledWith(
    expect.objectContaining({
      business: { unit: 'EA', aliases: ['Existing alias'] },
      definition: expect.objectContaining({
        resultType: { type: 'number', nullable: true },
      }),
    }),
  )
})
it('uses Float64 for integer and float source columns without displaying a type', async () => {
  const { save, user } = setup()
  await user.selectOptions(screen.getByLabelText('Value column'), 'integer')
  expect(screen.queryByLabelText('Data type')).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Save metadata' }))
  expect(save).toHaveBeenLastCalledWith(
    expect.objectContaining({
      definition: expect.objectContaining({ resultType: { type: 'number', nullable: true } }),
    }),
  )
  await user.selectOptions(screen.getByLabelText('Value column'), 'float')
  expect(screen.queryByLabelText('Data type')).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Save metadata' }))
  expect(save).toHaveBeenLastCalledWith(
    expect.objectContaining({
      definition: expect.objectContaining({ resultType: { type: 'number', nullable: true } }),
    }),
  )
})
it('hides decimal precision/scale for calculated measures too', async () => {
  const { user } = setup()
  await user.selectOptions(screen.getByLabelText('Measure type'), 'calculated')
  expect(screen.queryByLabelText('Numeric type')).not.toBeInTheDocument()
  expect(screen.queryByLabelText('Precision')).not.toBeInTheDocument()
  expect(screen.queryByLabelText('Scale')).not.toBeInTheDocument()
  expect(screen.queryByLabelText('Dis-aggregation policy')).not.toBeInTheDocument()
})
it.each([
  ['LowCardinality(Nullable(Decimal64(4)))', 18, 4],
  ['Decimal32(2)', 9, 2],
  ['Decimal128(8)', 38, 8],
  ['Decimal256(16)', 76, 16],
] as const)('promotes decimal source %s to Float64', (type, _precision, _scale) => {
  expect(referencedResultType(type, true)).toEqual({
    type: 'number',
    nullable: true,
  })
})

it('adds weighted by self without a basis measure and clears the separate weighted-basis field', async () => {
  const { save, user } = setup()
  await user.selectOptions(screen.getByLabelText('Dis-aggregation policy'), 'weighted')
  expect(screen.getByLabelText('Dis-aggregation basis measure')).toBeInTheDocument()
  await user.selectOptions(screen.getByLabelText('Dis-aggregation policy'), 'weighted_by_self')
  expect(screen.queryByLabelText('Dis-aggregation basis measure')).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Save metadata' }))
  expect(save).toHaveBeenCalledWith(
    expect.objectContaining({
      business: { unit: 'EA', aliases: ['Existing alias'] },
      definition: expect.objectContaining({ allocation: { mode: 'weighted_by_self' } }),
    }),
  )
})

it('offers only explicitly distributed values from a resolved transaction', async () => {
  const { save, user } = setup(true)
  const value = screen.getByLabelText('Value column')
  expect(value.querySelector('option[value="decimal"]')).toHaveTextContent('decimal_qty')
  expect(value.querySelector('option[value="integer"]')).toBeNull()
  expect(value.querySelector('option[value="float"]')).toBeNull()
  await user.click(screen.getByRole('button', { name: 'Save metadata' }))
  expect(save).toHaveBeenCalledWith(
    expect.objectContaining({
      definition: expect.objectContaining({ resultType: { type: 'number', nullable: true } }),
    }),
  )
})
