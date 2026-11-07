import { expect, test } from '@playwright/test'

test('builds, refreshes and previews materialised tables only in Decision View', async ({
  page,
}) => {
  test.setTimeout(120_000)
  await page.goto('/?tab=decision-view')
  await expect(page.getByRole('heading', { level: 1, name: 'Decision View' })).toBeVisible()
  await expect(
    page.locator('nav').getByRole('button', { name: 'Build Semantic', exact: true }),
  ).toHaveCount(0)
  const imported = await page.request.post('/api/v1/warehouse/imports/csv', {
    headers: { 'Idempotency-Key': 'decision_' + crypto.randomUUID() },
    multipart: {
      tableName: 'Decision_Forecast',
      file: {
        name: 'forecast.csv',
        mimeType: 'text/csv',
        buffer: Buffer.from('product,forecast_qty\nP1,10\nP2,20\n'),
      },
    },
  })
  expect(imported.ok(), await imported.text()).toBeTruthy()
  await page.reload()
  const screen = page.getByRole('region', { name: 'Decision View', exact: true })
  const preview = screen.getByRole('region', { name: 'Preview Materialised Table', exact: true })
  const build = screen.getByRole('button', { name: 'Build Semantic Network', exact: true })
  await expect(build).toBeEnabled()
  await build.click()
  await expect(screen.getByText('Semantic Network updated.', { exact: true })).toBeVisible()
  const selector = preview.getByLabel('Materialised table', { exact: true })
  await expect(selector).toBeEnabled()
  await expect(selector.locator('option')).toHaveCount(2)
  await expect(preview.getByRole('table')).toHaveCount(0)
  await selector.selectOption({ index: 1 })
  await expect(preview.getByRole('columnheader', { name: /^forecast_qty/ })).toBeVisible()
  await expect(preview.getByText('2/2 rows', { exact: true })).toBeVisible()
  await screen.getByLabel('Add rows attribute').selectOption({ index: 1 })
  await screen.getByLabel('forecast_qty', { exact: true }).check()
  await screen.getByRole('button', { name: 'Update pivot', exact: true }).click()
  await expect(screen.locator('.decision-view-results')).toBeVisible()
  const pivotBox = await screen.locator('.decision-view-results').boundingBox()
  const previewBox = await preview.boundingBox()
  expect(previewBox!.y).toBeGreaterThanOrEqual(pivotBox!.y + pivotBox!.height)
  page.once('dialog', (dialog) => dialog.accept())
  await screen.getByRole('button', { name: 'Full Semantic Refresh', exact: true }).click()
  await expect(screen.getByText('Full Semantic Refresh completed.', { exact: true })).toBeVisible()
  await expect(screen.getByLabel('forecast_qty', { exact: true })).toBeChecked()
  await expect(selector).toHaveValue(/.+/)
  await expect(preview.getByText('2/2 rows', { exact: true })).toBeVisible()
  await page.screenshot({ path: 'test-results/decision-network.png', fullPage: true })
  await page.goto('/?tab=materialise')
  await expect(page.getByRole('heading', { level: 1, name: 'Decision View' })).toBeVisible()
})

test('edits a source Dimension population strategy while keeping connected Transaction keys', async ({
  page,
}) => {
  test.setTimeout(120_000)
  const root = '/api/v1/models/default_model/revisions/revision_1'
  await page.goto('/?tab=dimensions')
  for (const tableName of ['StrategyOrigin', 'StrategyParent']) {
    const imported = await page.request.post('/api/v1/warehouse/imports/csv', {
      headers: { 'Idempotency-Key': 'decision_' + crypto.randomUUID() },
      multipart: {
        tableName,
        file: {
          name: 'products.csv',
          mimeType: 'text/csv',
          buffer: Buffer.from('product\nP1\nP2\n'),
        },
      },
    })
    expect(imported.ok(), await imported.text()).toBeTruthy()
  }
  const dimensions = (await (await page.request.get(root + '/dimensions')).json()).items
  const original = dimensions.find(
    (dimension: { name: string }) => dimension.name === 'StrategyOrigin',
  )
  expect(original).toBeTruthy()
  const source = (await (await page.request.get(root + '/sources')).json()).items.find(
    (source: { name: string }) => source.name === 'StrategyOrigin',
  )
  const transaction = await page.request.post(root + '/transactions', {
    headers: { 'Idempotency-Key': 'strategy_' + crypto.randomUUID() },
    data: {
      objectId: 'strategy_bound',
      kind: 'transaction',
      name: 'Bound',
      displayName: 'Bound',
      definition: {
        sourceId: source.objectId,
        dimensionId: original.objectId,
        keyMappings: [
          { sourceColumnId: 'col_1', grainColumnId: original.definition.grain.keyIds[0] },
        ],
      },
    },
  })
  expect(transaction.ok(), await transaction.text()).toBeTruthy()
  await page.reload()
  const card = page
    .locator('.meta-object-card')
    .filter({ has: page.getByRole('heading', { name: 'StrategyOrigin', exact: true }) })
  await card.getByRole('button', { name: 'Edit', exact: true }).click()
  const editor = page.getByRole('region', { name: 'Metadata editor' })
  await expect(editor.getByRole('radio', { name: 'From a Table' })).toBeChecked()
  await editor.getByRole('radio', { name: 'From Multiple Tables' }).click()
  await editor.getByLabel('Include StrategyOrigin', { exact: true }).check()
  const keys = editor.getByRole('group', { name: 'Keys from StrategyOrigin' })
  const attributes = editor.getByRole('group', {
    name: 'Population attributes from StrategyOrigin',
  })
  await expect(keys).toBeVisible()
  await expect(attributes).toBeVisible()
  await expect(keys.getByLabel('product', { exact: true })).not.toBeChecked()
  await keys.getByLabel('product', { exact: true }).check()
  await expect(editor.getByText('Map existing fields to the new population')).toHaveCount(0)
  await editor.getByRole('radio', { name: 'From Existing Dimension' }).click()
  await editor
    .getByLabel('Parent Dimension', { exact: true })
    .selectOption({ label: 'StrategyParent' })
  await editor
    .getByRole('group', { name: 'Select keys from Parent keys' })
    .getByLabel('product', { exact: true })
    .check()
  await editor.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(editor).toHaveCount(0)
  const saved = await (await page.request.get(root + '/dimensions/' + original.objectId)).json()
  expect(saved.definition.population.operation).toBe('distinct_projection')
  expect(saved.definition.grain.keyIds).toEqual(original.definition.grain.keyIds)
  const bound = await (await page.request.get(root + '/transactions/strategy_bound')).json()
  expect(bound.definition.dimensionId).toBe(original.objectId)
  expect(bound.definition.keyMappings[0].grainColumnId).toBe(original.definition.grain.keyIds[0])
  await card.getByRole('button', { name: 'Edit', exact: true }).click()
  await expect(editor.getByRole('radio', { name: 'From Existing Dimension' })).toBeChecked()
  await expect(
    editor.getByLabel('Parent Dimension', { exact: true }).locator('option:checked'),
  ).toHaveText('StrategyParent')
})
