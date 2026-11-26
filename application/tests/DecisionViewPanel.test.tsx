import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import {
  PlanningPivot,
  DecisionViewPanel,
  type DecisionViewResult,
} from '../src/metadata/DecisionViewPanel'
import { metadataApi } from '../src/metadata/api'

it('moves the build-required notice to the network action and preserves other errors', async () => {
  const message =
    'Build Semantic Network in Materialise before opening a Planning View. Saved definitions and planning populations must be current.'
  vi.spyOn(metadataApi, 'request').mockRejectedValue(new Error(message))
  const onBuildRequiredChange = vi.fn()
  const view = render(
    <DecisionViewPanel
      base="/models/m/revisions/r"
      revisionVersion={1}
      onBuildRequiredChange={onBuildRequiredChange}
    />,
  )
  await waitFor(() => expect(onBuildRequiredChange).toHaveBeenLastCalledWith(message))
  expect(screen.queryByText(message)).not.toBeInTheDocument()
  vi.mocked(metadataApi.request).mockRejectedValue(new Error('Cannot reach the backend.'))
  view.rerender(
    <DecisionViewPanel
      base="/models/m/revisions/r"
      revisionVersion={2}
      onBuildRequiredChange={onBuildRequiredChange}
    />,
  )
  expect(await screen.findByRole('alert')).toHaveTextContent('Cannot reach the backend.')
  expect(onBuildRequiredChange).toHaveBeenLastCalledWith('')
})

const options = {
  dimensionMembers: [
    {
      memberCatalogId: 'product',
      name: 'Product',
      columns: [
        { columnId: 'brand', name: 'Brand' },
        { columnId: 'id', name: 'Product ID' },
      ],
    },
    {
      memberCatalogId: 'location',
      name: 'Location',
      columns: [{ columnId: 'zone', name: 'Zone' }],
    },
  ],
  measures: [
    { measureId: 'f', name: 'FQTY' },
    { measureId: 's', name: 'SQTY' },
  ],
}
const result: DecisionViewResult = {
  fields: [
    {
      id: 'a',
      memberCatalogId: 'product',
      columnId: 'brand',
      label: 'Product · Brand',
      axis: 'row',
    },
    {
      id: 'b',
      memberCatalogId: 'location',
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
    dimensionName: 'BrandZone',
    usesBase: false,
    explanation: 'Uses a compatible materialised grain.',
  },
}
const storedPreferences = new Map<string, string>()
beforeEach(() => {
  storedPreferences.clear()
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => storedPreferences.get(key) ?? null,
    setItem: (key: string, value: string) => storedPreferences.set(key, value),
    removeItem: (key: string) => storedPreferences.delete(key),
    clear: () => storedPreferences.clear(),
  })
})
afterEach(() => vi.restoreAllMocks())

