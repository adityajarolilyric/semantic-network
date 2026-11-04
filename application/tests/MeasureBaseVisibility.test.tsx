import { render, screen, waitFor } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { MeasureBaseVisibility } from '../src/metadata/editors/MeasureBaseVisibility'
import { metadataApi } from '../src/metadata/api'
import type { RefDefinition } from '../src/metadata/types'
const definition: RefDefinition = {
  measureType: 'referenced',
  transactionId: 'tx',
  valueColumnId: 'qty',
  nativePlanningLevelId: 'coarse',
  resultType: { type: 'number', nullable: true },
  aggregation: { nonTime: 'sum', time: 'avg', nullInputPolicy: 'ignore', allNullResult: 'null' },
  missingTransactionValue: 'null',
  sourceNullPolicy: 'preserve',
  allocation: { mode: 'weighted_by_self' },
  editable: true,
}
it('shows the real Base path and updates split/repeat immediately from unsaved aggregation choices', async () => {
  const request = vi.spyOn(metadataApi, 'request').mockResolvedValue({
    baseLevelId: 'base',
    relationship: 'roll_down',
    reason: null,
    expansionAxes: ['nonTime', 'time'],
  })
  try {
    const { rerender } = render(
      <MeasureBaseVisibility definition={definition} levels={[]} base="/test" baseLevelId="base" />,
    )
    await waitFor(() =>
      expect(screen.getByLabelText('Non-time Base visibility policy')).toHaveValue('equal'),
    )
    expect(screen.getByLabelText('Time Base visibility policy')).toHaveValue('repeat')
    expect(screen.getByLabelText('Non-time Base visibility policy')).toHaveValue('equal')
    expect(screen.getByLabelText('Non-time Base visibility policy')).toBeDisabled()
    rerender(
      <MeasureBaseVisibility
        definition={{
          ...definition,
          aggregation: {
            ...definition.aggregation,
            nonTime: 'max',
            time: 'sum',
            order: 'timeThenNonTime',
          },
        }}
        levels={[]}
        base="/test"
        baseLevelId="base"
      />,
    )
    expect(screen.getByLabelText('Non-time Base visibility policy')).toHaveValue('repeat')
    expect(screen.getByLabelText('Time Base visibility policy')).toHaveValue('equal')
    expect(request).toHaveBeenCalledTimes(1)
  } finally {
    request.mockRestore()
  }
})
it('does not show the section for Base-native measures or when Base is unset', () => {
  const { rerender } = render(<MeasureBaseVisibility definition={definition} levels={[]} />)
  expect(screen.queryByRole('region')).not.toBeInTheDocument()
  rerender(<MeasureBaseVisibility definition={definition} levels={[]} baseLevelId="coarse" />)
  expect(screen.queryByRole('region')).not.toBeInTheDocument()
})
it('explains unreachable Base paths without promising disaggregation', async () => {
  const request = vi.spyOn(metadataApi, 'request').mockResolvedValue({
    baseLevelId: 'base',
    relationship: 'incompatible',
    reason: 'No proven dimension mapping.',
    expansionAxes: [],
  })
  try {
    render(
      <MeasureBaseVisibility definition={definition} levels={[]} base="/test" baseLevelId="base" />,
    )
    expect(await screen.findByText(/This measure cannot reach Base/)).toBeVisible()
    expect(screen.queryByRole('list')).not.toBeInTheDocument()
  } finally {
    request.mockRestore()
  }
})

it('describes AggregateFirst as recalculation, never splitting its formula result', async () => {
  const request = vi.spyOn(metadataApi, 'request').mockResolvedValue({
    baseLevelId: 'base',
    relationship: 'roll_down',
    reason: null,
    expansionAxes: ['nonTime'],
  })
  try {
    render(
      <MeasureBaseVisibility
        definition={{
          measureType: 'calculated',
          nativePlanningLevelId: 'coarse',
          calculationType: 'AggregateFirst',
          formula: { displayText: '1', expression: { type: 'literal', value: 1 } },
          resultType: { type: 'number', nullable: true },
          editable: false,
        }}
        levels={[]}
        base="/test"
        baseLevelId="base"
      />,
    )
    expect(await screen.findByText(/formula is recalculated at Base/)).toBeVisible()
    expect(screen.queryByRole('list')).not.toBeInTheDocument()
  } finally {
    request.mockRestore()
  }
})
