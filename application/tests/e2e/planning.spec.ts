import { test, expect } from '@playwright/test'

test('create a planning table with population and dimension attributes, preview and reopen', async ({
  page,
}) => {
  test.setTimeout(90_000)
  await page.goto('/')
  const model = 'planning_ui_model',
    base = `/api/v1/models/${model}/revisions/draft_1`
  expect(
    (
      await page.request.post('/api/v1/models', {
        data: { modelId: model, name: 'Planning', displayName: 'Planning' },
      })
    ).ok(),
  ).toBeTruthy()
  expect(
    (
      await page.request.post(`/api/v1/models/${model}/revisions`, {
        data: { revisionId: 'draft_1', expectedModelVersion: 1, metadataContractVersion: 2 },
      })
    ).ok(),
  ).toBeTruthy()
  for (const [table, csv] of [
    ['UI_Intersection', 'product,priority\nP1,High\nP2,Low\n'],
    ['UI_ProductDim', 'product,category\nP1,C1\nP2,C2\n'],
  ]) {
    expect(
      (
        await page.request.post('/api/v1/warehouse/imports/csv', {
          headers: { 'Idempotency-Key': 'csv_' + table },
          multipart: {
            tableName: table,
            file: { name: table + '.csv', mimeType: 'text/csv', buffer: Buffer.from(csv) },
          },
        })
      ).ok(),
    ).toBeTruthy()
    const response = await page.request.post(base + '/managed-tables/enable', {
      headers: { 'Idempotency-Key': 'manage_' + table },
      data: { tableName: table },
    })
    const run = await response.json()
    await expect
      .poll(
        async () =>
          (await (await page.request.get(base + '/engine/runs/' + run.runId)).json()).state,
      )
      .toBe('succeeded')
  }
  const sources = (await (await page.request.get(base + '/sources')).json()).items
  const dimSource = sources.find((s: { name: string }) => s.name === 'UI_ProductDim')
  expect(
    (
      await page.request.post(base + '/dimensions', {
        data: {
          objectId: 'dim_product',
          kind: 'dimension',
          name: 'Product',
          displayName: 'Product',
          isTimeDim: false,
          definition: {
            sourceId: dimSource.objectId,
            columns: [
              {
                columnId: 'product',
                name: 'product',
                displayName: 'Product ID',
                sourceColumnId: 'col_1',
              },
              {
                columnId: 'category',
                name: 'category',
                displayName: 'Category',
                sourceColumnId: 'col_2',
              },
            ],
            keyColumnIds: ['product'],
            labelColumnId: 'product',
            attributeColumnIds: ['category'],
          },
        },
      })
    ).ok(),
  ).toBeTruthy()
  await page.goto(`/?model=${model}&revision=draft_1&tab=levels`)
  const nav = page.getByRole('navigation', { name: 'Workspace', exact: true })
  await nav.getByRole('button', { name: 'Planning Levels', exact: true }).click()
  await page.getByRole('button', { name: 'Create planning level', exact: true }).click()
  await page.getByLabel('Name', { exact: true }).fill('Product Planning')
  await page
    .getByLabel('Population table', { exact: true })
    .selectOption({ label: 'UI_Intersection' })
  await page
    .getByRole('group', { name: 'Grain columns', exact: true })
    .getByLabel('product', { exact: true })
    .check()
  await page
    .getByRole('group', { name: 'Population attributes from UI_Intersection', exact: true })
    .getByLabel('priority', { exact: true })
    .check()
  await expect(page.getByRole('tablist', { name: 'Dimension bindings', exact: true })).toHaveCount(
    0,
  )
  await page.getByLabel('Add dimension', { exact: true }).selectOption({ label: 'Product' })
  await page
    .getByLabel('Product planning-level column 1', { exact: true })
    .selectOption({ label: 'product' })
  await page
    .getByLabel('Product dimension column 1', { exact: true })
    .selectOption({ label: 'Product ID' })
  await page
    .getByRole('group', { name: 'Attributes from Product', exact: true })
    .getByLabel('Category', { exact: true })
    .check()
  let builds = 0
  page.on('request', (request) => {
    if (request.url().endsWith('/planning-levels/editor/draft/build')) builds++
  })
  await page.getByRole('button', { name: 'Preview', exact: true }).click()
  await expect(
    page.getByText('Built preview (metadata not saved): 2 rows.', { exact: false }),
  ).toBeVisible()
  expect((await (await page.request.get(base + '/planning-levels')).json()).items).toHaveLength(0)
  await page.getByRole('button', { name: 'Save', exact: true }).click()
  await page
    .locator('.meta-object-card')
    .filter({ has: page.getByRole('heading', { name: 'Product Planning', exact: true }) })
    .getByRole('button', { name: 'Edit', exact: true })
    .click()
  expect(builds).toBe(1)
  const panel = page.getByLabel('Planning table Product Planning', { exact: true })
  await expect(panel.getByRole('status')).toContainText('Table: ready')
  await page.getByRole('button', { name: 'Preview', exact: true }).click()
  expect(builds).toBe(1)
  await expect(page.getByLabel('Planning level actions').getByRole('button')).toHaveText([
    'Cancel',
    'Preview',
    'Save',
  ])
  await expect(page.getByRole('cell', { name: 'High', exact: true })).toBeVisible()
  await expect(page.getByRole('cell', { name: 'C1', exact: true })).toBeVisible()
  await expect(page.getByRole('table')).toHaveClass('data-table')
  await expect(page.getByText('Showing 2 rows (maximum 100)', { exact: true })).toBeVisible()
  await expect(page.getByLabel('Rows per page')).toHaveCount(0)
  await page.screenshot({ path: 'test-results/planning-level-editor.png', fullPage: true })
  await page.reload()
  await page
    .locator('.meta-object-card')
    .filter({ has: page.getByRole('heading', { name: 'Product Planning', exact: true }) })
    .getByRole('button', { name: 'Edit', exact: true })
    .click()
  await expect(
    page
      .getByRole('group', { name: 'Population attributes from UI_Intersection', exact: true })
      .getByLabel('priority', { exact: true }),
  ).toBeChecked()
  await expect(
    page
      .getByRole('group', { name: 'Attributes from Product', exact: true })
      .getByLabel('Category', { exact: true }),
  ).toBeChecked()
  await page.getByRole('button', { name: 'Cancel', exact: true }).click()
  await page
    .getByLabel('Base planning level', { exact: true })
    .selectOption({ label: 'Product Planning' })
  await expect(page.getByText('Loading saved metadata…', { exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: 'Create planning level', exact: true }).click()
  await page.getByLabel('Name', { exact: true }).fill('Category Planning')
  await page.getByLabel('Population method', { exact: true }).selectOption('distinct_projection')
  await page
    .getByLabel('Parent planning level', { exact: true })
    .selectOption({ label: 'Product Planning' })
  const retained = page.getByRole('group', { name: 'Retain parent keys', exact: true })
  const attributes = page.getByRole('group', { name: 'Attributes from parent level', exact: true })
  await expect(attributes.getByLabel('Category', { exact: true })).toBeVisible()
  await expect(attributes.getByLabel('product', { exact: true })).toHaveCount(0)
  const retainedBox = await retained.boundingBox(),
    attributesBox = await attributes.boundingBox()
  expect(retainedBox!.x).toBeLessThan(attributesBox!.x)
  await page
    .getByRole('group', { name: 'Use parent attributes as grain keys', exact: true })
    .getByLabel('Category', { exact: true })
    .check()
  await page.getByLabel('Add dimension', { exact: true }).selectOption({ label: 'Product' })
  await page
    .getByLabel('Product planning-level column 1', { exact: true })
    .selectOption({ label: 'Category' })
  await page
    .getByLabel('Product dimension column 1', { exact: true })
    .selectOption({ label: 'Category' })
  await page.screenshot({ path: 'test-results/attribute-grain-projection.png', fullPage: true })
  await page.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Metadata editor', exact: true })).toHaveCount(0)
  await page
    .locator('.meta-object-card')
    .filter({ has: page.getByRole('heading', { name: 'Category Planning', exact: true }) })
    .getByRole('button', { name: 'Edit', exact: true })
    .click()
  const childPanel = page.getByLabel('Planning table Category Planning', { exact: true })
  await expect(childPanel.getByRole('status')).toContainText('Table: ready')
  await page.getByRole('button', { name: 'Preview', exact: true }).click()
  await expect(page.getByRole('cell', { name: 'C1', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Cancel', exact: true }).click()
  const savedLevels = (await (await page.request.get(base + '/planning-levels')).json()).items
  const parent = savedLevels.find((level: { name: string }) => level.name === 'Product Planning')
  const child = savedLevels.find((level: { name: string }) => level.name === 'Category Planning')
  const submitted: string[] = []
  page.on('request', (request) => {
    if (request.method() === 'POST' && /\/planning-levels\/[^/]+\/build$/.test(request.url()))
      submitted.push(request.url().split('/').at(-2)!)
  })
  await page.getByRole('button', { name: 'Build all planning levels', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Reload metadata' })).toBeDisabled()
  await expect(
    page.getByText('All planning levels built. Rebuild measure values in Materialise.', {
      exact: true,
    }),
  ).toBeVisible({ timeout: 30_000 })
  expect(submitted).toEqual([parent.objectId, child.objectId])
  for (const level of [parent, child]) {
    const status = await (
      await page.request.get(`${base}/planning-levels/${level.objectId}/table`)
    ).json()
    expect(status.state).toBe('ready')
    const saved = await (await page.request.get(`${base}/planning-levels/${level.objectId}`)).json()
    expect(saved.version).toBe(level.version)
  }
})
