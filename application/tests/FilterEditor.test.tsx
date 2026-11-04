import { useState } from 'react'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it } from 'vitest'
import { FilterEditor } from '../src/metadata/editors/FilterEditor'
import type { FilterDefinition } from '../src/metadata/types'

function Harness() {
  const [value, set] = useState<FilterDefinition | null>(null)
  return (
    <>
      <FilterEditor
        value={value}
        onChange={set}
        columns={[
          { columnId: 'brand', name: 'Brand', physicalType: 'String' },
          { columnId: 'qty', name: 'Quantity', physicalType: 'Float64' },
          { columnId: 'valid', name: 'Valid', physicalType: 'Bool' },
          { columnId: 'day', name: 'Date', physicalType: 'Date32' },
        ]}
      />
      <output data-testid="definition">{JSON.stringify(value)}</output>
    </>
  )
}
it('authors nested AND/OR rules with type-specific controls and preserves literal list values', async () => {
  const user = userEvent.setup()
  render(<Harness />)
  await user.click(screen.getByText('Filters', { exact: true }))
  await user.click(screen.getByRole('button', { name: 'Add filter' }))
  await user.selectOptions(screen.getByLabelText('Filter operator'), 'in')
  await user.type(screen.getByLabelText('Filter values'), 'A,B{Enter}Promo%')
  await user.click(screen.getByRole('button', { name: 'Add group' }))
  const nested = screen.getByRole('group', { name: 'Nested group' })
  await user.click(within(nested).getByRole('button', { name: 'OR' }))
  await user.selectOptions(within(nested).getByLabelText('Filter column'), 'qty')
  expect(within(nested).queryByRole('option', { name: 'Contains' })).not.toBeInTheDocument()
  await user.selectOptions(within(nested).getByLabelText('Filter operator'), 'gte')
  await user.type(within(nested).getByLabelText('Filter value'), '12.5')
  await user.click(within(nested).getByRole('button', { name: 'Add condition' }))
  await user.selectOptions(within(nested).getAllByLabelText('Filter column')[1], 'valid')
  await user.selectOptions(within(nested).getAllByLabelText('Filter value')[1], 'true')
  const definition = JSON.parse(screen.getByTestId('definition').textContent!)
  expect(definition.root.children[0].values).toEqual(['A,B', 'Promo%'])
  expect(definition.root.children[1]).toEqual({
    type: 'group',
    combinator: 'or',
    children: [
      { type: 'condition', columnId: 'qty', operator: 'gte', values: ['12.5'] },
      { type: 'condition', columnId: 'valid', operator: 'eq', values: [true] },
    ],
  })
  expect(screen.getByText('3 conditions')).toBeVisible()
  expect(screen.getByLabelText('Filter summary')).toHaveTextContent(
    'Brand is one of "A,B", "Promo%" AND (Quantity greater than or equal 12.5 OR Valid equals True)',
  )
  expect(within(nested).getByRole('button', { name: 'OR' })).toHaveAttribute('aria-pressed', 'true')
  await user.click(screen.getByLabelText('Remove filter group'))
  expect(screen.queryByRole('group', { name: 'Nested group' })).not.toBeInTheDocument()
  await user.selectOptions(screen.getByLabelText('Filter column'), 'day')
  expect(screen.getByLabelText('Filter value')).toHaveAttribute('type', 'date')
  await user.selectOptions(screen.getByLabelText('Filter operator'), 'is_null')
  expect(screen.queryByLabelText('Filter value')).not.toBeInTheDocument()
  await user.click(screen.getByLabelText('Remove filter condition'))
  expect(screen.getByTestId('definition')).toHaveTextContent('null')
  await user.click(screen.getByRole('button', { name: 'Add filter' }))
  await user.click(screen.getByRole('button', { name: 'Clear all' }))
  expect(screen.getByText('All rows are included')).toBeVisible()
})
