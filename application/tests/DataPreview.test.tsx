import { render, screen, within } from '@testing-library/react'
import { expect, it } from 'vitest'
import { DataPreview } from '../src/components/DataPreview'

it('caps sample rows at 50 while showing the full total', () => {
  render(
    <DataPreview
      table="Forecast"
      sampleOnly
      showRefresh={false}
      preview={{
        table: 'Forecast',
        columns: [{ name: 'id', type: 'String' }],
        rows: Array.from({ length: 60 }, (_, i) => [String(i)]),
        total: 159,
        offset: 0,
        limit: 50,
      }}
      loading={false}
      error=""
      offset={0}
      limit={50}
      onOffset={() => {}}
      onLimit={() => {}}
      onRefresh={() => {}}
    />,
  )
  expect(screen.getByText('50/159 rows')).toBeVisible()
  expect(within(screen.getByRole('table')).getAllByRole('row')).toHaveLength(51)
  expect(screen.queryByLabelText('Rows per page')).not.toBeInTheDocument()
})
