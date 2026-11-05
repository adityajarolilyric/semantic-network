import { useState } from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import { ObjectPivotEditor } from '../src/metadata/editors/ObjectPivotEditor'
import type { PivotStage, Source } from '../src/metadata/types'

it('shows inline configuration without enabling an unused pivot, and allows clearing it', async () => {
  const source: Source = {
    objectId: 'source',
    kind: 'source',
    name: 'Forecast',
    description: '',
    definition: {
      tableName: 'forecast',
      inScope: true,
      keyColumns: [],
      columns: [
        { columnId: 'attribute', name: 'attribute', physicalType: 'String', nullable: false },
        { columnId: 'value', name: 'value', physicalType: 'String', nullable: false },
        { columnId: 'qty', name: 'qty', physicalType: 'Float64', nullable: false },
      ],
    },
  }
  const changed = vi.fn()
  const columns = vi.fn()
  function Editor() {
    const [stage, setStage] = useState<PivotStage | null>(null)
    return (
      <form aria-label="Transaction">
        <ObjectPivotEditor
          objectId="tx"
          kind="transactions"
          source={source}
          value={stage}
          showConfiguration
          onChange={(value) => {
            changed(value)
            setStage(value)
          }}
          onColumns={columns}
        />
      </form>
    )
  }
  render(<Editor />)
  expect(screen.queryByLabelText('Pivot long-format columns')).not.toBeInTheDocument()
  expect(screen.getByLabelText('Attribute column')).toBeVisible()
  expect(screen.getByLabelText('Value column')).toBeVisible()
  expect(changed).not.toHaveBeenCalled()
  expect((screen.getByRole('form') as HTMLFormElement).checkValidity()).toBe(true)
  expect(columns).toHaveBeenLastCalledWith(source.definition.columns)
  await userEvent.selectOptions(screen.getByLabelText('Attribute column'), 'attribute')
  expect(changed.mock.lastCall?.[0]).toMatchObject({
    enabled: true,
    pairs: [{ attributeColumnId: 'attribute', valueColumnId: '', outputs: [] }],
  })
  await waitFor(() => expect(columns).toHaveBeenLastCalledWith(null))
  await userEvent.click(screen.getByRole('button', { name: 'Clear pivot' }))
  expect(changed).toHaveBeenLastCalledWith(null)
  expect(screen.getByLabelText('Attribute column')).toHaveValue('')
  expect((screen.getByRole('form') as HTMLFormElement).checkValidity()).toBe(true)
  await waitFor(() => expect(columns).toHaveBeenLastCalledWith(source.definition.columns))
  await userEvent.selectOptions(screen.getByLabelText('Attribute column'), 'attribute')
  await userEvent.click(screen.getByRole('button', { name: 'Remove pair' }))
  expect(changed).toHaveBeenLastCalledWith(null)
  expect(screen.getByLabelText('Attribute column')).toHaveValue('')
})
