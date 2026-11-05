import { expect, test } from '@playwright/test'

test.beforeEach(async ({ page }) => {
  await page.goto('/', { waitUntil: 'domcontentloaded' })
  await expect(page.getByText('ClickHouse connected')).toBeVisible()
})

test('upload, inferred columns and real ClickHouse preview', async ({ page }) => {
  await page.getByLabel('CSV files', { exact: true }).setInputFiles({
    name: 'Forecast.csv',
    mimeType: 'text/csv',
    buffer: Buffer.from(
      'product_id,location_id,quantity,date,notes\n001,L1,12.50,2026-10-31,"Café, small"\n002,L2,7.25,2026-11-01,\n',
    ),
  })
  await page.getByLabel('Table name', { exact: true }).fill('Forecast_e2e')
  await page.getByRole('button', { name: 'Upload & create table' }).click()
  await expect(page.getByText('2 rows · 5 columns · ready to preview')).toBeVisible()
  const preview = page.getByRole('region', { name: 'Forecast_e2e rows', exact: true })
  await expect(preview.getByText('001', { exact: true })).toBeVisible()
  await expect(preview.getByText('12.50', { exact: true })).toBeVisible()
  await expect(preview.getByText('Café, small', { exact: true })).toBeVisible()
  await expect(preview.getByText('null', { exact: true })).toBeVisible()
  await expect(preview.getByText(/Decimal\(38,\s*2\)/)).toBeVisible()
  await page.screenshot({ path: 'test-results/sources-desktop.png', fullPage: true })
})

test('pagination and persistence after a page reload', async ({ page }) => {
  const content =
    'product_id,quantity\n' +
    Array.from({ length: 60 }, (_, i) => `P${String(i + 1).padStart(3, '0')},${i + 1}`).join('\n')
  await page
    .getByLabel('CSV files', { exact: true })
    .setInputFiles({ name: 'Sales.csv', mimeType: 'text/csv', buffer: Buffer.from(content) })
  await page.getByLabel('Table name', { exact: true }).fill('Sales_e2e')
  await page.getByRole('button', { name: 'Upload & create table' }).click()
  await expect(page.getByText('50/60 rows', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Next page' }).click()
  await expect(page.getByText('10/60 rows', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Next page' })).toBeDisabled()
  await page.reload({ waitUntil: 'domcontentloaded' })
  await page.getByRole('button', { name: 'Preview Sales_e2e', exact: true }).click()
  await expect(page.getByText('50/60 rows', { exact: true })).toBeVisible()
})

test('malformed CSV reports a usable error without a source table', async ({ page }) => {
  await page.getByLabel('CSV files', { exact: true }).setInputFiles({
    name: 'Bad.csv',
    mimeType: 'text/csv',
    buffer: Buffer.from('product_id,product_id\nA,B\n'),
  })
  await page.getByLabel('Table name', { exact: true }).fill('Invalid_e2e')
  await page.getByRole('button', { name: 'Upload & create table' }).click()
  await expect(page.getByRole('alert')).toContainText('headers must be unique')
  await expect(page.getByRole('button', { name: 'Preview Invalid_e2e' })).toHaveCount(0)
})

test('duplicate table names preserve the existing data', async ({ page }) => {
  const created = await page.request.post('/api/v1/warehouse/imports/csv', {
    headers: { 'Idempotency-Key': 'csv_existing_e2e' },
    multipart: {
      tableName: 'KeepOriginal_e2e',
      file: { name: 'existing.csv', mimeType: 'text/csv', buffer: Buffer.from('quantity\n10\n') },
    },
  })
  expect(created.status()).toBe(201)
  await page.getByRole('button', { name: 'Refresh source tables' }).click()
  await page.getByLabel('CSV files', { exact: true }).setInputFiles({
    name: 'existing.csv',
    mimeType: 'text/csv',
    buffer: Buffer.from('quantity\n99\n'),
  })
  await page.getByLabel('Table name', { exact: true }).fill('KeepOriginal_e2e')
  await page.getByRole('button', { name: 'Upload & create table' }).click()
  await expect(page.getByRole('alert')).toContainText('already exists')
  await page.getByRole('button', { name: 'Preview KeepOriginal_e2e', exact: true }).click()
  const preview = page.getByRole('region', { name: 'KeepOriginal_e2e rows', exact: true })
  await expect(preview.getByText('10', { exact: true })).toBeVisible()
  await expect(preview.getByText('99', { exact: true })).toHaveCount(0)
})

test('mobile upload and preview fit without page overflow', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 900 })
  await page.getByLabel('CSV files', { exact: true }).setInputFiles({
    name: 'Product.csv',
    mimeType: 'text/csv',
    buffer: Buffer.from('product_id,product_name\n001,First product\n'),
  })
  await page.getByLabel('Table name', { exact: true }).fill('Mobile_e2e')
  await page.getByRole('button', { name: 'Upload & create table' }).click()
  await expect(page.getByRole('region', { name: 'Mobile_e2e rows', exact: true })).toBeVisible()
  const fits = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)
  expect(fits).toBe(true)
  await page.screenshot({ path: 'test-results/sources-mobile.png', fullPage: true })
})

