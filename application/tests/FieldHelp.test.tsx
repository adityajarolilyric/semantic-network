import { act, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import { Check, Checks, Select, Text } from '../src/metadata/editors/Fields'

it('keeps field names intact and adds no help to Name or Description', () => {
  render(
    <>
      <Text label="Name" value="Sales" onChange={() => {}} />
      <Text label="Description" value="" onChange={() => {}} />
      <Select
        label="Missing transaction value"
        value="null"
        items={['null', 'zero']}
        onChange={() => {}}
      />
    </>,
  )
  expect(screen.getByRole('textbox', { name: 'Name' })).toBeVisible()
  expect(screen.getByRole('combobox', { name: 'Missing transaction value' })).toBeVisible()
  expect(screen.queryByRole('button', { name: 'About Name' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'About Description' })).not.toBeInTheDocument()
})

it('opens on hover and keyboard focus, and closes with Escape', async () => {
  const user = userEvent.setup()
  render(<Select label="Aggregation order" value="" items={[]} onChange={() => {}} />)
  const info = screen.getByRole('button', { name: 'About Aggregation order' })
  await user.hover(info)
  expect(screen.getByRole('tooltip')).toHaveTextContent('combine Locations within each day')
  await user.keyboard('{Escape}')
  expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()
  await user.unhover(info)
  await user.tab()
  expect(info).toHaveFocus()
  expect(screen.getByRole('tooltip')).toBeVisible()
  await user.keyboard('{Escape}')
  expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()
})

it('does not toggle checkboxes or submit forms when help is clicked', async () => {
  const change = vi.fn()
  const submit = vi.fn((event) => event.preventDefault())
  const user = userEvent.setup()
  render(
    <form onSubmit={submit}>
      <Check label="Time dimension" value={false} onChange={change} />
      <button type="button">Outside</button>
    </form>,
  )
  await user.click(screen.getByRole('button', { name: 'About Time dimension' }))
  expect(screen.getByRole('tooltip')).toHaveTextContent('calendar dimension')
  expect(change).not.toHaveBeenCalled()
  expect(submit).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', { name: 'Outside' }))
  expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()
})

it('uses one help icon for a column group instead of adding icons to individual column names', () => {
  render(
    <Checks
      label="Keys from Forecast"
      items={[{ value: 'v', label: 'Time dimension' }]}
      selected={[]}
      onChange={() => {}}
    />,
  )
  expect(screen.getByRole('group', { name: 'Keys from Forecast' })).toBeVisible()
  expect(screen.getByRole('button', { name: 'About Keys from Forecast' })).toBeVisible()
  expect(screen.queryByRole('button', { name: 'About Time dimension' })).not.toBeInTheDocument()
})

it('keeps help keyboard accessible in a read-only fieldset', () => {
  render(
    <fieldset disabled>
      <Select label="Missing transaction value" value="null" items={['null']} onChange={() => {}} />
    </fieldset>,
  )
  const info = screen.getByRole('button', { name: 'About Missing transaction value' })
  // user-event treats every disabled-fieldset descendant as disabled, including
  // non-form controls. Dispatch native focus/keys to verify the help control.
  act(() => info.focus())
  expect(info).toHaveFocus()
  fireEvent.keyDown(info, { key: 'Escape' })
  fireEvent.keyDown(info, { key: 'Enter' })
  expect(screen.getByRole('tooltip')).toBeVisible()
  fireEvent.keyDown(info, { key: 'Escape' })
  fireEvent.keyDown(info, { key: ' ' })
  expect(screen.getByRole('tooltip')).toBeVisible()
})
