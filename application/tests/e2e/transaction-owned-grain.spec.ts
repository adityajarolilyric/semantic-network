import { expect, test } from '@playwright/test'

test('standalone Transaction grain supports measures, builds, pivot and quick calculation without Dimensions', async ({
  page,
}) => {
  test.setTimeout(120_000)
  const model = 'transaction_grain_ui'
  const root = `/api/v1/models/${model}/revisions/draft_1`
  async function post(path: string, data: unknown) {
    const response = await page.request.post(path, {
      data,
      headers: { 'Idempotency-Key': crypto.randomUUID() },
    })
    expect(response.ok(), await response.text()).toBeTruthy()
    return response.json()
  }
  await post('/api/v1/models', { modelId: model, name: model, displayName: model })
  await post(`/api/v1/models/${model}/revisions`, {
    revisionId: 'draft_1',
    expectedModelVersion: 1,
  })
  const imported = await page.request.post('/api/v1/warehouse/imports/csv', {
    headers: { 'Idempotency-Key': crypto.randomUUID() },
    multipart: {
      tableName: 'StandaloneSalesUI',
      file: {
        name: 'sales.csv',
        mimeType: 'text/csv',
        buffer: Buffer.from(
          'product,location,qty,value\nP1,L1,10,200\nP1,L1,20,400\nP2,L1,5,150\n',
        ),
      },
    },
  })
  expect(imported.ok(), await imported.text()).toBeTruthy()
  const managed = await post(root + '/managed-tables/enable', { tableName: 'StandaloneSalesUI' })
  await expect
    .poll(
      async () =>
        (await (await page.request.get(root + '/engine/runs/' + managed.runId)).json()).state,
    )
    .toBe('succeeded')
  await page.goto(`/?model=${model}&revision=draft_1&tab=transactions`)
  await page.getByRole('button', { name: 'Create transaction', exact: true }).click()
  await page.getByLabel('Name', { exact: true }).fill('Sales')
  await page
    .getByLabel('Managed table', { exact: true })
    .selectOption({ label: 'StandaloneSalesUI' })
  await expect(page.getByRole('radio', { name: 'Grain keys only', exact: true })).toBeChecked()
  await expect(page.getByLabel('Dimension name', { exact: true })).toHaveCount(0)
  const grain = page.getByRole('group', { name: 'Transaction grain columns', exact: true })
  await grain.getByLabel('product', { exact: true }).check()
  await grain.getByLabel('location', { exact: true }).check()
  await page
    .locator('.transformation-section > summary')
    .filter({ hasText: /^Measures/ })
    .click()
  const grid = page.locator('.transaction-measures-table')
  for (const name of ['qty', 'value']) {
    await grid.getByLabel(name, { exact: true }).check()
    await grid.getByLabel(name + ' non-time aggregation').selectOption('sum')
    await grid.getByLabel(name + ' time aggregation').selectOption('sum')
  }
  await page.getByRole('button', { name: 'View SQL', exact: true }).click()
  const sql = page.getByRole('dialog', { name: 'Final SQL', exact: true })
  await expect(sql.getByLabel('Final ClickHouse SQL')).toContainText('qty')
  await sql.getByRole('button', { name: 'Close', exact: true }).click()
  await page.getByRole('button', { name: 'Save metadata', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Metadata editor' })).toHaveCount(0)
  expect((await (await page.request.get(root + '/dimensions')).json()).items).toEqual([])
  expect((await (await page.request.get(root)).json()).baseDimensionId).toBeNull()
  await page.locator('nav').getByRole('button', { name: 'Decision View', exact: true }).click()
  const build = page
    .getByRole('region', { name: 'Decision View', exact: true })
    .getByRole('button', { name: 'Build Semantic Network', exact: true })
  await expect(build).toBeEnabled()
  await build.click()
  await expect(build).toBeEnabled()
  await expect(page.getByRole('alert')).toHaveCount(0)
  await page.locator('nav').getByRole('button', { name: 'Decision View', exact: true }).click()
  await page.getByLabel('Add rows attribute').selectOption({ label: 'Sales · product' })
  await page.getByLabel('qty', { exact: true }).check()
  await page.getByRole('button', { name: 'Update pivot', exact: true }).click()
  await expect(page.getByRole('cell', { name: '30.00', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Create Measure', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: 'Create Measure', exact: true })
  await dialog.getByLabel('Formula', { exact: true }).fill('price = [value]/[qty]')
  await dialog.getByRole('button', { name: 'Save metadata', exact: true }).click()
  await expect(dialog).toHaveCount(0)
  await page.getByLabel('price', { exact: true }).check()
  await page.getByRole('button', { name: 'Update pivot', exact: true }).click()
  await expect(page.getByRole('cell', { name: '20.00', exact: true })).toBeVisible()
  await expect(page.getByRole('cell', { name: '30.00', exact: true })).toBeVisible()
  await page.screenshot({ path: 'test-results/transaction-owned-grain.png', fullPage: true })
})
