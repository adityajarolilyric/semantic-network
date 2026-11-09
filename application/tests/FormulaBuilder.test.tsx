import { useState } from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it } from 'vitest'
import { FormulaBuilder } from '../src/metadata/FormulaBuilder'
import { emptySnapshot } from '../src/metadata/types'
function setup() {
  function Editor() {
    const [value, setValue] = useState('')
    return (
      <FormulaBuilder
        value={value}
        onChange={setValue}
        measures={[]}
        data={emptySnapshot()}
        attributes={[
          {
            key: 'product.brand',
            label: 'Product.Brand',
            valueType: 'string',
            reference: { type: 'attributeRef', memberCatalogId: 'product', columnId: 'brand' },
          },
        ]}
        functions={[
          { name: 'count_distinct', description: 'Count exact distinct values.', minimum: 1 },
          { name: 'count_rows', description: 'Count planning intersections.', minimum: 0 },
        ]}
        validation={null}
        attributeError=""
        functionError=""
        levelSelected
      />
    )
  }
  render(<Editor />)
  return userEvent.setup()
}
it('searches and inserts functions and columns at the caret without dropdowns', async () => {
  const user = setup()
  expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
  await user.click(screen.getByRole('tab', { name: 'Functions' }))
  await user.type(screen.getByLabelText('Search formula inputs'), 'distinct{Enter}')
  const editor = screen.getByLabelText('Formula') as HTMLTextAreaElement
  expect(editor).toHaveValue('COUNT_DISTINCT()')
  expect(editor).toHaveFocus()
  expect(editor.selectionStart).toBe('COUNT_DISTINCT('.length)
  expect(screen.getByText('Count exact distinct values.')).toBeVisible()
  await user.click(screen.getByRole('tab', { name: 'Columns' }))
  await user.type(screen.getByLabelText('Search formula inputs'), 'brand{ArrowDown}{Enter}')
  expect(editor).toHaveValue('COUNT_DISTINCT([Product.Brand])')
  expect(editor).toHaveFocus()
  expect(editor.selectionStart).toBe('COUNT_DISTINCT([Product.Brand]'.length)
})
it('supports keyboard tab navigation, search shortcut and zero-argument functions', async () => {
  const user = setup()
  await user.click(screen.getByRole('tab', { name: 'Measures' }))
  await user.keyboard('{End}')
  expect(screen.getByRole('tab', { name: 'Functions' })).toHaveFocus()
  const editor = screen.getByLabelText('Formula') as HTMLTextAreaElement
  await user.click(editor)
  await user.keyboard('{Control>} {/Control}')
  expect(screen.getByLabelText('Search formula inputs')).toHaveFocus()
  await user.type(screen.getByLabelText('Search formula inputs'), 'count_rows{Enter}')
  expect(editor).toHaveValue('COUNT_ROWS()')
  expect(editor.selectionStart).toBe('COUNT_ROWS()'.length)
})

it('marks invalid formulas as errors and clears the error state after validation succeeds', () => {
  const props = {
    value: '[Forecast Qty]',
    onChange: () => {},
    measures: [],
    attributes: [],
    functions: [],
    data: emptySnapshot(),
    attributeError: '',
    functionError: '',
    levelSelected: true,
  }
  const { rerender } = render(
    <FormulaBuilder
      {...props}
      validation={{
        valid: false,
        containsCounts: false,
        diagnostics: [
          { message: '"Forecast Qty" is unavailable at Output Dimension "Orders grain".' },
        ],
      }}
    />,
  )
  expect(screen.getByRole('status')).toHaveClass('is-error')
  expect(screen.getByRole('status')).toHaveTextContent('"Forecast Qty" is unavailable')
  expect(screen.getByLabelText('Formula')).toHaveAttribute('aria-invalid', 'true')
  rerender(
    <FormulaBuilder
      {...props}
      validation={{
        valid: true,
        containsCounts: false,
        resultType: { type: 'number', nullable: true },
        diagnostics: [],
      }}
    />,
  )
  expect(screen.getByRole('status')).not.toHaveClass('is-error')
  expect(screen.getByLabelText('Formula')).not.toHaveAttribute('aria-invalid')
})

it('inserts compatible calculated measures and explains incompatible inputs', async () => {
  const user = userEvent.setup()
  const data = emptySnapshot()
  const template = {
    modelId: 'm',
    revisionId: 'r',
    version: 1,
    createdAt: '',
    updatedAt: '',
    kind: 'measure' as const,
    description: '',
    displayName: '',
    business: { unit: null, aliases: [] },
    definition: {
      measureType: 'calculated' as const,
      nativeGrainId: 'base',
      calculationType: 'AggregateFirst' as const,
      resultType: { type: 'number' as const, nullable: true },
      editable: false,
      formula: { displayText: '1', expression: { type: 'literal' as const, value: 1 } },
    },
  }
  const measures = [
    { ...template, objectId: 'blocked', name: 'Unrelated' },
    { ...template, objectId: 'profit', name: 'Profit' },
  ] as import('../src/metadata/types').Measure[]
  function Editor() {
    const [value, setValue] = useState('')
    return (
      <FormulaBuilder
        value={value}
        onChange={setValue}
        measures={measures}
        measureAvailability={{
          blocked: { available: false, reason: 'Unrelated has no proven path to Base.' },
          profit: { available: true, reason: 'Recalculate at Base.' },
        }}
        attributes={[]}
        functions={[]}
        data={data}
        validation={null}
        attributeError=""
        functionError=""
        levelSelected
      />
    )
  }
  render(<Editor />)
  expect(screen.getByRole('button', { name: 'Unrelated' })).toBeDisabled()
  expect(screen.getByText('Unrelated has no proven path to Base.')).toBeVisible()
  expect(screen.getByText('Calculated · Number')).toBeVisible()
  // Keyboard insertion skips unavailable suggestions and restores the formula caret.
  await user.click(screen.getByLabelText('Search formula inputs'))
  await user.keyboard('{Enter}')
  expect(screen.getByLabelText('Formula')).toHaveValue('[Profit]')
  expect(screen.getByLabelText('Formula')).toHaveFocus()
})

it('groups the function library by arithmetic, conditional and time-series families', async () => {
  const user = userEvent.setup()
  render(
    <FormulaBuilder
      value=""
      onChange={() => {}}
      measures={[]}
      attributes={[]}
      data={emptySnapshot()}
      functions={[
        { name: 'sqrt', group: 'Arithmetic', description: 'Square root.' },
        { name: 'if', group: 'Conditional', description: 'Choose a branch.' },
        { name: 'rolling', group: 'Time series', description: 'Calendar window.' },
      ]}
      validation={null}
      attributeError=""
      functionError=""
      levelSelected
    />,
  )
  await user.click(screen.getByRole('tab', { name: 'Functions' }))
  expect(
    screen.getByRole('button', { name: 'Insert SQRT' }).closest('.formula-column-group'),
  ).toHaveTextContent('Arithmetic')
  expect(
    screen.getByRole('button', { name: 'Insert IF' }).closest('.formula-column-group'),
  ).toHaveTextContent('Conditional')
  expect(
    screen.getByRole('button', { name: 'Insert ROLLING' }).closest('.formula-column-group'),
  ).toHaveTextContent('Time series')
})
