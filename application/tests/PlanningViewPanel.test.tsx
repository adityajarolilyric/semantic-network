import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import {
  PlanningPivot,
  PlanningViewPanel,
  type PlanningViewResult,
} from '../src/metadata/PlanningViewPanel'
import { metadataApi } from '../src/metadata/api'

const options = {
  dimensions: [
    {
      dimensionId: 'product',
      name: 'Product',
      columns: [
        { columnId: 'brand', name: 'Brand' },
        { columnId: 'id', name: 'Product ID' },
      ],
    },
    { dimensionId: 'location', name: 'Location', columns: [{ columnId: 'zone', name: 'Zone' }] },
  ],
  measures: [
    { measureId: 'f', name: 'FQTY' },
    { measureId: 's', name: 'SQTY' },
  ],
}
const result: PlanningViewResult = {
  fields: [
    { id: 'a', dimensionId: 'product', columnId: 'brand', label: 'Product · Brand', axis: 'row' },
    {
      id: 'b',
      dimensionId: 'location',
      columnId: 'zone',
      label: 'Location · Zone',
      axis: 'column',
    },
  ],
  measures: [
    { measureId: 'f', name: 'FQTY', calculation: 'reused' },
    { measureId: 's', name: 'SQTY', calculation: 'reused' },
  ],
  rows: [
    ['A', 'North', '12.5', '3'],
    ['A', 'South', '20', null],
    ['B', 'South', '8', '2'],
  ],
  groupCount: 3,
  plan: {
    planningLevelName: 'BrandZone',
    usesBase: false,
    explanation: 'Uses a compatible materialised grain.',
  },
}
afterEach(() => vi.restoreAllMocks())

it('submits explicit axes and measure IDs, and renders a multi-measure pivot without client aggregation', async () => {
  const user = userEvent.setup()
  const request = vi
    .spyOn(metadataApi, 'request')
    .mockImplementation(async (path) =>
      path.endsWith('/options') ? (options as never) : (result as never),
    )
  render(<PlanningViewPanel base="/models/m/revisions/r" revisionVersion={1} />)
  await user.selectOptions(await screen.findByLabelText('Add rows attribute'), 'product:brand')
  await user.selectOptions(screen.getByLabelText('Add columns attribute'), 'location:zone')
  expect(
    within(screen.getByLabelText('Add columns attribute')).queryByRole('option', {
      name: 'Product · Brand',
    }),
  ).not.toBeInTheDocument()
  await user.click(screen.getByLabelText('FQTY'))
  await user.click(screen.getByLabelText('SQTY'))
  await user.click(screen.getByRole('button', { name: 'Update pivot' }))
  await screen.findByText('Data from BrandZone')
  expect(request).toHaveBeenLastCalledWith(
    '/models/m/revisions/r/planning-view/query',
    'POST',
    {
      rows: [{ dimensionId: 'product', columnId: 'brand' }],
      columns: [{ dimensionId: 'location', columnId: 'zone' }],
      measures: ['f', 's'],
    },
    undefined,
    expect.any(AbortSignal),
  )
  const table = screen.getByRole('table')
  expect(within(table).getByRole('columnheader', { name: 'North' })).toBeVisible()
  expect(within(table).queryByRole('columnheader', { name: 'FQTY' })).not.toBeInTheDocument()
  expect(within(table).getAllByRole('rowheader', { name: 'FQTY' })).toHaveLength(2)
  expect(within(table).getByText('12.50')).toBeVisible()
  expect(within(table).getByText('—')).toHaveAttribute('title', 'NULL value')
  expect(table.textContent).not.toContain('product:brand')
  await user.click(screen.getByLabelText('Remove Product · Brand from rows'))
  expect(screen.queryByRole('table')).not.toBeInTheDocument()
})