it('submits explicit axes and measure IDs, and renders a multi-measure pivot without client aggregation', async () => {
  const user = userEvent.setup()
  const request = vi
    .spyOn(metadataApi, 'request')
    .mockImplementation(async (path) =>
      path.endsWith('/options') ? (options as never) : (result as never),
    )
  render(<DecisionViewPanel base="/models/m/revisions/r" revisionVersion={1} />)
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
    '/models/m/revisions/r/decision-view/query',
    'POST',
    {
      rows: [{ memberCatalogId: 'product', columnId: 'brand' }],
      columns: [{ memberCatalogId: 'location', columnId: 'zone' }],
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
  let resolve: (value: DecisionViewResult) => void = () => {}
  vi.spyOn(metadataApi, 'request').mockImplementation(async (path) =>
    path.endsWith('/options')
      ? (options as never)
      : ((await new Promise<DecisionViewResult>((done) => {
          resolve = done
        })) as never),
  )
  render(<DecisionViewPanel base="/models/m/revisions/r" revisionVersion={1} />)
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
    <DecisionViewPanel
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

it('restores selections and loads fresh results after navigation, then supports manual cleanup', async () => {
  const user = userEvent.setup()
  let calls = 0
  vi.spyOn(metadataApi, 'request').mockImplementation(async (path) => {
    if (path.endsWith('/options')) return { ...options, buildId: 'build_one' } as never
    calls++
    return { ...result, rows: [['A', 'North', String(calls), '3']] } as never
  })
  const first = render(<DecisionViewPanel base="/models/m/revisions/r" revisionVersion={1} />)
  await user.selectOptions(await screen.findByLabelText('Add rows attribute'), 'product:brand')
  await user.selectOptions(screen.getByLabelText('Add columns attribute'), 'location:zone')
  await user.click(screen.getByLabelText('FQTY'))
  await user.click(screen.getByRole('button', { name: 'Update pivot' }))
  await screen.findByText('Data from BrandZone')
  const cached = JSON.parse([...storedPreferences.values()][0])
  expect(cached.rows).toEqual([{ memberCatalogId: 'product', columnId: 'brand' }])
  expect(cached.columns).toEqual([{ memberCatalogId: 'location', columnId: 'zone' }])
  expect(cached.measures).toEqual(['f'])
  expect(cached).not.toHaveProperty('result')
  first.unmount()
  render(<DecisionViewPanel base="/models/m/revisions/r" revisionVersion={2} />)
  await screen.findByText('Data from BrandZone')
  expect(screen.getByLabelText('FQTY')).toBeChecked()
  expect(screen.getByRole('button', { name: 'Remove Product · Brand from rows' })).toBeVisible()
  expect(calls).toBe(2)
  await user.click(screen.getByRole('button', { name: 'Clear selections' }))
  expect(screen.getByLabelText('FQTY')).not.toBeChecked()
  expect(screen.queryByRole('table')).not.toBeInTheDocument()
  expect(storedPreferences.size).toBe(0)
})
it('preserves selections after a successful new build and isolates other revisions', async () => {
  const user = userEvent.setup()
  let buildId = 'one'
  vi.spyOn(metadataApi, 'request').mockImplementation(async (path) =>
    path.endsWith('/options') ? ({ ...options, buildId } as never) : (result as never),
  )
  const first = render(<DecisionViewPanel base="/models/m/revisions/r" revisionVersion={1} />)
  await user.click(await screen.findByLabelText('FQTY'))
  first.unmount()
  const other = render(<DecisionViewPanel base="/models/m/revisions/other" revisionVersion={1} />)
  expect(await screen.findByLabelText('FQTY')).not.toBeChecked()
  other.unmount()
  buildId = 'two'
  render(<DecisionViewPanel base="/models/m/revisions/r" revisionVersion={2} />)
  expect(await screen.findByLabelText('FQTY')).toBeChecked()
  expect(storedPreferences.size).toBe(1)
})
it('detects rebuilds before querying a layout that is still open', async () => {
  const user = userEvent.setup()
  let buildId = 'one'
  const request = vi
    .spyOn(metadataApi, 'request')
    .mockImplementation(async (path) =>
      path.endsWith('/options') ? ({ ...options, buildId } as never) : (result as never),
    )
  render(<DecisionViewPanel base="/models/m/revisions/r" revisionVersion={1} />)
  await user.click(await screen.findByLabelText('FQTY'))
  buildId = 'two'
  await user.click(screen.getByRole('button', { name: 'Update pivot' }))
  expect(await screen.findByLabelText('FQTY')).toBeChecked()
  await waitFor(() =>
    expect(request.mock.calls.some(([path]) => path.endsWith('/query'))).toBe(true),
  )
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})

it('reorders selected measures and submits the order to the query', async () => {
  const user = userEvent.setup()
  const request = vi
    .spyOn(metadataApi, 'request')
    .mockImplementation(async (path) =>
      path.endsWith('/options') ? (options as never) : (result as never),
    )
  render(<DecisionViewPanel base="/models/m/revisions/r" revisionVersion={1} />)
  await user.click(await screen.findByLabelText('FQTY'))
  await user.click(screen.getByLabelText('SQTY'))
  await user.click(screen.getByLabelText('Move SQTY up in values'))
  expect(
    within(screen.getByLabelText('Measure order'))
      .getAllByRole('listitem')
      .map((li) => li.textContent),
  ).toEqual(['SQTY↑×', 'FQTY↑×'])
  await user.click(screen.getByRole('button', { name: 'Update pivot' }))
  await waitFor(() =>
    expect(request).toHaveBeenLastCalledWith(
      '/models/m/revisions/r/decision-view/query',
      'POST',
      { rows: [], columns: [], measures: ['s', 'f'] },
      undefined,
      expect.any(AbortSignal),
    ),
  )
})

it('restores an equivalent saved field after native/Base attributes are deduplicated', async () => {
  const scope = '/models/aliases/revisions/r'
  storedPreferences.set(
    'semantic:decision-view:v1:' + scope,
    JSON.stringify({
      buildId: 'old',
      rows: [{ transactionId: 'forecast', columnId: 'product_key' }],
      columns: [],
      measures: ['f'],
    }),
  )
  const request = vi.spyOn(metadataApi, 'request').mockImplementation(async (path) => {
    if (path.endsWith('/options'))
      return {
        buildId: 'new',
        dimensionMembers: [],
        transactions: [],
        dimensions: [
          {
            dimensionId: 'base',
            name: 'PLW',
            columns: [
              {
                columnId: 'product',
                name: 'Product',
                aliases: [{ transactionId: 'forecast', columnId: 'product_key' }],
              },
            ],
          },
        ],
        measures: options.measures,
      } as never
    return result as never
  })
  render(<DecisionViewPanel base={scope} revisionVersion={2} />)
  expect(
    await screen.findByRole('button', { name: 'Remove PLW · Product from rows' }),
  ).toBeVisible()
  await waitFor(() =>
    expect(
      request.mock.calls.some(
        ([path, , payload]) =>
          path.endsWith('/query') && JSON.stringify(payload).includes('"dimensionId":"base"'),
      ),
    ).toBe(true),
  )
})
