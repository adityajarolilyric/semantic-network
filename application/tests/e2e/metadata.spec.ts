import { expect, test, type Page } from '@playwright/test'

test('manage from the source grid and capture PLT/PT metadata without visible internal IDs', async ({
  page,
}) => {
  test.setTimeout(180_000)
  await page.goto('/')
  await expect(page.getByText('ClickHouse connected')).toBeVisible()
  await expect(page.getByLabel('Semantic model', { exact: true })).toHaveValue('default_model')
  await page.getByRole('button', { name: 'About Semantic model', exact: true }).hover()
  await expect(page.getByRole('tooltip')).toContainText('Groups the tables')
  await page.keyboard.press('Escape')
  await expect(page.getByRole('tooltip')).toHaveCount(0)
  await expect(page.getByLabel('Revision', { exact: true })).toHaveValue('revision_1')
  const root = '/api/v1/models/default_model/revisions/revision_1'
  const fixtures = [
    {
      table: 'UI_Product',
      csv: 'product_id,product_name,product_code\nP1,Widget,P1\nP2,Gadget,P2\n',
    },
    { table: 'UI_Location', csv: 'location_id,location_name\nL1,North\n' },
    { table: 'UI_Time', csv: 'date\n2026-10-31\n2026-11-01\n' },
    { table: 'UI_SourcingMatrix', csv: 'product_id,location_id\nP1,L1\nP2,L1\n' },
    {
      table: 'UI_Forecast',
      csv: 'product_id,location_id,date,quantity\nP1,L1,2026-10-31,100\nP2,L1,2026-10-31,80\n',
    },
    { table: 'UI_Sales', csv: 'product_id,date,quantity\nP1,2026-10-31,90\nP2,2026-10-31,100\n' },
  ]
  for (const f of fixtures) {
    await page
      .getByLabel('CSV files', { exact: true })
      .setInputFiles({ name: f.table + '.csv', mimeType: 'text/csv', buffer: Buffer.from(f.csv) })
    await page.getByLabel('Table name', { exact: true }).fill(f.table)
    await page.getByRole('button', { name: 'Upload & create table' }).click()
    const manage = page.getByRole('checkbox', { name: 'Manage ' + f.table, exact: true })
    await expect(manage).toBeEnabled()
    await manage.check()
    await expect(manage).toBeChecked()
    await expect(manage).toBeEnabled()
    await expect(page.getByLabel('Object ID', { exact: true })).toHaveCount(0)
  }
  await page.getByRole('checkbox', { name: 'Manage UI_Product', exact: true }).uncheck()
  await expect(page.getByRole('checkbox', { name: 'Manage UI_Product', exact: true })).toBeEnabled()
  await page.reload()
  await expect(page.getByRole('checkbox', { name: 'Manage UI_Product', exact: true })).toBeEnabled()
  await expect(
    page.getByRole('checkbox', { name: 'Manage UI_Product', exact: true }),
  ).not.toBeChecked()
  await page.getByRole('checkbox', { name: 'Manage UI_Product', exact: true }).check()
  await expect(page.getByRole('checkbox', { name: 'Manage UI_Product', exact: true })).toBeEnabled()
  await page.screenshot({ path: 'test-results/managed-source-grid.png', fullPage: true })
  const sources = await (await page.request.get(root + '/sources')).json()
  expect(sources.items).toHaveLength(6)
  expect(
    sources.items.every(
      (s: { definition: { keyColumns: string[] } }) => s.definition.keyColumns.length === 0,
    ),
  ).toBeTruthy()
  await expect(
    page
      .getByRole('navigation', { name: 'Workspace', exact: true })
      .getByRole('button', { name: 'Managed Tables', exact: true }),
  ).toHaveCount(0)
  const managedUrl = new URL(page.url())
  managedUrl.searchParams.set('tab', 'managed')
  await page.goto(managedUrl.toString())
  await expect(page.locator('.meta-object-card')).toHaveCount(6)
  for (const f of [
    {
      name: 'Product',
      table: 'UI_Product',
      label: 'col_2',
      key: 'product_id',
      display: 'Product ID',
    },
    {
      name: 'Location',
      table: 'UI_Location',
      label: 'col_2',
      key: 'location_id',
      display: 'Location ID',
    },
    { name: 'Time', table: 'UI_Time', label: 'col_1', key: 'date', display: 'Date' },
  ]) {
    await nav(page, 'Dimensions')
    await page.getByRole('button', { name: 'Create dimension', exact: true }).click()
    await page.getByLabel('Dimension name', { exact: true }).fill(f.name)
    await page.getByLabel('Managed table', { exact: true }).selectOption({ label: f.table })
    await expect(page.getByRole('textbox', { name: 'Display name', exact: true })).toHaveCount(0)
    await expect(page.getByLabel('Display name for ' + f.key, { exact: true })).toHaveValue(f.key)
    await page.getByLabel('Display name for ' + f.key, { exact: true }).fill(f.display)
    await expect(page.getByLabel('Expose ' + f.key, { exact: true })).toBeChecked()
    await page.getByLabel('Key ' + f.key, { exact: true }).check()
    await expect(page.getByLabel('Expose ' + f.key, { exact: true })).toBeChecked()
    await expect(page.getByLabel('Expose ' + f.key, { exact: true })).toBeDisabled()
    await page.getByText('More settings', { exact: true }).click()
    await page.getByLabel('Member label column', { exact: true }).selectOption(f.label)
    if (f.name === 'Time') {
      await page.getByLabel('Time dimension', { exact: true }).check()
    } else
      await page
        .getByLabel('Expose ' + (f.name === 'Product' ? 'product_name' : 'location_name'), {
          exact: true,
        })
        .check()
    if (f.name === 'Product')
      await page.screenshot({ path: 'test-results/dimension-column-editor.png', fullPage: true })
    await save(page)
  }
  await nav(page, 'Planning Levels')
  await page.getByRole('button', { name: 'Create planning level', exact: true }).click()
  await page.getByLabel('Name', { exact: true }).fill('PLT')
  await page.getByLabel('Population method', { exact: true }).selectOption('cartesian_product')
  await page.getByLabel('Include UI_SourcingMatrix', { exact: true }).check()
  await page
    .getByRole('group', { name: 'Keys from UI_SourcingMatrix', exact: true })
    .getByLabel('product_id', { exact: true })
    .check()
  await page
    .getByRole('group', { name: 'Keys from UI_SourcingMatrix', exact: true })
    .getByLabel('location_id', { exact: true })
    .check()
  await page.getByLabel('Include UI_Time', { exact: true }).check()
  await page
    .getByRole('group', { name: 'Keys from UI_Time', exact: true })
    .getByLabel('date', { exact: true })
    .check()
  await bind(page, 'Product', 'product_code', 'product_id')
  await bind(page, 'Location', 'Location ID', 'location_id')
  await bind(page, 'Time', 'Date', 'date')
  await page.getByRole('tab', { name: 'Product', exact: true }).click()
  await expect(
    page.getByLabel('Product dimension column 1', { exact: true }).getByRole('option'),
  ).toHaveText(['Select…', 'Product ID', 'product_name', 'product_code'])
  await page.getByRole('tab', { name: 'Location', exact: true }).click()
  await expect(
    page.getByLabel('Location dimension column 1', { exact: true }).getByRole('option'),
  ).toHaveText(['Select…', 'Location ID', 'location_name'])
  await page.getByRole('tab', { name: 'Time', exact: true }).click()
  await expect(
    page.getByLabel('Time dimension column 1', { exact: true }).getByRole('option'),
  ).toHaveText(['Select…', 'Date'])
  await save(page)
  await page.getByLabel('Base planning level', { exact: true }).selectOption({ label: 'PLT' })
  await expect(page.getByText('Base planning level saved.', { exact: true })).toBeVisible()
  await expect(
    page.getByRole('button', { name: 'Create planning level', exact: true }),
  ).toBeEnabled()
  await page.getByRole('button', { name: 'Create planning level', exact: true }).click()
  await page.getByLabel('Name', { exact: true }).fill('PT')
  await page.getByLabel('Population method', { exact: true }).selectOption('distinct_projection')
  await page.getByLabel('Parent planning level', { exact: true }).selectOption({ label: 'PLT' })
  await page
    .getByRole('group', { name: 'Retain parent keys', exact: true })
    .getByLabel('product_id', { exact: true })
    .check()
  await page
    .getByRole('group', { name: 'Retain parent keys', exact: true })
    .getByLabel('date', { exact: true })
    .check()
  await expect(page.getByRole('tab', { name: 'Product', exact: true })).toBeVisible()
  await expect(page.getByRole('tab', { name: 'Time', exact: true })).toBeVisible()
  await expect(page.getByLabel('Product dimension column 1', { exact: true })).toHaveValue('col_3')
  await expect(page.getByRole('tab', { name: 'Location', exact: true })).toHaveCount(0)
  const productAttributes = page.getByRole('group', {
    name: 'Attributes from Product',
    exact: true,
  })
  await productAttributes.getByRole('button', { name: 'Select all', exact: true }).click()
  await expect(productAttributes.getByLabel('product_name', { exact: true })).toBeChecked()
  await productAttributes.getByRole('button', { name: 'Deselect all', exact: true }).click()
  await expect(productAttributes.getByLabel('product_name', { exact: true })).not.toBeChecked()
  await expect(page.getByRole('heading', { name: 'Grain identity', exact: true })).toHaveCount(0)
  await page.screenshot({ path: 'test-results/projection-bindings.png', fullPage: true })
  await save(page)
  for (const tx of [
    {
      name: 'Forecast',
      table: 'UI_Forecast',
      level: 'PLT',
      mapping: { product_id: 'col_1', location_id: 'col_2', date: 'col_3' },
    },
    {
      name: 'Sales',
      table: 'UI_Sales',
      level: 'PT',
      mapping: { product_id: 'col_1', date: 'col_2' },
    },
  ]) {
    await nav(page, 'Transactions')
    await page.getByRole('button', { name: 'Create transaction', exact: true }).click()
    await page.getByLabel('Name', { exact: true }).fill(tx.name)
    await expect(page.getByLabel('Expected rows per grain', { exact: true })).toHaveCount(0)
    await page.getByLabel('Managed table', { exact: true }).selectOption({ label: tx.table })
    await page
      .getByLabel('Native planning level', { exact: true })
      .selectOption({ label: tx.level })
    for (const [key, column] of Object.entries(tx.mapping))
      await page.getByLabel('Source column for ' + key, { exact: true }).selectOption(column)
    await save(page)
  }
  expect(
    (await (await page.request.get(root + '/transactions')).json()).items.every(
      (tx: { definition: { expectedRowsPerGrain: string } }) =>
        tx.definition.expectedRowsPerGrain === 'many',
    ),
  ).toBeTruthy()
  for (const m of [
    { name: 'FQTY', tx: 'Forecast', column: 'col_4' },
    { name: 'SQTY', tx: 'Sales', column: 'col_3' },
  ]) {
    await nav(page, 'Measures')
    await page.getByRole('button', { name: 'Create measure', exact: true }).click()
    await page.getByLabel('Name', { exact: true }).fill(m.name)
    await page.getByLabel('Transaction', { exact: true }).selectOption({ label: m.tx })
    await page.getByLabel('Value column', { exact: true }).selectOption(m.column)
    await expect(page.getByLabel('Data type', { exact: true })).toHaveCount(0)
    await expect(page.getByLabel('Numeric type', { exact: true })).toHaveCount(0)
    await expect(page.getByLabel('Precision', { exact: true })).toHaveCount(0)
    await expect(page.getByLabel('Scale', { exact: true })).toHaveCount(0)
    await expect(page.getByLabel('Unit', { exact: true })).toHaveCount(0)
    await expect(page.getByLabel('Aliases (comma separated)', { exact: true })).toHaveCount(0)
    if (m.name === 'SQTY' || m.name === 'FQTY') {
      await page
        .getByLabel('Dis-aggregation policy', { exact: true })
        .selectOption('weighted_by_self')
      await expect(page.getByLabel('Dis-aggregation basis measure', { exact: true })).toHaveCount(0)
    }
    await save(page)
  }
  await page.getByRole('button', { name: 'Create measure', exact: true }).click()
  await page.getByLabel('Name', { exact: true }).fill('Error')
  await page.getByRole('radio', { name: 'Calculated', exact: true }).check()
  await page.getByLabel('Output Planning Level', { exact: true }).selectOption({ label: 'PT' })
  await page.getByLabel('Formula', { exact: true }).fill('ABS(FQTY-SQTY)')
  await save(page)
  await page.reload()
  await expect(page.locator('.meta-object-card')).toHaveCount(3)
  const measures = await (await page.request.get(root + '/measures')).json()
  const error = measures.items.find((m: { name: string }) => m.name === 'Error')
  const fqty = measures.items.find((m: { name: string }) => m.name === 'FQTY')
  const sqty = measures.items.find((m: { name: string }) => m.name === 'SQTY')
  expect(fqty.business.aliases).toEqual([])
  expect(sqty.definition.allocation.mode).toBe('weighted_by_self')
  expect(sqty.definition.allocation.basisMeasureId).toBeNull()
  expect(fqty.definition.resultType.type).toBe('number')
  expect(sqty.definition.resultType.type).toBe('number')
  expect(error.definition.calculationType).toBe('AggregateFirst')
  expect(error.definition.formula.expression.arguments[0].left.measureId).toBe(fqty.objectId)
  expect(error.definition.formula.expression.arguments[0].right.measureId).toBe(sqty.objectId)
  const card = page
    .locator('.meta-object-card')
    .filter({ has: page.getByRole('heading', { name: 'Error', exact: true }) })
  await card.getByRole('button', { name: 'Edit', exact: true }).click()
  await expect(page.getByLabel('Formula', { exact: true })).toHaveValue('ABS(([FQTY] - [SQTY]))')
  await expect(page.getByLabel('Object ID', { exact: true })).toHaveCount(0)
  await page.getByLabel('Description', { exact: true }).fill('My change')
  const { modelId: _m, revisionId: _r, version: _v, createdAt: _c, updatedAt: _u, ...input } = error
  expect(
    (
      await page.request.put(root + '/measures/' + error.objectId, {
        headers: { 'If-Match': '"1"' },
        data: { ...input, description: 'Concurrent update' },
      })
    ).ok(),
  ).toBeTruthy()
  await page.getByRole('button', { name: 'Save metadata', exact: true }).click()
  await expect(page.getByRole('alert')).toBeVisible()
  await expect(page.getByLabel('Description', { exact: true })).toHaveValue('My change')
  await page.getByRole('button', { name: 'Cancel', exact: true }).click()
  await page.getByRole('button', { name: 'Reload metadata', exact: true }).click()
  await expect(page.getByText('Loading saved metadata…', { exact: true })).toHaveCount(0)
  await nav(page, 'Planning Levels')
  for (const name of ['PLT', 'PT']) {
    await page
      .locator('.meta-object-card')
      .filter({ has: page.getByRole('heading', { name, exact: true }) })
      .getByRole('button', { name: 'Edit', exact: true })
      .click()
    const panel = page.getByLabel('Planning table ' + name, { exact: true })
    await expect(panel.getByRole('status')).toContainText('Table: ready')
    await expect(panel.getByRole('status')).toContainText('4 rows')
    await page.getByRole('button', { name: 'Cancel', exact: true }).click()
  }
  await nav(page, 'Measures')
  await page
    .locator('.meta-object-card')
    .filter({ has: page.getByRole('heading', { name: 'SQTY', exact: true }) })
    .getByRole('button', { name: 'Edit', exact: true })
    .click()
  await page.getByLabel('Dis-aggregation policy', { exact: true }).selectOption('none')
  await page.getByLabel('Editable measure', { exact: true }).check()
  await save(page)
  await nav(page, 'Materialise')
  const valuesPanel = page.getByRole('region', { name: 'Materialise', exact: true })
  const buildButton = valuesPanel.getByRole('button', {
    name: 'Build Semantic Network',
    exact: true,
  })
  const previewSelector = valuesPanel.getByLabel('Preview planning level', { exact: true })
  const buildBox = await buildButton.boundingBox()
  const previewBox = await previewSelector.boundingBox()
  expect(buildBox!.y + buildBox!.height).toBeLessThan(previewBox!.y)
  await expect(
    valuesPanel.getByText('What gets materialised at PLT', { exact: true }),
  ).toBeVisible()
  await expect(valuesPanel.getByText('Disaggregate to Base', { exact: true })).toBeVisible()
  await valuesPanel
    .getByRole('button', { name: 'About Build Semantic Network', exact: true })
    .click()
  await expect(page.getByRole('tooltip')).toContainText(
    'preview selection does not restrict the build',
  )
  await page.screenshot({ path: 'test-results/materialise-help.png', fullPage: true })
  await page.keyboard.press('Escape')
  await buildButton.click()
  await expect(valuesPanel.getByRole('status')).toContainText('Network: ready')
  await expect(valuesPanel.getByRole('columnheader', { name: 'FQTY', exact: false })).toBeVisible()
  await expect(valuesPanel.getByRole('columnheader', { name: 'SQTY', exact: false })).toBeVisible()
  await page.screenshot({ path: 'test-results/materialise-preview.png', fullPage: true })
  await valuesPanel
    .getByLabel('Preview planning level', { exact: true })
    .selectOption({ label: 'PT' })
  await expect(valuesPanel.getByRole('columnheader', { name: 'Error', exact: false })).toBeVisible()
  await nav(page, 'Planning View')
  await expect(page.getByLabel('Semantic model', { exact: true })).toHaveCount(0)
  await page.getByLabel('Add rows attribute').selectOption({ label: 'Product · product_code' })
  await page.getByLabel('Add columns attribute').selectOption({ label: 'Time · Date' })
  await page.getByLabel('FQTY', { exact: true }).check()
  await page.getByLabel('SQTY', { exact: true }).check()
  await page.getByLabel('Error', { exact: true }).check()
  await page.getByRole('button', { name: 'Update pivot', exact: true }).click()
  await expect(page.getByText('Data from PT', { exact: true })).toBeVisible()
  const pivot = page.getByRole('region', { name: 'Planning pivot results' })
  await expect(pivot.getByRole('columnheader', { name: '2026-10-31', exact: true })).toBeVisible()
  await expect(pivot.getByRole('rowheader', { name: 'P1', exact: true })).toBeVisible()
  await expect(pivot.getByRole('rowheader', { name: 'FQTY', exact: true }).first()).toBeVisible()
  await expect(pivot.getByRole('columnheader', { name: 'FQTY', exact: true })).toHaveCount(0)
  await expect(pivot.getByRole('cell', { name: '100.00', exact: true }).first()).toBeVisible()
  const editButton = pivot.getByRole('button', { name: 'Edit SQTY · P1 / 2026-10-31', exact: true })
  await expect(editButton).toBeVisible()
  await expect(pivot.getByRole('button', { name: /Edit Error/ })).toHaveCount(0)
  await editButton.click()
  await page
    .getByRole('textbox', { name: 'New value for SQTY · P1 / 2026-10-31', exact: true })
    .fill('150')
  await page.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(
    pivot.getByRole('button', { name: 'Edit SQTY · P1 / 2026-10-31', exact: true }),
  ).toContainText('150')
  await expect(
    page.getByText('Applying edit and recomputing compatible planning levels…'),
  ).toHaveCount(0)
  await page.screenshot({ path: 'test-results/planning-view.png', fullPage: true })
  await page.setViewportSize({ width: 375, height: 900 })
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
  ).toBeTruthy()
  await page.screenshot({ path: 'test-results/planning-view-mobile.png', fullPage: true })
  await page.setViewportSize({ width: 1440, height: 1000 })
  await nav(page, 'Measures')
  await nav(page, 'Planning View')
  await expect(page.getByLabel('FQTY', { exact: true })).toBeChecked()
  await expect(
    page.getByRole('button', { name: 'Remove Product · product_code from rows', exact: true }),
  ).toBeVisible()
  await expect(
    page.getByRole('button', { name: 'Edit SQTY · P1 / 2026-10-31', exact: true }),
  ).toContainText('150')
  await page.reload()
  await expect(page.getByLabel('FQTY', { exact: true })).toBeChecked()
  await expect(
    page.getByRole('button', { name: 'Edit SQTY · P1 / 2026-10-31', exact: true }),
  ).toContainText('150')
  await page.getByRole('button', { name: 'Clear selections', exact: true }).click()
  await expect(page.getByLabel('FQTY', { exact: true })).not.toBeChecked()
  expect(await page.evaluate(() => Object.keys(localStorage))).toEqual([])
  await nav(page, 'Sources')
  await page.getByRole('checkbox', { name: 'Manage UI_Product', exact: true }).uncheck()
  await expect(page.getByRole('alert')).toBeVisible()
  await expect(page.getByRole('checkbox', { name: 'Manage UI_Product', exact: true })).toBeChecked()
  await page.getByRole('button', { name: 'Reload metadata', exact: true }).click()
  await page.getByRole('button', { name: 'Validate revision', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Revision is valid', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Publish revision', exact: true }).click()
  await page.getByRole('button', { name: 'Confirm publication', exact: true }).click()
  await expect(
    page.getByText('This revision is read-only. Create a new draft to change its metadata.'),
  ).toBeVisible()
  await page.getByRole('button', { name: 'New draft', exact: true }).click()
  await page.getByRole('button', { name: 'Create draft', exact: true }).click()
  await expect(page.getByLabel('Revision', { exact: true })).not.toHaveValue('revision_1')
  const second = await page.getByLabel('Revision', { exact: true }).inputValue()
  await nav(page, 'Measures')
  await expect(page.locator('.meta-object-card')).toHaveCount(3)
  await expect(page.getByLabel('Semantic model', { exact: true })).toHaveCount(0)
  await expect(page.getByLabel('Revision', { exact: true })).toHaveCount(0)
  await page.screenshot({ path: 'test-results/metadata-desktop.png', fullPage: true })
  await page.setViewportSize({ width: 375, height: 900 })
  await page.screenshot({ path: 'test-results/metadata-mobile.png', fullPage: true })
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
  ).toBeTruthy()
  await nav(page, 'Dimensions')
  await page
    .locator('.meta-object-card')
    .filter({ has: page.getByRole('heading', { name: 'Product', exact: true }) })
    .getByRole('button', { name: 'Edit', exact: true })
    .click()
  await expect(page.getByLabel('Expose product_id', { exact: true })).toBeDisabled()
  await page.screenshot({ path: 'test-results/dimension-column-editor-mobile.png', fullPage: true })
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
  ).toBeTruthy()
  await page.getByRole('button', { name: 'Cancel', exact: true }).click()
  await nav(page, 'Planning Levels')
  await page
    .locator('.meta-object-card')
    .filter({ has: page.getByRole('heading', { name: 'PT', exact: true }) })
    .getByRole('button', { name: 'Delete', exact: true })
    .click()
  await page.getByRole('button', { name: 'Confirm deletion', exact: true }).click()
  await expect(page.getByRole('alert')).toBeVisible()
  await page.getByRole('button', { name: 'Cancel deletion', exact: true }).click()
  await nav(page, 'Measures')
  await page
    .locator('.meta-object-card')
    .filter({ has: page.getByRole('heading', { name: 'Error', exact: true }) })
    .getByRole('button', { name: 'Delete', exact: true })
    .click()
  await page.getByRole('button', { name: 'Confirm deletion', exact: true }).click()
  await expect(page.locator('.meta-object-card')).toHaveCount(2)
  expect(
    (
      await page.request.get(
        `/api/v1/models/default_model/revisions/${second}/measures/${error.objectId}`,
      )
    ).status(),
  ).toBe(404)
  expect((await page.request.get('/api/v1/warehouse/tables/UI_Sales/preview')).status()).toBe(200)
  expect(await page.evaluate(() => Object.keys(localStorage))).toEqual([])
})
async function nav(page: Page, label: string) {
  await page
    .getByRole('navigation', { name: 'Workspace', exact: true })
    .getByRole('button', { name: label, exact: true })
    .click()
}
async function save(page: Page) {
  const save = page.getByRole('button', { name: 'Save', exact: true })
  if (await save.count()) await save.click()
  else await page.getByRole('button', { name: 'Save metadata', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Metadata editor', exact: true })).toHaveCount(0)
  await expect(page.getByText('Loading saved metadata…', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('alert')).toHaveCount(0)
}
async function bind(page: Page, dimension: string, key: string, grain: string) {
  await page.getByLabel('Add dimension', { exact: true }).selectOption({ label: dimension })
  await page
    .getByLabel(dimension + ' planning-level column 1', { exact: true })
    .selectOption({ label: grain })
  await page
    .getByLabel(dimension + ' dimension column 1', { exact: true })
    .selectOption({ label: key })
}
