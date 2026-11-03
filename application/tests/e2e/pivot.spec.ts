import { expect, test } from '@playwright/test'

test('pivot a managed table, reload its definition and consume effective columns', async ({
  page,
}) => {
  test.setTimeout(90_000)
  await page.goto('/')
  const model = 'pivot_ui_model'
  expect(
    (
      await page.request.post('/api/v1/models', {
        data: { modelId: model, name: 'Pivot', displayName: 'Pivot' },
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
  await page.goto('/?model=' + model + '&revision=draft_1&tab=sources')
  await expect(page.getByText('ClickHouse connected')).toBeVisible()
  await page.getByLabel('CSV files', { exact: true }).setInputFiles({
    name: 'UI_Long.csv',
    mimeType: 'text/csv',
    buffer: Buffer.from(
      'product,attribute,value,attribute_two,value_two\nP1,FQTY,10,Revenue,9\nP1,SQTY,8,Cost,4\nP2,FQTY,0,Revenue,1\n',
    ),
  })
  await page.getByLabel('Table name', { exact: true }).fill('UI_Long')
  await page.getByRole('button', { name: 'Upload & create table' }).click()
  const manage = page.getByRole('checkbox', { name: 'Manage UI_Long', exact: true })
  await expect(manage).toBeEnabled()
  await manage.check()
  await expect(manage).toBeEnabled()
  const nav = page.getByRole('navigation', { name: 'Workspace', exact: true })
  await nav.getByRole('button', { name: 'Managed Tables', exact: true }).click()
  await page
    .locator('.meta-object-card')
    .filter({ has: page.getByRole('heading', { name: 'UI_Long', exact: true }) })
    .getByRole('button', { name: 'Edit', exact: true })
    .click()
  await page.getByLabel('Pivot long-format columns', { exact: true }).check()
  await page
    .getByRole('group', { name: 'Retained columns' })
    .getByLabel('product', { exact: true })
    .check()
  await page
    .getByRole('group', { name: 'Record keys for separate attribute rows (optional)', exact: true })
    .getByLabel('product', { exact: true })
    .check()
  await page.getByLabel('Attribute column', { exact: true }).selectOption({ label: 'attribute' })
  await page.getByLabel('Value column', { exact: true }).selectOption({ label: 'value' })
  await page.getByRole('button', { name: 'Discover' }).click()
  const outputs = page.getByRole('group', { name: 'Output columns', exact: true })
  await expect(outputs.getByRole('checkbox')).toHaveCount(2)
  await expect(outputs.getByLabel('FQTY', { exact: true })).toBeChecked()
  await outputs.getByLabel('SQTY', { exact: true }).uncheck()
  await expect(outputs.getByLabel('SQTY', { exact: true })).not.toBeChecked()
  await outputs.getByRole('button', { name: 'Deselect all', exact: true }).click()
  await expect(outputs.getByRole('checkbox', { checked: true })).toHaveCount(0)
  await outputs.getByRole('button', { name: 'Select all', exact: true }).click()
  await expect(outputs.getByRole('checkbox', { checked: true })).toHaveCount(2)
  await expect(page.getByLabel('Expected attribute value', { exact: true })).toHaveCount(0)
  await expect(page.getByLabel('Expected output name', { exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: 'Add attribute / value pair', exact: true }).click()
  await page.getByLabel('Attribute column', { exact: true }).selectOption('col_4')
  await page.getByLabel('Value column', { exact: true }).selectOption('col_5')
  await page.getByRole('button', { name: 'Discover' }).click()
  await expect(page.getByRole('tab')).toHaveCount(2)
  await expect(page.getByRole('tabpanel')).toHaveCount(1)
  await page.screenshot({ path: 'test-results/managed-pair-tabs.png', fullPage: true })
  await page.getByRole('tab', { name: /Pair 1/ }).click()
  await expect(outputs.getByLabel('FQTY', { exact: true })).toBeChecked()
  const editor = page.getByRole('region', { name: 'Metadata editor', exact: true })
  for (const label of [
    'Raw preview',
    'Saved managed preview',
    'Ensure saved view',
    'Preview proposed output',
  ])
    await expect(editor.getByRole('button', { name: label, exact: true })).toHaveCount(0)
  const actions = editor.locator('.meta-actions')
  await expect(actions.getByRole('button')).toHaveCount(3)
  const keyBox = await page
    .getByRole('group', { name: 'Optional managed output primary key', exact: true })
    .boundingBox()
  const saveBox = await actions.getByRole('button', { name: 'Save', exact: true }).boundingBox()
  expect(saveBox!.y).toBeGreaterThan(keyBox!.y + keyBox!.height)
  // Refusing the prompt must not build or show proposed output.
  page.once('dialog', (dialog) => dialog.dismiss())
  await actions.getByRole('button', { name: 'Preview', exact: true }).click()
  await expect(editor.getByRole('table')).toHaveCount(0)
  page.once('dialog', (dialog) => dialog.accept())
  await actions.getByRole('button', { name: 'Preview', exact: true }).click()
  await expect(editor.getByRole('columnheader', { name: 'FQTY', exact: false })).toBeVisible()
  await expect(
    editor.getByRole('status').filter({ hasText: /^Configuration saved\.$/ }),
  ).toHaveText('Configuration saved.')
  await expect(editor.locator('.data-table')).toHaveCount(1)
  await expect(editor.getByRole('button', { name: 'Refresh preview' })).toHaveCount(0)
  await page.screenshot({ path: 'test-results/managed-saved-preview.png', fullPage: true })
  await page.reload()
  await nav.getByRole('button', { name: 'Managed Tables', exact: true }).click()
  await page
    .locator('.meta-object-card')
    .filter({ has: page.getByRole('heading', { name: 'UI_Long', exact: true }) })
    .getByRole('button', { name: 'Edit', exact: true })
    .click()
  await expect(page.getByLabel('Pivot long-format columns', { exact: true })).toBeChecked()
  await expect(
    page.getByRole('group', { name: 'Retained columns' }).getByLabel('product', { exact: true }),
  ).toBeChecked()
  await expect(page.getByLabel('Attribute column', { exact: true })).toHaveValue('col_2')
  await expect(page.getByLabel('Value column', { exact: true })).toHaveValue('col_3')
  await expect(outputs.getByLabel('FQTY', { exact: true })).toBeChecked()
  await expect(outputs.getByLabel('SQTY', { exact: true })).toBeChecked()
  const root = `/api/v1/models/${model}/revisions/draft_1/managed-tables`
  const before = (await (await page.request.get(root)).json()).items[0]
  expect(before.definition.pipeline[0].pairs).toHaveLength(2)
  // Saving a reopened editor must update the existing pivot, not re-enable identity mode.
  await editor.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(
    editor.getByRole('status').filter({ hasText: /^Configuration saved\.$/ }),
  ).toHaveText('Configuration saved.')
  await expect(editor.getByRole('button', { name: 'Save', exact: true })).toBeEnabled()
  const after = (await (await page.request.get(root)).json()).items[0]
  expect(after.definition).toEqual(before.definition)
  expect(after.version).toBe(before.version + 1)
  await editor.getByRole('button', { name: 'Close', exact: true }).click()
  await expect(editor).toHaveCount(0)
  await expect(
    page
      .locator('.meta-object-card')
      .filter({ has: page.getByRole('heading', { name: 'UI_Long', exact: true }) }),
  ).toBeVisible()
  await nav.getByRole('button', { name: 'Dimensions', exact: true }).click()
  await page.getByRole('button', { name: 'Create dimension', exact: true }).click()
  await page.getByLabel('Managed table', { exact: true }).selectOption({ label: 'UI_Long' })
  await expect(page.getByLabel('Display name for FQTY', { exact: true })).toHaveValue('FQTY')
  await expect(page.getByLabel('Display name for attribute', { exact: true })).toHaveCount(0)
  expect(await page.evaluate(() => Object.keys(localStorage))).toEqual([])
})

test('expand forecast rows and choose generated Brand/Zone keys before saving', async ({
  page,
}) => {
  test.setTimeout(60_000)
  const model = 'forecast_rows_model'
  const base = `/api/v1/models/${model}/revisions/draft_1`
  await page.goto('/')
  expect(
    (
      await page.request.post('/api/v1/models', {
        data: { modelId: model, name: 'Row forecast', displayName: 'Row forecast' },
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
  await page.goto(`/?model=${model}&revision=draft_1&tab=sources`)
  await page.getByLabel('CSV files', { exact: true }).setInputFiles({
    name: 'Forecast_Rows.csv',
    mimeType: 'text/csv',
    buffer: Buffer.from(
      'product_attribute,product_value,location_attribute,location_value,week,forecast_qty\nbrand,Crunchy,zone,West,2026-W11,240\nbrand,Zesto,zone,West,2026-W11,240\nbrand,FreshCo,zone,North,2026-W12,0\n',
    ),
  })
  await page.getByRole('button', { name: 'Upload & create table', exact: true }).click()
  const manage = page.getByRole('checkbox', { name: 'Manage Forecast_Rows', exact: true })
  await expect(manage).toBeEnabled()
  await manage.check()
  await expect(manage).toBeEnabled()
  await page.getByRole('button', { name: 'Managed Tables', exact: true }).click()
  await page
    .locator('.meta-object-card')
    .filter({ has: page.getByRole('heading', { name: 'Forecast_Rows', exact: true }) })
    .getByRole('button', { name: 'Edit', exact: true })
    .click()
  await page.getByLabel('Pivot long-format columns', { exact: true }).check()
  await expect(page.getByLabel('Pivot mode', { exact: true })).toHaveCount(0)
  const retained = page.getByRole('group', { name: 'Retained columns', exact: true })
  await retained.getByLabel('week', { exact: true }).check()
  await retained.getByLabel('forecast_qty', { exact: true }).check()
  await page.getByLabel('Attribute column', { exact: true }).selectOption('col_1')
  await page.getByLabel('Value column', { exact: true }).selectOption('col_2')
  await page.getByRole('button', { name: 'Discover', exact: true }).click()
  await page.getByRole('button', { name: 'Add attribute / value pair', exact: true }).click()
  await page.getByLabel('Attribute column', { exact: true }).selectOption('col_3')
  await page.getByLabel('Value column', { exact: true }).selectOption('col_4')
  await page.getByRole('button', { name: 'Discover', exact: true }).click()
  const keys = page.getByRole('group', { name: 'Optional managed output primary key', exact: true })
  await keys.getByLabel('week', { exact: true }).check()
  await keys.getByLabel('brand', { exact: true }).check()
  await keys.getByLabel('zone', { exact: true }).check()
  await expect(keys.getByLabel('product_value', { exact: true })).toHaveCount(0)
  await expect(keys.getByLabel('location_value', { exact: true })).toHaveCount(0)
  await expect(retained.getByLabel('product_value', { exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(page.getByText('Configuration saved.', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Preview', exact: true }).click()
  const table = page.locator('.meta-editor .data-table')
  // DataPreview adds a row-number heading beside the four actual output columns.
  await expect(table.getByRole('columnheader')).toHaveCount(5)
  await expect(table.getByRole('cell', { name: '240', exact: true })).toHaveCount(2)
  await expect(table.getByRole('cell', { name: 'Crunchy', exact: true })).toBeVisible()
  await expect(table.getByRole('cell', { name: 'Zesto', exact: true })).toBeVisible()
  await page.reload()
  await page
    .locator('.meta-object-card')
    .filter({ has: page.getByRole('heading', { name: 'Forecast_Rows', exact: true }) })
    .getByRole('button', { name: 'Edit', exact: true })
    .click()
  await expect(page.getByLabel('Pivot mode', { exact: true })).toHaveCount(0)
  await expect(
    page.getByText(
      'Pair values and retained columns are grouped automatically. Identical records collapse.',
    ),
  ).toBeVisible()
  await expect(keys.getByLabel('brand', { exact: true })).toBeChecked()
  await expect(keys.getByLabel('zone', { exact: true })).toBeChecked()
  await page.setViewportSize({ width: 375, height: 900 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: 'test-results/forecast-row-pivot.png', fullPage: true })
})
