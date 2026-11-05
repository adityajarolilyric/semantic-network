import { useState } from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it } from 'vitest'
import {
  TransactionMeasureEditor,
  numericMeasureColumns,
  transactionMeasuresComplete,
  transactionMeasureSelections,
  type TransactionMeasureDraft,
} from '../src/metadata/editors/TransactionMeasureEditor'
const columns = numericMeasureColumns([
  { columnId: 'qty', name: 'Quantity', physicalType: 'Nullable(Decimal(18,4))', nullable: true },
  { columnId: 'product', name: 'Product', physicalType: 'String', nullable: false },
])
it('creates numeric measure selections with explicit aggregation and fixed defaults', async () => {
  let selected: TransactionMeasureDraft[] = []
  function Editor() {
    const [drafts, setDrafts] = useState<TransactionMeasureDraft[]>([])
    selected = drafts
    return (
      <TransactionMeasureEditor
        columns={columns}
        drafts={drafts}
        onChange={setDrafts}
        measures={[]}
        loading={false}
      />
    )
  }
  render(<Editor />)
  await userEvent.click(screen.getByText('Measures', { exact: true }))
  expect(screen.queryByLabelText('Product')).not.toBeInTheDocument()
  await userEvent.click(screen.getByLabelText('Quantity', { exact: true }))
  expect(transactionMeasuresComplete(selected, columns)).toBe(false)
  expect(screen.getByLabelText('Quantity dis-aggregation policy')).toHaveValue('weighted_by_self')
  await userEvent.click(screen.getByLabelText('Quantity editable'))
  await userEvent.selectOptions(screen.getByLabelText('Quantity non-time aggregation'), 'avg')
  await userEvent.selectOptions(screen.getByLabelText('Quantity time aggregation'), 'max')
  expect(transactionMeasuresComplete(selected, columns)).toBe(true)
  expect(transactionMeasureSelections(selected)).toEqual([
    {
      valueColumnId: 'qty',
      editable: true,
      aggregation: {
        nonTime: 'avg',
        time: 'max',
        order: 'nonTimeThenTime',
        nullInputPolicy: 'ignore',
        allNullResult: 'null',
      },
      allocation: { mode: 'weighted_by_self' },
    },
  ])
  await userEvent.selectOptions(
    screen.getByLabelText('Quantity dis-aggregation policy'),
    'weighted',
  )
  expect(screen.getByLabelText('Quantity weight measure')).toBeVisible()
  expect(transactionMeasuresComplete(selected, columns)).toBe(false)
  await userEvent.click(screen.getByLabelText('Quantity', { exact: true }))
  expect(transactionMeasureSelections(selected)).toEqual([])
})
it('rejects selections removed from transformed output', () => {
  expect(
    transactionMeasuresComplete(
      [
        {
          valueColumnId: 'old',
          name: 'Old',
          editable: false,
          nonTime: 'sum',
          time: 'sum',
          allocation: { mode: 'equal' },
        },
      ],
      columns,
    ),
  ).toBe(false)
})
