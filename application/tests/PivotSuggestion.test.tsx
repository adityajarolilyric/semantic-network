import { useState } from 'react'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { ObjectPivotEditor } from '../src/metadata/editors/ObjectPivotEditor'
import { metadataApi } from '../src/metadata/api'
import type { PivotStage, Source } from '../src/metadata/types'

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
const suggestion: PivotStage = {
  stageId: 'suggested',
  type: 'pivot',
  enabled: true,
  recordKeyColumnIds: [],
  groupByColumnIds: ['qty'],
  pairs: [
    {
      pairId: 'pair',
      attributeColumnId: 'attribute',
      valueColumnId: 'value',
      outputs: [{ attributeValue: 'Brand', name: 'Brand' }],
    },
  ],
}
afterEach(() => vi.restoreAllMocks())

function setup(initial: PivotStage | null = null) {
  const changed = vi.fn()
  function Editor() {
    const [value, setValue] = useState(initial)
    return (
      <ObjectPivotEditor
        objectId="transaction"
        source={source}
        base="/models/m/revisions/r"
        kind="transactions"
        showConfiguration
        value={value}
        onColumns={vi.fn()}
        onChange={(next) => {
          changed(next)
          setValue(next)
        }}
      />
    )
  }
  render(<Editor />)
  return changed
}

it('fills the editable draft using tableId without saving', async () => {
  const request = vi.spyOn(metadataApi, 'request').mockImplementation(async (path) =>
    path.endsWith('/pivot-suggestion')
      ? ({
          pivot: suggestion,
          reasons: ['Found one pair.'],
          warnings: ['Review before saving.'],
        } as never)
      : ({ columns: source.definition.columns } as never),
  )
  const changed = setup()
  await userEvent.click(screen.getByRole('button', { name: 'Suggest pivot' }))
  await waitFor(() => expect(screen.getByLabelText('Attribute column')).toHaveValue('attribute'))
  expect(screen.getByLabelText('Value column')).toHaveValue('value')
  expect(screen.getByLabelText('Brand')).toBeChecked()
  expect(changed).toHaveBeenCalledWith(suggestion)
  expect(request).toHaveBeenCalledWith(
    '/models/m/revisions/r/managed-tables/pivot-suggestion',
    'POST',
    { tableId: 'source' },
    undefined,
    expect.any(AbortSignal),
  )
  await userEvent.click(
    within(screen.getByRole('group', { name: 'Retained columns' })).getByLabelText('qty'),
  )
  expect(changed.mock.lastCall?.[0].groupByColumnIds).toEqual([])
  expect(
    request.mock.calls.every(
      ([path]) => path.endsWith('/pivot-suggestion') || path.endsWith('/input/schema'),
    ),
  ).toBe(true)
})

it('keeps the current draft when no suggestion is available', async () => {
  vi.spyOn(metadataApi, 'request').mockImplementation(async (path) =>
    path.endsWith('/pivot-suggestion')
      ? ({ pivot: null, reasons: [], warnings: ['No unambiguous pairs were found.'] } as never)
      : ({ columns: source.definition.columns } as never),
  )
  const changed = setup(suggestion)
  await userEvent.click(screen.getByRole('button', { name: 'Suggest pivot' }))
  expect(await screen.findByText('No unambiguous pairs were found.')).toBeVisible()
  expect(changed).not.toHaveBeenCalled()
  expect(screen.getByLabelText('Attribute column')).toHaveValue('attribute')
})

it('reports profiling failures without changing the draft', async () => {
  vi.spyOn(metadataApi, 'request').mockRejectedValue(new Error('Managed input unavailable.'))
  const changed = setup()
  await userEvent.click(screen.getByRole('button', { name: 'Suggest pivot' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Managed input unavailable.')
  expect(changed).not.toHaveBeenCalled()
})
