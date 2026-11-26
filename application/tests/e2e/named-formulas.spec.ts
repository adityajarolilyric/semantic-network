import { expect, test } from '@playwright/test'

test('create and rename a calculated measure through its named formula', async ({ page }) => {
  test.setTimeout(90_000)
  await page.goto('/')
  const imported = await page.request.post('/api/v1/warehouse/imports/csv', {
    headers: { 'Idempotency-Key': 'named_' + crypto.randomUUID() },
    multipart: {
      tableName: 'NamedFormulaFacts',
      file: {
        name: 'named.csv',
        mimeType: 'text/csv',
        buffer: Buffer.from('product,forecast_qty,sales_qty\nP1,100,80\nP2,40,60\n'),
      },
    },
  })
  expect(imported.ok(), await imported.text()).toBeTruthy()
  await page.reload()
  await page.locator('nav').getByRole('button', { name: 'Measures', exact: true }).click()
  await page.getByRole('button', { name: 'Create measure', exact: true }).click()
  await page.getByRole('radio', { name: 'Calculated', exact: true }).check()
  const editor = page.getByRole('region', { name: 'Metadata editor' })
  await expect(editor.getByLabel('Name', { exact: true })).toHaveCount(0)
  const output = editor.getByLabel('Output grain', { exact: true })
  await output.selectOption({ label: 'NamedFormulaFacts · Transaction grain' })
  const formula = editor.getByLabel('Formula', { exact: true })
  await formula.fill('  Absolute Error = abs([forecast_qty] - [sales_qty])  ')
  await expect(editor.locator('.formula-feedback')).toContainText('Valid formula')
  await page.screenshot({ path: 'test-results/named-formula.png', fullPage: true })
  await editor.getByRole('button', { name: 'Save metadata', exact: true }).click()
  await expect(editor).toHaveCount(0)
  const card = page
    .locator('article')
    .filter({ has: page.getByText('Absolute Error', { exact: true }) })
  await card.getByRole('button', { name: 'Edit', exact: true }).click()
  await expect(formula).toHaveValue('Absolute Error = ABS(([forecast_qty] - [sales_qty]))')
  await formula.fill('"Error = Absolute" = abs([forecast_qty] - [sales_qty])')
  await editor.getByRole('button', { name: 'Save metadata', exact: true }).click()
  await expect(editor).toHaveCount(0)
  await expect(page.locator('article').getByText('Error = Absolute', { exact: true })).toBeVisible()
})
