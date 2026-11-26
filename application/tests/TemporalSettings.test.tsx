import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import { TemporalSettings } from '../src/metadata/editors/TemporalSettings'
import type { FormulaValidation } from '../src/metadata/useFormulaValidation'

const resolution: NonNullable<FormulaValidation['temporalResolution']> = {
  context: {
    timeKeyId: 'week',
    orderColumnId: 'week_start',
    periodUnit: 'week',
    partitionColumnIds: ['product', 'location'],
    minimumPeriods: 1,
  },
  summary: 'Order by Week start; separate series for Product, Location.',
  reason: '',
  columns: [
    { columnId: 'week_start', name: 'Week start' },
    { columnId: 'week', name: 'Week' },
    { columnId: 'month', name: 'Month' },
  ],
  keys: [
    { columnId: 'product', name: 'Product' },
    { columnId: 'location', name: 'Location' },
    { columnId: 'week', name: 'Week' },
    { columnId: 'month', name: 'Month' },
  ],
  timeKeyIds: ['week', 'month'],
}

it('shows inferred settings as a summary and keeps detailed inputs collapsed', async () => {
  const user = userEvent.setup(),
    change = vi.fn()
  render(<TemporalSettings value={null} resolution={resolution} onChange={change} />)
  expect(screen.getByText(resolution.summary)).toBeVisible()
  expect(screen.getByLabelText('Time key')).not.toBeVisible()
  expect(change).not.toHaveBeenCalled()
  await user.click(screen.getByText('Advanced time-series settings'))
  expect(screen.getByLabelText('Time key')).toHaveValue('week')
  expect(screen.getByLabelText('Calendar ordering column')).toHaveValue('week_start')
  expect(screen.getByLabelText('Product')).toBeChecked()
  expect(screen.getByLabelText('Location')).toBeChecked()
  expect(screen.getByLabelText('Month')).not.toBeChecked()
})

it('asks for advanced configuration only when inference is ambiguous', () => {
  render(
    <TemporalSettings
      value={null}
      resolution={{
        ...resolution,
        context: null,
        summary: '',
        reason: 'Choose which calendar defines this series.',
      }}
      onChange={vi.fn()}
    />,
  )
  expect(screen.getByText('Choose which calendar defines this series.')).toBeVisible()
  expect(screen.getByLabelText('Time key')).toBeVisible()
})

it('lets a saved override return to automatic settings', async () => {
  const user = userEvent.setup(),
    change = vi.fn()
  render(<TemporalSettings value={resolution.context} resolution={resolution} onChange={change} />)
  await user.click(screen.getByText('Advanced time-series settings'))
  await user.click(screen.getByRole('button', { name: 'Use automatic settings' }))
  expect(change).toHaveBeenCalledWith(null)
})
