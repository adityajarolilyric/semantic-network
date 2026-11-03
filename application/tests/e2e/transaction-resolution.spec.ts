import { test, expect } from '@playwright/test'

test('configure, preview and reopen a multi-grain transaction', async ({ page }) => {
  test.setTimeout(120_000)
  await page.goto('/')
  const model = 'resolution_ui_model'
  const base = `/api/v1/models/${model}/revisions/draft_1`
  async function post(path: string, data: unknown) {
    const response = await page.request.post(path, {
      data,
      headers: { 'If-Match': '1', 'Idempotency-Key': 'resolution_' + crypto.randomUUID() },
    })
    expect(response.ok(), await response.text()).toBeTruthy()
    return response.json()
  }
  await post('/api/v1/models', { modelId: model, name: 'Resolution', displayName: 'Resolution' })
  await post(`/api/v1/models/${model}/revisions`, {
    revisionId: 'draft_1',
    expectedModelVersion: 1,
    metadataContractVersion: 2,
  })
  for (const [name, csv] of [
    ['ResolutionPopulation', 'product,brand\nP1,A\nP2,A\n'],
    ['ResolutionAssignments', 'level,member,quantity,sequence\nbrand,A,100,10\n'],
  ]) {
    const response = await page.request.post('/api/v1/warehouse/imports/csv', {
      headers: { 'Idempotency-Key': 'csv_' + name },
      multipart: {
        tableName: name,
        file: { name: name + '.csv', mimeType: 'text/csv', buffer: Buffer.from(csv) },
      },
    })
    expect(response.ok(), await response.text()).toBeTruthy()
    const managed = await post(base + '/managed-tables/enable', { tableName: name })
    await expect
      .poll(
        async () =>
          (await (await page.request.get(base + '/engine/runs/' + managed.runId)).json()).state,
      )
      .toBe('succeeded')
  }
  const sources = (await (await page.request.get(base + '/sources')).json()).items
  const source = (name: string) => sources.find((s: { name: string }) => s.name === name).objectId
  await post(base + '/planning-levels', {
    objectId: 'native',
    kind: 'planningLevel',
    name: 'Product',
    displayName: 'Product',
    definition: {
      population: { operation: 'source', sourceId: source('ResolutionPopulation') },
      grain: {
        keyIds: ['product'],
        columns: [
          { grainColumnId: 'product', name: 'product', populationRef: { columnId: 'col_1' } },
        ],
      },
      populationAttributes: [
        { columnId: 'brand', name: 'brand', populationRef: { columnId: 'col_2' } },
      ],
      dimensionBindings: [],
    },
  })
  const run = await post(base + '/planning-levels/native/build', {})
  await expect
    .poll(
      async () => (await (await page.request.get(base + '/engine/runs/' + run.runId)).json()).state,
    )
    .toBe('succeeded')
  await page.goto(`/?model=${model}&revision=draft_1&tab=transactions`)
  await page
    .getByRole('navigation', { name: 'Workspace', exact: true })
    .getByRole('button', { name: 'Transactions', exact: true })
    .click()
  await page.getByRole('button', { name: 'Create transaction', exact: true }).click()
  await page.getByLabel('Name', { exact: true }).fill('Mixed Quantity')
  await page
    .getByLabel('Managed table', { exact: true })
    .selectOption({ label: 'ResolutionAssignments' })
  await page.getByLabel('Native planning level', { exact: true }).selectOption({ label: 'Product' })
  await page.getByRole('button', { name: 'Raw Data Preview', exact: true }).click()
  await expect(page.locator('th .column-name').filter({ hasText: /^level$/ })).toBeVisible()
  await page.getByRole('button', { name: 'Close raw preview', exact: true }).click()
  await page.getByLabel('Pivot long-format columns', { exact: true }).check()
  await page.getByLabel('Attribute column', { exact: true }).selectOption({ label: 'level' })
  await page.getByLabel('Value column', { exact: true }).selectOption({ label: 'member' })
  await page.getByRole('button', { name: 'Discover', exact: true }).click()
  await page.getByLabel('Transaction mapping', { exact: true }).selectOption('resolve_multi_grain')
  await page.getByRole('button', { name: 'Add matching rule', exact: true }).click()
  await page.getByLabel('Match source column', { exact: true }).selectOption({ label: 'brand' })
  await page
    .getByLabel('Match native attribute', { exact: true })
    .selectOption({ label: 'Native · brand' })
  await expect(page.getByLabel('Match using')).toHaveCount(0)
  await page.getByLabel('Apply-order column', { exact: true }).selectOption({ label: 'sequence' })
  await page.getByRole('checkbox', { name: 'Resolve quantity', exact: true }).check()
  await page.getByRole('button', { name: 'Add filter', exact: true }).click()
  await page.getByLabel('Filter column', { exact: true }).selectOption({ label: 'quantity' })
  await page.getByLabel('Filter operator', { exact: true }).selectOption('gte')
  await page.getByLabel('Filter value', { exact: true }).fill('80')
  await page.getByRole('button', { name: 'Preview', exact: true }).click()
  await expect(page.getByText('0/0 rows', { exact: true })).toBeVisible()
  await page.getByLabel('Filter value', { exact: true }).fill('40')
  await page.getByRole('button', { name: 'Preview', exact: true }).click()
  await expect(page.getByRole('status').filter({ hasText: '1 input rows' })).toContainText(
    '2 winning rows',
  )
  await expect(page.getByRole('cell', { name: '50.00', exact: true })).toHaveCount(2)
  await expect(
    page.getByRole('table').filter({ has: page.getByRole('cell', { name: '50.00', exact: true }) }),
  ).toHaveClass('data-table')
  await page.screenshot({ path: 'test-results/transaction-resolution-desktop.png', fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  const overflow = await page.evaluate(() =>
    Array.from(document.querySelectorAll('main *'))
      .filter((e) => e.getBoundingClientRect().right > window.innerWidth + 1)
      .map((e) => ({
        tag: e.tagName,
        className: e.className,
        right: e.getBoundingClientRect().right,
      })),
  )
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    JSON.stringify(overflow),
  ).toBeTruthy()
  await page.screenshot({ path: 'test-results/transaction-resolution-mobile.png', fullPage: true })
  await page.getByRole('button', { name: 'Save metadata', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Metadata editor', exact: true })).toHaveCount(0)
  await expect(
    page.getByText('Multi-grain resolution · 1 native key · 1 value column', { exact: true }),
  ).toBeVisible()
  await page.reload()
  await page
    .locator('.meta-object-card')
    .filter({ has: page.getByRole('heading', { name: 'Mixed Quantity', exact: true }) })
    .getByRole('button', { name: 'Edit', exact: true })
    .click()
  await expect(page.getByLabel('Transaction mapping', { exact: true })).toHaveValue(
    'resolve_multi_grain',
  )
  await expect(page.getByRole('checkbox', { name: 'Resolve quantity', exact: true })).toBeChecked()
  await expect(page.getByLabel('Apply-order column', { exact: true })).toHaveValue('col_4')
  await expect(page.getByLabel('Filter value', { exact: true })).toHaveValue('40')
  await page.getByRole('button', { name: 'Preview', exact: true }).click()
  await expect(page.getByRole('cell', { name: '50.00', exact: true })).toHaveCount(2)
})

test('resolve daily ranges into weekly keys, save and reopen', async ({ page }) => {
  test.setTimeout(120_000)
  await page.goto('/')
  const model = 'range_ui_model',
    base = `/api/v1/models/${model}/revisions/draft_1`
  async function post(path: string, data: unknown) {
    const response = await page.request.post(path, {
      data,
      headers: { 'If-Match': '1', 'Idempotency-Key': 'range_' + crypto.randomUUID() },
    })
    expect(response.ok(), await response.text()).toBeTruthy()
    return response.json()
  }
  await post('/api/v1/models', { modelId: model, name: 'Ranges', displayName: 'Ranges' })
  await post(`/api/v1/models/${model}/revisions`, {
    revisionId: 'draft_1',
    expectedModelVersion: 1,
    metadataContractVersion: 2,
  })
  async function waitRun(run: { runId: string }) {
    await expect
      .poll(
        async () =>
          (await (await page.request.get(base + '/engine/runs/' + run.runId)).json()).state,
      )
      .toBe('succeeded')
  }
  for (const [name, csv] of [
    [
      'RangeCalendar',
      'day,week\n2026-01-01,WeekA\n2026-01-02,WeekA\n2026-01-03,WeekA\n2026-01-04,WeekB\n2026-01-05,WeekB\n2026-01-06,WeekB\n2026-01-07,WeekB\n',
    ],
    ['RangePopulation', 'product,week\nP1,WeekA\nP1,WeekB\n'],
    ['RangeAssignments', 'quantity,start_date,end_date,sequence\n70,2026-01-01,2026-01-07,10\n'],
  ]) {
    const response = await page.request.post('/api/v1/warehouse/imports/csv', {
      headers: { 'Idempotency-Key': 'csv_' + name },
      multipart: {
        tableName: name,
        file: { name: name + '.csv', mimeType: 'text/csv', buffer: Buffer.from(csv) },
      },
    })
    expect(response.ok(), await response.text()).toBeTruthy()
    await waitRun(await post(base + '/managed-tables/enable', { tableName: name }))
  }
  const sources = (await (await page.request.get(base + '/sources')).json()).items
  const source = (name: string) => sources.find((s: { name: string }) => s.name === name).objectId
  await post(base + '/dimensions', {
    objectId: 'time',
    kind: 'dimension',
    name: 'Calendar',
    displayName: 'Calendar',
    isTimeDim: true,
    definition: {
      sourceId: source('RangeCalendar'),
      columns: [
        { columnId: 'day', name: 'day', displayName: 'Calendar date', sourceColumnId: 'col_1' },
        { columnId: 'week', name: 'week', displayName: 'Week ID', sourceColumnId: 'col_2' },
      ],
      keyColumnIds: ['day'],
      labelColumnId: 'day',
      attributeColumnIds: ['week'],
      time: { baseGrain: 'day', dateColumnId: 'day', orderingColumnId: 'day' },
    },
  })
  await post(base + '/planning-levels', {
    objectId: 'weekly',
    kind: 'planningLevel',
    name: 'Weekly',
    displayName: 'Weekly',
    definition: {
      population: { operation: 'source', sourceId: source('RangePopulation') },
      grain: {
        keyIds: ['product', 'week'],
        columns: [
          { grainColumnId: 'product', name: 'Product', populationRef: { columnId: 'col_1' } },
          { grainColumnId: 'week', name: 'Week', populationRef: { columnId: 'col_2' } },
        ],
      },
      dimensionBindings: [
        {
          dimensionId: 'time',
          keyMappings: [{ grainColumnId: 'week', dimensionColumnId: 'week' }],
          selectedAttributeColumnIds: [],
        },
      ],
    },
  })
  await waitRun(await post(base + '/planning-levels/weekly/build', {}))
  await page.goto(`/?model=${model}&revision=draft_1&tab=transactions`)
  await page
    .getByRole('navigation', { name: 'Workspace', exact: true })
    .getByRole('button', { name: 'Transactions', exact: true })
    .click()
  await page.getByRole('button', { name: 'Create transaction', exact: true }).click()
  await page.getByLabel('Name', { exact: true }).fill('Weekly promotion')
  await page
    .getByLabel('Managed table', { exact: true })
    .selectOption({ label: 'RangeAssignments' })
  await page.getByLabel('Native planning level', { exact: true }).selectOption({ label: 'Weekly' })
  await page.getByLabel('Transaction mapping', { exact: true }).selectOption('resolve_multi_grain')
  await page.getByLabel('Allow all Product members when blank', { exact: true }).check()
  await page.getByLabel('Resolve a date range using a calendar', { exact: true }).check()
  await page.getByLabel('Start date column', { exact: true }).selectOption({ label: 'start_date' })
  await page.getByLabel('End date column', { exact: true }).selectOption({ label: 'end_date' })
  await page.getByLabel('Daily Time dimension', { exact: true }).selectOption('time')
  await page.getByRole('checkbox', { name: 'Week ID', exact: true }).check()
  await page.getByRole('button', { name: 'Preview calendar data', exact: true }).click()
  await expect(page.getByRole('cell', { name: 'WeekA', exact: true })).toHaveCount(3)
  await page.getByRole('button', { name: 'Close calendar preview', exact: true }).click()
  await page.getByRole('button', { name: 'Add matching rule', exact: true }).nth(1).click()
  await page.getByLabel('Match source column', { exact: true }).selectOption({ label: 'week' })
  await page
    .getByLabel('Match native attribute', { exact: true })
    .selectOption({ label: 'Native · Week' })
  await page.getByLabel('Apply-order column', { exact: true }).selectOption({ label: 'sequence' })
  await page.getByRole('checkbox', { name: 'Resolve quantity', exact: true }).check()
  await page.getByLabel('Across dates policy for quantity', { exact: true }).selectOption('split')
  await page.getByRole('button', { name: 'Preview', exact: true }).click()
  await expect(page.getByRole('status').filter({ hasText: '1 input rows' })).toContainText(
    '7 winning rows',
  )
  await expect(page.getByRole('cell', { name: 'WeekA', exact: true })).toHaveCount(3)
  await expect(page.getByRole('cell', { name: 'WeekB', exact: true })).toHaveCount(4)
  await page.screenshot({ path: 'test-results/date-range-weekly-desktop.png', fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
  ).toBeTruthy()
  await page.screenshot({ path: 'test-results/date-range-weekly-mobile.png', fullPage: true })
  await page.getByRole('button', { name: 'Save metadata', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Metadata editor', exact: true })).toHaveCount(0)
  await page.reload()
  await page
    .locator('.meta-object-card')
    .filter({ has: page.getByRole('heading', { name: 'Weekly promotion', exact: true }) })
    .getByRole('button', { name: 'Edit', exact: true })
    .click()
  await expect(
    page.getByLabel('Resolve a date range using a calendar', { exact: true }),
  ).toBeChecked()
  await expect(page.getByLabel('Start date column', { exact: true })).toHaveValue('col_2')
  await expect(page.getByLabel('End date column', { exact: true })).toHaveValue('col_3')
  await expect(page.getByLabel('Match source column', { exact: true })).not.toHaveValue('')
  await page.getByRole('button', { name: 'Preview', exact: true }).click()
  await expect(page.getByRole('cell', { name: 'WeekA', exact: true })).toHaveCount(3)
})
