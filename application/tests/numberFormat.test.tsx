import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import { formatNumericDisplay } from '../src/lib/numberFormat'
import { PlanningEditCell } from '../src/metadata/PlanningEditCell'
import { DataPreview } from '../src/components/DataPreview'

it.each([
  ['1.234567', '1.23'],
  ['1.235000', '1.24'],
  ['-1.235', '-1.24'],
  ['-0.000001', '0.00'],
  ['9.999999', '10.00'],
  ['0', '0.00'],
  ['1.234567e-2', '0.01'],
  ['1e3', '1000.00'],
  ['12345678901234567890.995', '12345678901234567891.00'],
])('formats %s to two decimal places without Number precision loss', (input, expected) => {
  expect(formatNumericDisplay(input)).toBe(expected)
})

it('keeps the unrounded value in the edit input and submission', async () => {
  const save = vi.fn().mockResolvedValue(undefined)
  render(<PlanningEditCell value="1.234567" label="Quantity" disabled={false} onSave={save} />)
  expect(screen.getByRole('button')).toHaveTextContent('1.23')
  const user = userEvent.setup()
  await user.click(screen.getByRole('button'))
  expect(screen.getByLabelText('New value for Quantity')).toHaveValue('1.234567')
  await user.click(screen.getByRole('button', { name: 'Save' }))
  expect(save).toHaveBeenCalledWith(1.234567)
})

it('formats typed numeric table values without altering string IDs or source rows', () => {
  const rows = [['001', '1.234567', '12345678901234567890.995']]
  render(
    <DataPreview
      table="Values"
      preview={{
        table: 'Values',
        columns: [
          { name: 'id', type: 'String' },
          { name: 'quantity', type: 'Float64' },
          { name: 'amount', type: 'Decimal(38,6)' },
        ],
        rows,
        total: 1,
        offset: 0,
        limit: 100,
      }}
      loading={false}
      error=""
      offset={0}
      limit={100}
      onOffset={vi.fn()}
      onLimit={vi.fn()}
      onRefresh={vi.fn()}
    />,
  )
  expect(screen.getByText('001')).toBeVisible()
  expect(screen.getByText('1.23')).toBeVisible()
  expect(screen.getByText('12345678901234567891.00')).toBeVisible()
  expect(rows[0][1]).toBe('1.234567')
})
