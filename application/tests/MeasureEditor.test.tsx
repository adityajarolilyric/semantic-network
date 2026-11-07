import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import { MeasureEditor, defaultAggregation } from '../src/metadata/editors/MeasureEditor'
import { referencedResultType } from '../src/metadata/measureTypes'
import { emptySnapshot, inputOf, type Measure } from '../src/metadata/types'
function setup(
  resolved = false,
  baseLevelId?: string,
  withReferences = false,
  physicalType?: string,
  standalone = false,
  otherNative = false,
) {
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
  if (physicalType) data.sources[0].definition.columns[0].physicalType = physicalType
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
        dimensionId: 'pt',
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
  if (standalone) {
    data.transactions[0].definition.dimensionId = null
    data.transactions[0].definition.grainColumnIds = ['integer']
    data.transactions[0].effectiveGrainId = 'tg_sales'
  }
  if (baseLevelId) {
    data.dimensions = ['pt', 'other'].map((objectId) => ({
      ...envelope,
      objectId,
      kind: 'dimension',
      name: objectId,
      displayName: objectId,
      description: '',
      definition: {
        population: { operation: 'source', sourceId: 'source' },
        grain: {
          keyIds: ['key'],
          columns: [{ grainColumnId: 'key', name: 'key', populationRef: { columnId: 'integer' } }],
        },
        memberBindings: [],
      },
    }))
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
      nativeGrainId: 'pt',
      resultType: { type: 'decimal', precision: 38, scale: 2, nullable: true },
      aggregation: defaultAggregation,
      allocation: { mode: 'none' },
      missingTransactionValue: 'null',
      sourceNullPolicy: 'preserve',
      editable: false,
    },
  }
  if (withReferences)
    data.measures = [
      { ...initial, ...envelope, objectId: 'forecast', name: 'FQTY', displayName: 'FQTY' },
    ]
  if (otherNative)
    data.transactions.push({
      ...data.transactions[0],
      objectId: 'other-tx',
      name: 'Other transaction',
      definition: { ...data.transactions[0].definition, dimensionId: 'other' },
    })
  const save = vi.fn(async () => {})
  render(
    <MeasureEditor
      initial={inputOf(initial)}
      baseLevelId={baseLevelId}
      data={data}
      editing={true}
      busy={false}
      onSave={save}
      onCancel={() => {}}
    />,
  )
  return { save, user: userEvent.setup() }
}
it.each([false, true])(
  'shows the referenced output grain after editor serialization (standalone=%s)',
  (standalone) => {
    setup(false, 'pt', false, undefined, standalone)
    const output = screen.getByRole('group', { name: 'Output grain' })
    expect(output).toHaveTextContent(standalone ? 'Sales · Transaction grain' : 'pt')
    expect(output).not.toHaveTextContent('Choose a transaction')
  },
)
it('places transaction/value below measure type, hides numeric types and moves disaggregation last', async () => {
  const { save, user } = setup()
  const type = screen.getByRole('group', { name: 'Measure type' }),
    tx = screen.getByLabelText('Transaction'),
    column = screen.getByLabelText('Value column')
  expect(type.compareDocumentPosition(tx) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  expect(tx.compareDocumentPosition(column) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  expect(screen.queryByLabelText('Source null policy')).not.toBeInTheDocument()
  expect(screen.getByRole('radio', { name: 'Reference' })).toBeChecked()
  expect(screen.getByRole('radio', { name: 'Calculated' })).not.toBeChecked()
  expect(screen.getByRole('group', { name: 'Output grain' })).toBeVisible()
  expect(
    column.compareDocumentPosition(screen.getByRole('group', { name: 'Output grain' })) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy()
  expect(
    screen
      .getByLabelText('Editable measure')
      .compareDocumentPosition(screen.getByLabelText('Missing transaction value')) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy()
  expect(screen.queryByLabelText('Unit')).not.toBeInTheDocument()
  expect(screen.queryByLabelText('Aliases (comma separated)')).not.toBeInTheDocument()
  expect(screen.queryByLabelText('Data type')).not.toBeInTheDocument()
  expect(screen.getByLabelText('Output grain')).toBeVisible()
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
  await user.click(screen.getByRole('radio', { name: 'Calculated' }))
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

it('defaults new reference and CalculateFirst definitions to Weighted by self', async () => {
  const { user } = setup()
  // Existing saved choices remain visible until a new definition is selected.
  expect(screen.getByLabelText('Dis-aggregation policy')).toHaveValue('none')
  await user.click(screen.getByRole('radio', { name: 'Calculated' }))
  await user.click(screen.getByRole('radio', { name: 'Calculate first' }))
  expect(screen.getByLabelText('Dis-aggregation policy')).toHaveValue('weighted_by_self')
  await user.click(screen.getByRole('radio', { name: 'Aggregate first' }))
  expect(screen.queryByLabelText('Dis-aggregation policy')).not.toBeInTheDocument()
  await user.click(screen.getByRole('radio', { name: 'Reference' }))
  expect(screen.getByLabelText('Dis-aggregation policy')).toHaveValue('weighted_by_self')
})

it('hides disaggregation for Base-native calculated measures and restores it at another output level', async () => {
  const { user } = setup(false, 'pt', false, undefined, false, true)
  expect(screen.getByLabelText('Dis-aggregation policy')).toBeVisible()
  await user.click(screen.getByRole('radio', { name: 'Calculated' }))
  await user.click(screen.getByRole('radio', { name: 'Calculate first' }))
  await user.selectOptions(screen.getByLabelText('Output grain'), 'dimension:pt')
  expect(
    screen.queryByRole('heading', { name: 'Dis-Aggregation policies' }),
  ).not.toBeInTheDocument()
  expect(screen.queryByLabelText('Dis-aggregation policy')).not.toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'Aggregation policies' })).toBeVisible()
  await user.selectOptions(screen.getByLabelText('Output grain'), 'dimension:other')
  expect(screen.getByLabelText('Dis-aggregation policy')).toHaveValue('weighted_by_self')
})

it.each(['mouse', 'keyboard'])(
  'inserts a measure at the formula caret and restores typing focus via %s',
  async (activation) => {
    const { user } = setup(false, undefined, true)
    await user.click(screen.getByRole('radio', { name: 'Calculated' }))
    const formula = screen.getByLabelText('Formula') as HTMLTextAreaElement
    await user.type(formula, 'ABS( + 2)')
    formula.setSelectionRange(4, 4)
    const measure = screen.getByRole('button', { name: 'FQTY' })
    if (activation === 'mouse') await user.click(measure)
    else {
      measure.focus()
      await user.keyboard('{Enter}')
    }
    expect(formula).toHaveValue('ABS([FQTY] + 2)')
    expect(formula).toHaveFocus()
    expect(formula.selectionStart).toBe(10)
    expect(formula.selectionEnd).toBe(10)
    await user.keyboard('-1')
    expect(formula).toHaveValue('ABS([FQTY]-1 + 2)')
  },
)

it('replaces selected formula text with the measure and continues at its end', async () => {
  const { user } = setup(false, undefined, true)
  await user.click(screen.getByRole('radio', { name: 'Calculated' }))
  const formula = screen.getByLabelText('Formula') as HTMLTextAreaElement
  await user.type(formula, 'MAX(12, 3)')
  formula.setSelectionRange(4, 6)
  await user.click(screen.getByRole('button', { name: 'FQTY' }))
  expect(formula).toHaveValue('MAX([FQTY], 3)')
  expect(formula).toHaveFocus()
  await user.keyboard('+1')
  expect(formula).toHaveValue('MAX([FQTY]+1, 3)')
})

it('collapses the selection after inserting the same selected measure token', async () => {
  const { user } = setup(false, undefined, true)
  await user.click(screen.getByRole('radio', { name: 'Calculated' }))
  const formula = screen.getByLabelText('Formula') as HTMLTextAreaElement
  await user.click(formula)
  await user.paste('ABS([FQTY])')
  expect(formula).toHaveValue('ABS([FQTY])')
  formula.setSelectionRange(4, 10)
  await user.click(screen.getByRole('button', { name: 'FQTY' }))
  expect(formula).toHaveFocus()
  expect(formula.selectionStart).toBe(10)
  expect(formula.selectionEnd).toBe(10)
  await user.keyboard('+1')
  expect(formula).toHaveValue('ABS([FQTY]+1)')
})

it('shows disabled None as the only String aggregation', async () => {
  const { user, save } = setup(false, undefined, false, 'String')
  expect(screen.getByLabelText('Aggregation')).toBeDisabled()
  expect(screen.getByLabelText('Aggregation')).toHaveDisplayValue('None')
  expect(screen.queryByLabelText('Time aggregation')).not.toBeInTheDocument()
  expect(screen.queryByLabelText('Non-time aggregation')).not.toBeInTheDocument()
  expect(screen.queryByLabelText('Aggregation order')).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Save metadata' }))
  expect(save).toHaveBeenCalledWith(
    expect.objectContaining({
      definition: expect.objectContaining({
        aggregation: expect.objectContaining({ nonTime: 'single_value', time: 'single_value' }),
      }),
    }),
  )
})

it.each([
  ['Date', 'max'],
  ['Bool', 'any_true'],
])('applies one %s policy to both axes', async (kind, policy) => {
  const { user, save } = setup(false, undefined, false, kind)
  expect(screen.queryByLabelText('Time aggregation')).not.toBeInTheDocument()
  await user.selectOptions(screen.getByLabelText('Aggregation'), policy)
  await user.click(screen.getByRole('button', { name: 'Save metadata' }))
  expect(save).toHaveBeenCalledWith(
    expect.objectContaining({
      definition: expect.objectContaining({
        aggregation: expect.objectContaining({ nonTime: policy, time: policy }),
      }),
    }),
  )
})

it('offers only transaction-native dimensions for calculated output grains', async () => {
  const { user } = setup(false, 'pt')
  await user.click(screen.getByRole('radio', { name: 'Calculated' }))
  const selector = screen.getByLabelText('Output grain') as HTMLSelectElement
  expect([...selector.options].map((option) => option.value)).toContain('dimension:pt')
  expect([...selector.options].map((option) => option.value)).not.toContain('dimension:other')
})
it('keeps standalone transaction grains available for calculated measures', async () => {
  const { user } = setup(false, 'pt', false, undefined, true)
  await user.click(screen.getByRole('radio', { name: 'Calculated' }))
  expect(screen.getByLabelText('Output grain')).toHaveTextContent('Sales · Transaction grain')
})
