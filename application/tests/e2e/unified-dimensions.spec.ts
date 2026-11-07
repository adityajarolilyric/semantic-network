import { expect, test } from '@playwright/test'

test('use a Product Dimension directly as transaction grain and plot its measure', async ({
  page,
}) => {
  test.setTimeout(120_000)
  const model = 'unified_dimensions_ui'
  const root = `/api/v1/models/${model}/revisions/draft_1`
  async function post(path: string, data: unknown) {
    const response = await page.request.post(path, {
      data,
      headers: { 'Idempotency-Key': 'unified_' + crypto.randomUUID() },
    })
    expect(response.ok(), await response.text()).toBeTruthy()
    return response.json()
  }
  await post('/api/v1/models', { modelId: model, name: model, displayName: model })
  await post(`/api/v1/models/${model}/revisions`, {
    revisionId: 'draft_1',
    expectedModelVersion: 1,
    metadataContractVersion: 2,
  })
  for (const [tableName, csv] of [
    ['UI_ProductsUnified', 'product_id,brand\nP1,A\nP2,B\n'],
    ['UI_TargetsUnified', 'product_id,target_qty\nP1,120\nP2,60\n'],
  ]) {
    const response = await page.request.post('/api/v1/warehouse/imports/csv', {
      headers: { 'Idempotency-Key': 'unified_' + crypto.randomUUID() },
      multipart: {
        tableName,
        file: { name: 'data.csv', mimeType: 'text/csv', buffer: Buffer.from(csv) },
      },
    })
    expect(response.ok(), await response.text()).toBeTruthy()
    const run = await post(root + '/managed-tables/enable', { tableName })
    await expect
      .poll(
        async () =>
          (await (await page.request.get(root + '/engine/runs/' + run.runId)).json()).state,
      )
      .toBe('succeeded')
  }
  await page.goto(`/?model=${model}&revision=draft_1&tab=dimensions`)
  await expect(
    page.locator('nav').getByRole('button', { name: 'Dimensions', exact: true }),
  ).toHaveCount(1)
  await page.getByRole('button', { name: 'Create dimension', exact: true }).click()
  await page.getByLabel('Dimension name', { exact: true }).fill('Product')
  await page
    .getByLabel('Managed table', { exact: true })
    .selectOption({ label: 'UI_ProductsUnified' })
  await page.getByLabel('Key product_id', { exact: true }).check()
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write'])
  await page.getByRole('button', { name: 'View SQL', exact: true }).click()
  const sqlDialog = page.getByRole('dialog', { name: 'Final SQL', exact: true })
  await expect(sqlDialog.getByLabel('Final ClickHouse SQL')).toContainText('product_id')
  await sqlDialog.getByRole('button', { name: 'Copy SQL', exact: true }).click()
  await expect(sqlDialog.getByRole('status')).toHaveText('Copied')
  const sql = await sqlDialog.getByLabel('Final ClickHouse SQL').textContent()
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(sql)
  await page.screenshot({ path: 'test-results/final-sql.png' })
  await sqlDialog.getByRole('button', { name: 'Close', exact: true }).click()

  await page.getByRole('button', { name: 'Save metadata', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Metadata editor' })).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'Product', exact: true })).toHaveCount(1)
  await page.getByLabel('Base Dimension', { exact: true }).selectOption({ label: 'Product' })
  await expect(page.getByText('Base Dimension saved.', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: /Build all/ })).toHaveCount(0)
  await page.getByRole('button', { name: 'Transactions', exact: true }).click()
  await page.getByRole('button', { name: 'Create transaction', exact: true }).click()
  await page.getByLabel('Name', { exact: true }).fill('Targets')
  await page
    .getByLabel('Managed table', { exact: true })
    .selectOption({ label: 'UI_TargetsUnified' })
  await page.getByRole('radio', { name: 'Bind to a Dimension', exact: true }).check()
  await page.getByLabel('Dimension', { exact: true }).selectOption({ label: 'Product' })
  await page.getByLabel('Product.product_id', { exact: true }).selectOption({ label: 'product_id' })
  await page.getByRole('button', { name: 'View SQL', exact: true }).click()
  await expect(sqlDialog.getByLabel('Final ClickHouse SQL')).toContainText('target_qty')
  await sqlDialog.getByRole('button', { name: 'Close', exact: true }).click()

  await page
    .locator('.transformation-section > summary')
    .filter({ hasText: /^Measures/ })
    .click()
  const grid = page.locator('.transaction-measures-table')
  await grid.getByLabel('target_qty', { exact: true }).check()
  await grid.getByLabel('target_qty non-time aggregation').selectOption('sum')
  await grid.getByLabel('target_qty time aggregation').selectOption('sum')
  await page.getByRole('button', { name: 'Save metadata', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Metadata editor' })).toHaveCount(0)
  await page.locator('nav').getByRole('button', { name: 'Decision View', exact: true }).click()
  const build = page
    .getByRole('region', { name: 'Decision View', exact: true })
    .getByRole('button', { name: 'Build Semantic Network', exact: true })
  await build.click()
  await expect(build).toBeEnabled()
  await expect(page.getByRole('alert')).toHaveCount(0)
  await page.getByRole('button', { name: 'View SQL', exact: true }).click()
  await expect(sqlDialog.getByLabel('Final ClickHouse SQL')).toContainText('LEFT JOIN')
  await sqlDialog.getByRole('button', { name: 'Close', exact: true }).click()

  await page.getByRole('button', { name: 'Decision View', exact: true }).click()
  await page.getByLabel('Add rows attribute').selectOption({ label: 'Product · product_id' })
  await page.getByLabel('target_qty', { exact: true }).check()
  await page.getByRole('button', { name: 'Update pivot', exact: true }).click()
  await expect(page.getByRole('cell', { name: '120.00', exact: true })).toBeVisible()
  await expect(page.getByRole('cell', { name: '60.00', exact: true })).toBeVisible()
  await page.screenshot({ path: 'test-results/unified-dimensions.png', fullPage: true })
})