test('nuke everything confirms once and leaves only a default draft workspace', async ({
  page,
}) => {
  const button = page.getByRole('button', { name: 'Nuke everything', exact: true })
  let confirmations = 0
  page.once('dialog', async (dialog) => {
    confirmations++
    await dialog.dismiss()
  })
  await button.click()
  expect(confirmations).toBe(1)
  page.once('dialog', async (dialog) => {
    confirmations++
    await dialog.accept()
  })
  await button.click()
  await expect(page.getByLabel('Semantic model', { exact: true })).toHaveCount(0)
  await expect(page.getByLabel('Revision', { exact: true })).toHaveCount(0)
  await expect(
    page.getByRole('heading', { name: 'Your first source starts here', exact: true }),
  ).toBeVisible()
  const models = (await (await page.request.get('/api/v1/models')).json()).items
  expect(models).toHaveLength(1)
  expect(models[0].publishedRevisionId).toBeNull()
  const revisions = (
    await (await page.request.get('/api/v1/models/default_model/revisions')).json()
  ).items
  expect(revisions).toHaveLength(1)
  expect(revisions[0].approvalStatus).toBe('draft')
  const tables = (await (await page.request.get('/api/v1/warehouse/tables')).json()).tables
  expect(tables).toHaveLength(0)
  expect(confirmations).toBe(2)
})

test('upload ten CSVs sequentially and retry only a conflicting file', async ({ page }) => {
  test.setTimeout(60_000)
  const existing = await page.request.post('/api/v1/warehouse/imports/csv', {
    headers: { 'Idempotency-Key': 'batch_existing' },
    multipart: {
      tableName: 'Batch_5',
      file: { name: 'original.csv', mimeType: 'text/csv', buffer: Buffer.from('quantity\n99\n') },
    },
  })
  expect(existing.status()).toBe(201)
  const files = Array.from({ length: 10 }, (_, index) => ({
    name: `Batch_${index + 1}.csv`,
    mimeType: 'text/csv',
    buffer: Buffer.from(`quantity\n${index + 1}\n`),
  }))
  await page.getByLabel('CSV files', { exact: true }).setInputFiles(files)
  await page.getByRole('button', { name: 'Upload & create 10 tables', exact: true }).click()
  await expect(
    page.getByText('9 of 10 tables created. Correct or retry the remaining files.', {
      exact: true,
    }),
  ).toBeVisible({ timeout: 30_000 })
  await expect(page.getByRole('alert')).toContainText('already exists')
  await page.getByLabel('Table name for Batch_5.csv', { exact: true }).fill('Batch_5_new')
  await page.getByRole('button', { name: 'Retry 1 file', exact: true }).click()
  await expect(page.getByText('10 of 10 tables created.', { exact: true })).toBeVisible()
  for (const [table, value] of [
    ['Batch_1', '1'],
    ['Batch_5_new', '5'],
    ['Batch_5', '99'],
  ]) {
    await page.getByRole('button', { name: `Preview ${table}`, exact: true }).click()
    const preview = page.getByRole('region', { name: `${table} rows`, exact: true })
    await expect(
      preview.locator('.cell-value').filter({ hasText: new RegExp('^' + value + '$') }),
    ).toBeVisible()
  }
  await page.setViewportSize({ width: 375, height: 900 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  )
  await page.screenshot({ path: 'test-results/csv-batch-mobile.png', fullPage: true })
})
