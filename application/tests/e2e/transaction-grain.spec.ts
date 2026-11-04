import { test, expect } from '@playwright/test'

test('start from a single Transaction, generate its grain and pivot without Dimensions', async ({
  page,
}) => {
  test.setTimeout(120_000)
  const model = 'table_first_ui'
  const base = `/api/v1/models/${model}/revisions/draft_1`
  await page.goto('/')
  async function post(path: string, data: unknown) {
    const response = await page.request.post(path, {
      data,
      headers: { 'Idempotency-Key': 'onboard_' + crypto.randomUUID() },
    })
    expect(response.ok(), await response.text()).toBeTruthy()
    return response.json()
  }
  await post('/api/v1/models', { modelId: model, name: 'Table first', displayName: 'Table first' })
  await post(`/api/v1/models/${model}/revisions`, {
    revisionId: 'draft_1',
    expectedModelVersion: 1,
    metadataContractVersion: 2,
  })
  const response = await page.request.post('/api/v1/warehouse/imports/csv', {
    headers: { 'Idempotency-Key': 'grain_csv_' + crypto.randomUUID() },
    multipart: {
      tableName: 'UI_TableFirstForecast',
      file: {
        name: 'forecast.csv',
        mimeType: 'text/csv',
        buffer: Buffer.from(
          'product_id,location_id,time_id,forecast_qty\nP1,L1,W1,100\nP1,L1,W1,50\nP2,L1,W1,200\n',
        ),
      },
    },
  })
  expect(response.ok(), await response.text()).toBeTruthy()
  const managed = await post(base + '/managed-tables/enable', {
    tableName: 'UI_TableFirstForecast',
  })
  await expect
    .poll(
      async () =>
        (await (await page.request.get(base + '/engine/runs/' + managed.runId)).json()).state,
    )
    .toBe('succeeded')
  await page.goto(`/?model=${model}&revision=draft_1&tab=transactions`)
  await page.getByRole('button', { name: 'Create transaction', exact: true }).click()
  await page.getByLabel('Name', { exact: true }).fill('Forecast')
  await page
    .getByLabel('Managed table', { exact: true })
    .selectOption({ label: 'UI_TableFirstForecast' })
  await page
    .locator('.transformation-section > summary')
    .filter({ hasText: /^Planning-level mapping/ })
    .click()
  await expect(page.getByRole('radio', { name: 'No Planning Level', exact: true })).toBeChecked()
  const keys = page.getByRole('group', { name: 'Transaction grain columns', exact: true })
  for (const name of ['product_id', 'location_id', 'time_id'])
    await keys.getByLabel(name, { exact: true }).check()
  await page.getByRole('button', { name: 'Preview', exact: true }).click()
  await expect(page.getByText('2/2 rows', { exact: true })).toBeVisible()
  expect((await (await page.request.get(base + '/planning-levels')).json()).items).toHaveLength(0)
  await page.getByRole('button', { name: 'Save metadata', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Metadata editor', exact: true })).toHaveCount(0)
  const levels = (await (await page.request.get(base + '/planning-levels')).json()).items
  expect(levels).toHaveLength(1)
  expect((await (await page.request.get(base + '/dimensions')).json()).items).toHaveLength(0)
  await page.getByRole('button', { name: 'Measures', exact: true }).click()
  await page.getByRole('button', { name: 'Create measure', exact: true }).click()
  await page.getByLabel('Name', { exact: true }).fill('FQTY')
  await page.getByLabel('Transaction', { exact: true }).selectOption({ label: 'Forecast' })
  await page.getByLabel('Value column', { exact: true }).selectOption('col_4')
  await page.getByRole('button', { name: 'Save metadata', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Metadata editor', exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: 'Materialise', exact: true }).click()
  await page.getByRole('button', { name: 'Build Semantic Network', exact: true }).click()
  await expect(
    page.getByRole('button', { name: 'Build Semantic Network', exact: true }),
  ).toBeEnabled()
  await page.getByRole('button', { name: 'Planning View', exact: true }).click()
  await page.getByLabel('Add rows attribute').selectOption({ label: 'Forecast grain · product_id' })
  await page.getByLabel('FQTY', { exact: true }).check()
  await page.getByRole('button', { name: 'Update pivot', exact: true }).click()
  await expect(page.getByRole('cell', { name: '150.00', exact: true })).toBeVisible()
  await expect(page.getByRole('cell', { name: '200.00', exact: true })).toBeVisible()
  await page.screenshot({ path: 'test-results/table-first-pivot.png', fullPage: true })
  await page.reload()
  await expect(page.getByRole('cell', { name: '150.00', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Materialise', exact: true }).click()
  await page.getByRole('button', { name: 'Build Semantic Network', exact: true }).click()
  await expect(
    page.getByRole('button', { name: 'Build Semantic Network', exact: true }),
  ).toBeEnabled()
  await page.getByRole('button', { name: 'Planning View', exact: true }).click()
  await expect(page.getByLabel('FQTY', { exact: true })).toBeChecked()
  await expect(page.getByRole('cell', { name: '150.00', exact: true })).toBeVisible()
  expect((await (await page.request.get(base + '/dimensions')).json()).items).toHaveLength(0)
  const savedTransaction = (await (await page.request.get(base + '/transactions')).json()).items[0]
  const replacementImport = await page.request.post('/api/v1/warehouse/imports/csv', {
    headers: { 'Idempotency-Key': 'population_' + crypto.randomUUID() },
    multipart: {
      tableName: 'UI_TableFirstPopulation',
      file: {
        name: 'population.csv',
        mimeType: 'text/csv',
        buffer: Buffer.from('time_id,product_id,location_id\nW1,P1,L1\nW1,P2,L1\nW1,P3,L2\n'),
      },
    },
  })
  expect(replacementImport.ok(), await replacementImport.text()).toBeTruthy()
  const newManaged = await post(base + '/managed-tables/enable', {
    tableName: 'UI_TableFirstPopulation',
  })
  await expect
    .poll(
      async () =>
        (await (await page.request.get(base + '/engine/runs/' + newManaged.runId)).json()).state,
    )
    .toBe('succeeded')
  await page.reload()
  await page.getByRole('button', { name: 'Planning Levels', exact: true }).click()
  await page
    .locator('.meta-object-card')
    .filter({ has: page.getByRole('heading', { name: 'Forecast grain', exact: true }) })
    .getByRole('button', { name: 'Edit', exact: true })
    .click()
  await page
    .getByLabel('Population table', { exact: true })
    .selectOption({ label: 'UI_TableFirstPopulation' })
  for (const name of ['product_id', 'location_id', 'time_id'])
    await page
      .getByLabel('Population column for ' + name, { exact: true })
      .selectOption({ label: name })
  await page.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Metadata editor', exact: true })).toHaveCount(0)
  const after = (await (await page.request.get(base + '/transactions')).json()).items[0]
  expect(after.definition.keyMappings).toEqual(savedTransaction.definition.keyMappings)
  const population = await (
    await page.request.get(base + '/planning-levels/' + levels[0].objectId + '/table?preview=true')
  ).json()
  expect(population.rowCount).toBe(3)
})

test('No Planning Level derives grain from Pivot then Date resolution and reopens it', async ({
  page,
}) => {
  test.setTimeout(90_000)
  const model = 'transformed_table_first_ui'
  const base = `/api/v1/models/${model}/revisions/draft_1`
  await page.goto('/')
  async function post(path: string, data: unknown) {
    const response = await page.request.post(path, {
      data,
      headers: { 'Idempotency-Key': 'onboard_' + crypto.randomUUID() },
    })
    expect(response.ok(), await response.text()).toBeTruthy()
    return response.json()
  }
  await post('/api/v1/models', {
    modelId: model,
    name: 'Transformed first',
    displayName: 'Transformed first',
  })
  await post(`/api/v1/models/${model}/revisions`, {
    revisionId: 'draft_1',
    expectedModelVersion: 1,
    metadataContractVersion: 2,
  })
  const sources: Record<string, string> = {}
  for (const [table, csv] of [
    [
      'UI_PromotionsFirst',
      'attribute,member,start_date,end_date,qty\nproduct_id,P1,2026-01-01,2026-01-03,90\nproduct_id,P2,2026-01-02,2026-01-03,60\n',
    ],
    ['UI_CalendarFirst', 'day,week\n2026-01-01,WA\n2026-01-02,WA\n2026-01-03,WB\n'],
  ]) {
    const imported = await page.request.post('/api/v1/warehouse/imports/csv', {
      headers: { 'Idempotency-Key': 'onboard_' + crypto.randomUUID() },
      multipart: {
        tableName: table,
        file: { name: table + '.csv', mimeType: 'text/csv', buffer: Buffer.from(csv) },
      },
    })
    expect(imported.ok(), await imported.text()).toBeTruthy()
    const managed = await post(base + '/managed-tables/enable', { tableName: table })
    await expect
      .poll(
        async () =>
          (await (await page.request.get(base + '/engine/runs/' + managed.runId)).json()).state,
      )
      .toBe('succeeded')
    sources[table] = managed.sourceId
  }
  await post(base + '/dimensions', {
    objectId: 'Time',
    kind: 'dimension',
    name: 'Time',
    displayName: 'Time',
    isTimeDim: true,
    definition: {
      sourceId: sources.UI_CalendarFirst,
      columns: [
        { columnId: 'day', name: 'day', displayName: 'Day', sourceColumnId: 'col_1' },
        { columnId: 'week', name: 'week', displayName: 'Week', sourceColumnId: 'col_2' },
      ],
      keyColumnIds: ['day'],
      labelColumnId: 'day',
      attributeColumnIds: ['week'],
      time: { baseGrain: 'day', dateColumnId: 'day', orderingColumnId: 'day' },
    },
  })
  await page.goto(`/?model=${model}&revision=draft_1&tab=transactions`)
  await page.getByRole('button', { name: 'Create transaction', exact: true }).click()
  await page.getByLabel('Name', { exact: true }).fill('Promotions')
  await page
    .getByLabel('Managed table', { exact: true })
    .selectOption({ label: 'UI_PromotionsFirst' })
  await expect(page.getByLabel('Native planning level', { exact: true })).toHaveCount(0)
  await page
    .locator('.transformation-section > summary')
    .filter({ hasText: /^Pivot transformation/ })
    .click()
  await page.getByLabel('Pivot long-format columns', { exact: true }).check()
  await page.getByLabel('Attribute column', { exact: true }).selectOption({ label: 'attribute' })
  await page.getByLabel('Value column', { exact: true }).selectOption({ label: 'member' })
  await page.getByRole('button', { name: 'Discover', exact: true }).click()
  await page
    .locator('.transformation-section > summary')
    .filter({ hasText: /^Date resolution/ })
    .click()
  await page.getByLabel('Resolve a date range using a calendar', { exact: true }).check()
  await page.getByLabel('Start date column', { exact: true }).selectOption({ label: 'start_date' })
  await page.getByLabel('End date column', { exact: true }).selectOption({ label: 'end_date' })
  await page.getByLabel('Daily Time dimension', { exact: true }).selectOption({ label: 'Time' })
  await page
    .getByRole('group', { name: 'Calendar columns to bring in', exact: true })
    .getByLabel('Week', { exact: true })
    .check()
  await page
    .locator('.transformation-section > summary')
    .filter({ hasText: /^Planning-level mapping/ })
    .click()
  await expect(page.getByRole('radio', { name: 'No Planning Level', exact: true })).toBeChecked()
  const keys = page.getByRole('group', { name: 'Transaction grain columns', exact: true })
  await keys.getByLabel('product_id', { exact: true }).check()
  await keys.getByLabel('week', { exact: true }).check()
  await expect(keys.getByLabel('attribute', { exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: 'Preview', exact: true }).click()
  await expect(page.getByText('4/4 rows', { exact: true })).toBeVisible()
  await page.screenshot({ path: 'test-results/no-level-pivot-calendar.png', fullPage: true })
  await page.getByRole('button', { name: 'Save metadata', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Metadata editor', exact: true })).toHaveCount(0)
  const level = (await (await page.request.get(base + '/planning-levels')).json()).items[0]
  expect(level.definition.population.inputTransform.calendarResolution.dimensionId).toBe('Time')
  await page.getByRole('button', { name: 'Planning Levels', exact: true }).click()
  await page
    .locator('.meta-object-card')
    .filter({ has: page.getByRole('heading', { name: 'Promotions grain', exact: true }) })
    .getByRole('button', { name: 'Edit', exact: true })
    .click()
  const savedKeys = page.getByRole('group', { name: 'Grain columns', exact: true })
  await expect(savedKeys.getByLabel('product_id', { exact: true })).toBeChecked()
  await expect(savedKeys.getByLabel('week', { exact: true })).toBeChecked()
  await page.getByRole('button', { name: 'Preview', exact: true }).click()
  await expect(page.getByText('4/4 rows', { exact: true })).toBeVisible()
})
