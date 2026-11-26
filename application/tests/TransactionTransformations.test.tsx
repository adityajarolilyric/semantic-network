import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it } from 'vitest'
import { TransformationsPanel } from '../src/metadata/editors/TransformationsPanel'

it('keeps tab drafts mounted and supports keyboard navigation', async () => {
  const { container } = render(
    <TransformationsPanel
      tabs={[
        { label: 'Filters', configured: false, content: <input aria-label="Filter draft" /> },
        { label: 'Pivot Data', configured: true, content: <input aria-label="Pivot draft" /> },
        { label: 'Date Resolution', configured: false, content: <input aria-label="Date draft" /> },
      ]}
    />,
  )
  expect(container.querySelector('details')).not.toHaveAttribute('open')
  await userEvent.click(screen.getByText('Transformations', { exact: true }))
  expect(screen.getByRole('tab', { name: 'Filters' })).toHaveAttribute('aria-selected', 'true')
  await userEvent.type(screen.getByLabelText('Filter draft'), 'keep me')
  await userEvent.click(screen.getByRole('tab', { name: 'Pivot Data' }))
  expect(screen.getByLabelText('Filter draft')).not.toBeVisible()
  await userEvent.type(screen.getByLabelText('Pivot draft'), 'brand')
  fireEvent.keyDown(screen.getByRole('tab', { name: 'Pivot Data' }), { key: 'ArrowLeft' })
  expect(screen.getByRole('tab', { name: 'Filters' })).toHaveFocus()
  expect(screen.getByLabelText('Filter draft')).toHaveValue('keep me')
  expect(screen.getByLabelText('Pivot draft')).toHaveValue('brand')
  await userEvent.click(screen.getByText('Transformations', { exact: true }))
  fireEvent.invalid(screen.getByLabelText('Date draft'))
  expect(container.querySelector('details')).toHaveAttribute('open')
  expect(screen.getByRole('tab', { name: 'Date Resolution' })).toHaveAttribute(
    'aria-selected',
    'true',
  )
  expect(screen.getByLabelText('Date draft')).toBeVisible()
})