it('rejects stale async results after layout changes and displays server explanations', async () => {
  const user = userEvent.setup()
  let resolve: (value: PlanningViewResult) => void = () => {}
  vi.spyOn(metadataApi, 'request').mockImplementation(async (path) =>
    path.endsWith('/options')
      ? (options as never)
      : ((await new Promise<PlanningViewResult>((done) => {
          resolve = done
        })) as never),
  )
  render(<PlanningViewPanel base="/models/m/revisions/r" revisionVersion={1} />)
  await user.click(await screen.findByLabelText('FQTY'))
  await user.click(screen.getByRole('button', { name: 'Update pivot' }))
  await user.selectOptions(screen.getByLabelText('Add rows attribute'), 'product:brand')
  resolve(result)
  await waitFor(() => expect(screen.queryByRole('table')).not.toBeInTheDocument())
  vi.mocked(metadataApi.request).mockRejectedValue(
    new Error('Build Semantic Network before querying.'),
  )
  await user.click(screen.getByRole('button', { name: 'Update pivot' }))
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Build Semantic Network before querying.',
  )
})

it('renders a global result and keeps NULL dimension groups distinct from text and blank groups', () => {
  const { rerender } = render(
    <PlanningPivot result={{ ...result, fields: [], rows: [['100', null]] }} />,
  )
  expect(screen.getByRole('rowheader', { name: 'All' })).toBeVisible()
  expect(screen.getByText('100.00')).toBeVisible()
  rerender(
    <PlanningPivot
      result={{
        ...result,
        fields: [result.fields[0]],
        rows: [
          [null, '1', '2'],
          ['', '3', '4'],
          ['(empty)', '5', '6'],
        ],
      }}
    />,
  )
  expect(
    screen.getAllByRole('rowheader').filter((cell) => cell.hasAttribute('rowspan')),
  ).toHaveLength(3)
  expect(screen.getByText('(blank)')).toBeVisible()
})

it('edits only eligible existing cells and sends generation-bound member coordinates', async () => {
  const user = userEvent.setup()
  const onCommitted = vi.fn()
  const editable = {
    ...result,
    measures: [
      { ...result.measures[0], editable: true },
      { ...result.measures[1], editable: false, editReason: 'Finer than native' },
    ],
    editContext: { generationId: 'run_old', revisionVersion: 1 },
  }
  let queries = 0
  const request = vi.spyOn(metadataApi, 'request').mockImplementation(async (path) => {
    if (path.endsWith('/options')) return options as never
    if (path.endsWith('/edits')) return { runId: 'run_edit', state: 'succeeded' } as never
    queries++
    return (
      queries === 1
        ? editable
        : {
            ...editable,
            rows: [['A', 'North', '50', '3']],
            editContext: { generationId: 'run_edit', revisionVersion: 2 },
          }
    ) as never
  })
  render(
    <PlanningViewPanel
      base="/models/m/revisions/r"
      revisionVersion={1}
      onCommitted={onCommitted}
    />,
  )
  await user.selectOptions(await screen.findByLabelText('Add rows attribute'), 'product:brand')
  await user.selectOptions(screen.getByLabelText('Add columns attribute'), 'location:zone')
  await user.click(screen.getByLabelText('FQTY'))
  await user.click(screen.getByLabelText('SQTY'))
  await user.click(screen.getByRole('button', { name: 'Update pivot' }))
  await user.click(await screen.findByRole('button', { name: 'Edit FQTY · A / North' }))
  const input = screen.getByRole('textbox', { name: 'New value for FQTY · A / North' })
  await user.clear(input)
  await user.type(input, '50')
  await user.click(screen.getByRole('button', { name: 'Save' }))
  await waitFor(() => expect(onCommitted).toHaveBeenCalledWith(2))
  const editCall = request.mock.calls.find(([path]) => path.endsWith('/edits'))!
  expect(editCall[2]).toMatchObject({
    measureId: 'f',
    memberValues: ['A', 'North'],
    value: 50,
    generationId: 'run_old',
    revisionVersion: 1,
  })
  expect(editCall[5]).toMatch(/^edit_/)
  expect(screen.queryByRole('button', { name: /Edit SQTY/ })).not.toBeInTheDocument()
  expect(await screen.findByRole('button', { name: 'Edit FQTY · A / North' })).toHaveTextContent(
    '50',
  )
})
