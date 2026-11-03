import { expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { metadataApi } from '../src/metadata/api'
import {
  resolutionComplete,
  TransactionResolutionEditor,
} from '../src/metadata/editors/TransactionResolutionEditor'
import type { GrainResolution } from '../src/metadata/types'

it('requires explicit order, values and coverage of every configured axis', () => {
  const config: GrainResolution = {
    axes: [{ grainColumnId: 'product', allowAll: false, rules: [] }],
    values: [{ sourceColumnId: 'qty', mode: 'split' }],
    orderColumnIds: ['sequence'],
    partitionColumnIds: [],
    passthroughColumnIds: [],
    overlapPolicy: 'last_applied_wins',
  }
  expect(resolutionComplete(config)).toBe(false)
  config.axes[0].allowAll = true
  expect(resolutionComplete(config)).toBe(true)
  config.orderColumnIds = []
  expect(resolutionComplete(config)).toBe(false)
  config.orderColumnIds = ['sequence']
  config.axes[0].rules = [
    {
      ruleId: 'rule',
      kind: 'level_value',
      selectorColumnId: 'level',
      valueColumnId: 'member',
      selectors: [{ value: 'brand', target: { columnId: '' } }],
      blankIsAbsent: true,
    },
  ]
  expect(resolutionComplete(config)).toBe(false)
  config.axes[0].rules[0].selectors[0].target.columnId = 'brand'
  expect(resolutionComplete(config)).toBe(true)
})

it('discovers selector values automatically and preserves attribute mappings on refresh', async () => {
  const request = vi
    .spyOn(metadataApi, 'request')
    .mockImplementation(async (path) =>
      path.includes('/resolution/options')
        ? { targets: [{ target: { columnId: 'brand' }, label: 'Native · Brand' }] }
        : { values: [null, '', 'brand'], nextOffset: null },
    )
  const config: GrainResolution = {
    axes: [
      {
        grainColumnId: 'product',
        allowAll: false,
        rules: [
          {
            ruleId: 'r',
            kind: 'level_value',
            selectorColumnId: 'selector',
            valueColumnId: 'member',
            selectors: [],
            blankIsAbsent: true,
          },
        ],
      },
    ],
    values: [],
    orderColumnIds: [],
    partitionColumnIds: [],
    passthroughColumnIds: [],
    overlapPolicy: 'last_applied_wins',
  }
  const level = {
    kind: 'planningLevel' as const,
    name: 'Product',
    displayName: 'Product',
    description: '',
    objectId: 'native',
    definition: {
      grain: {
        keyIds: ['product'],
        columns: [
          { grainColumnId: 'product', name: 'Product', populationRef: { columnId: 'product' } },
        ],
      },
      population: { operation: 'source' as const, sourceId: 's' },
      dimensionBindings: [],
    },
  }
  function Harness() {
    const [value, set] = useState(config)
    return (
      <TransactionResolutionEditor
        value={value}
        onChange={set}
        level={level}
        columns={[]}
        base="/test"
        sourceId="managed"
      />
    )
  }
  try {
    render(<Harness />)
    const field = await screen.findByLabelText('Attribute for brand')
    await userEvent.selectOptions(field, JSON.stringify([null, 'brand']))
    expect(screen.queryByLabelText('Order tie-breaker columns')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Independent assignment series')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Selector value 1')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Discover' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Discover' })).toBeEnabled())
    expect(screen.getByLabelText('Attribute for brand')).toHaveValue(
      JSON.stringify([null, 'brand']),
    )
    expect(request).toHaveBeenCalledWith(
      expect.stringContaining('managedOutput=true'),
      'GET',
      undefined,
      undefined,
      expect.any(AbortSignal),
    )
  } finally {
    request.mockRestore()
  }
})

it('requires complete date-range metadata and an explicit time distribution policy', () => {
  const config: GrainResolution = {
    axes: [
      {
        grainColumnId: 'week',
        allowAll: false,
        rules: [],
        dateRange: {
          dimensionId: 'time',
          calendarKeyColumnId: 'week',
          startColumnId: 'start',
          endColumnId: '',
        },
      },
    ],
    values: [{ sourceColumnId: 'qty', mode: 'split' }],
    orderColumnIds: ['sequence'],
    partitionColumnIds: [],
    passthroughColumnIds: [],
    overlapPolicy: 'last_applied_wins',
  }
  expect(resolutionComplete(config)).toBe(false)
  config.axes[0].dateRange!.endColumnId = 'end'
  expect(resolutionComplete(config)).toBe(false)
  config.values[0].timeMode = 'copy'
  expect(resolutionComplete(config)).toBe(true)
})

it('shows retained columns above order and allows retained text as application order', async () => {
  const config: GrainResolution = {
    axes: [],
    values: [{ sourceColumnId: 'quantity', mode: 'split' }],
    orderColumnIds: [],
    partitionColumnIds: [],
    passthroughColumnIds: [],
    overlapPolicy: 'last_applied_wins',
  }
  function Harness() {
    const [value, set] = useState(config)
    return (
      <TransactionResolutionEditor
        value={value}
        onChange={set}
        sourceId="s"
        level={{
          objectId: 'pl',
          kind: 'planningLevel',
          name: 'P',
          displayName: 'P',
          description: '',
          definition: {
            population: { operation: 'source', sourceId: 's' },
            grain: { keyIds: [], columns: [] },
            dimensionBindings: [],
          },
        }}
        columns={[
          { columnId: 'promo', name: 'promo_id', physicalType: 'String', nullable: false },
          { columnId: 'quantity', name: 'quantity', physicalType: 'Float64', nullable: false },
        ]}
      />
    )
  }
  render(<Harness />)
  expect(screen.queryByText('More resolution settings')).not.toBeInTheDocument()
  const retained = screen.getByRole('group', { name: 'Additional columns to retain' })
  const order = screen.getByRole('combobox', { name: 'Apply-order column' })
  expect(retained.compareDocumentPosition(order) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  await userEvent.click(screen.getByRole('checkbox', { name: 'promo_id' }))
  await userEvent.selectOptions(order, 'promo')
  expect(order).toHaveValue('promo')
  expect(screen.getByRole('checkbox', { name: 'promo_id' })).toBeChecked()
  expect(order.querySelector('option[value="quantity"]')).toBeNull()
})
