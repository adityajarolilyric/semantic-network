import { expect, test } from '@playwright/test'

test('create typed references, attribute formulas and a mixed pivot through the UI', async ({
  page,
}) => {
  test.setTimeout(120_000)
  await page.goto('/')
  const model = 'typed_measures_ui'
  const root = `/api/v1/models/${model}/revisions/draft_1`
  async function post(path: string, data: unknown) {
    const response = await page.request.post(path, {
      data,
      headers: { 'Idempotency-Key': 'typed_' + crypto.randomUUID() },
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
  const imported = await page.request.post('/api/v1/warehouse/imports/csv', {
    headers: { 'Idempotency-Key': 'typed_' + crypto.randomUUID() },
    multipart: {
      tableName: 'UI_TypedFacts',
      file: {
        name: 'typed.csv',
        mimeType: 'text/csv',
        buffer: Buffer.from(
          'product_id,location_id,status,dispatch,active,qty\n001,L1,Healthy,2026-11-01,true,100\n001,L2,At Risk,2026-11-03,false,50\n002,L1,Healthy,2026-11-04,true,40\n',
        ),
      },
    },
  })
  expect(imported.ok(), await imported.text()).toBeTruthy()
  const managed = await post(root + '/managed-tables/enable', { tableName: 'UI_TypedFacts' })
  await expect
    .poll(
      async () =>
        (await (await page.request.get(root + '/engine/runs/' + managed.runId)).json()).state,
    )
    .toBe('succeeded')
  await page.goto(`/?model=${model}&revision=draft_1&tab=transactions`)
  await page.getByRole('button', { name: 'Create transaction', exact: true }).click()
  await page.getByLabel('Name', { exact: true }).fill('Typed Facts')
  await page.getByLabel('Managed table', { exact: true }).selectOption({ label: 'UI_TypedFacts' })
  const keys = page.getByRole('group', { name: 'Transaction grain columns', exact: true })
  await keys.getByLabel('product_id', { exact: true }).check()
  await keys.getByLabel('location_id', { exact: true }).check()
  await page
    .locator('.transformation-section > summary')
    .filter({ hasText: /^Measures/ })
    .click()
  const grid = page.locator('.transaction-measures-table')
  for (const [name, policy] of [
    ['qty', 'sum'],
    ['status', 'single_value'],
    ['dispatch', 'max'],
    ['active', 'any_true'],
  ]) {
    await grid.getByLabel(name, { exact: true }).check()
    if (name === 'qty') {
      await grid.getByLabel(name + ' non-time aggregation').selectOption(policy)
      await grid.getByLabel(name + ' time aggregation').selectOption(policy)
    } else if (name === 'status') {
      await expect(grid.getByLabel(name + ' aggregation')).toBeDisabled()
    } else {
      await grid.getByLabel(name + ' aggregation').selectOption(policy)
    }
    if (name !== 'qty') {
      await expect(grid.getByLabel(name + ' editable')).toBeDisabled()
      await expect(grid.getByLabel(name + ' dis-aggregation policy')).toHaveCount(0)
    }
  }
  await page.getByRole('button', { name: 'Save metadata', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Metadata editor' })).toHaveCount(0)
  await page.getByRole('button', { name: 'Measures', exact: true }).click()
  for (const [name, formula] of [
    ['Risk Status', 'IF([qty] < 80, "At Risk", "Healthy")'],
    ['Next Dispatch', 'ADD_DAYS([dispatch], 2)'],
    ['Positive Cells', 'COUNT_IF([qty] > 0)'],
  ]) {
    await page.getByRole('button', { name: 'Create measure', exact: true }).click()
    await page.getByRole('radio', { name: 'Calculated', exact: true }).check()
    await page
      .getByLabel('Output grain', { exact: true })
      .selectOption({ label: 'Typed Facts grain' })
    await page.getByLabel('Formula', { exact: true }).fill(`${name} = ${formula}`)
    await expect(page.getByLabel('Counting Level', { exact: true })).toHaveCount(0)
    await expect(
      page.getByRole('heading', { name: 'Dis-Aggregation policies', exact: true }),
    ).toHaveCount(0)
    await page.getByRole('button', { name: 'Save metadata', exact: true }).click()
    await expect(page.getByRole('region', { name: 'Metadata editor' })).toHaveCount(0)
  }
  await page.getByRole('button', { name: 'Create measure', exact: true }).click()
  await page.getByLabel('Name', { exact: true }).fill('Product Code')
  await page.getByRole('radio', { name: 'Calculated', exact: true }).check()
  await page
    .getByLabel('Output grain', { exact: true })
    .selectOption({ label: 'Typed Facts grain' })
  await page.getByRole('tab', { name: 'Columns', exact: true }).click()
  await page
    .getByRole('button', { name: 'Insert Typed Facts grain.product_id', exact: true })
    .click()
  await expect(page.getByLabel('Formula', { exact: true })).toHaveValue(
    '[Typed Facts grain.product_id]',
  )
  await page.getByRole('button', { name: 'Save metadata', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Metadata editor' })).toHaveCount(0)
  const measures = (await (await page.request.get(root + '/measures')).json()).items
  expect(
    measures.find((measure: { name: string }) => measure.name === 'Risk Status').definition
      .resultType.type,
  ).toBe('string')
  await page.locator('nav').getByRole('button', { name: 'Decision View', exact: true }).click()
  await page
    .getByRole('region', { name: 'Decision View', exact: true })
    .getByRole('button', { name: 'Build Semantic Network', exact: true })
    .click()
  await expect(
    page
      .getByRole('region', { name: 'Decision View', exact: true })
      .getByRole('button', { name: 'Build Semantic Network', exact: true }),
  ).toBeEnabled()
  await page.getByRole('button', { name: 'Context Graph', exact: true }).click()
  await page.getByText('Check plotting compatibility', { exact: true }).click()
  await page.getByRole('button', { name: 'Select qty for compatibility', exact: true }).click()
  await page
    .getByRole('button', { name: 'Select Positive Cells for compatibility', exact: true })
    .click()
  await expect(
    page.getByText('The full selection can be plotted together in a shown context.', {
      exact: true,
    }),
  ).toBeVisible()
  await expect(
    page.getByRole('button', { name: 'Select Positive Cells for compatibility', exact: true }),
  ).toHaveAttribute('aria-pressed', 'true')
  await page.screenshot({ path: 'test-results/context-graph.png', fullPage: true })
  await page.getByRole('button', { name: 'Decision View', exact: true }).click()
  await page
    .getByLabel('Add rows attribute')
    .selectOption({ label: 'Typed Facts grain · product_id' })
  for (const name of [
    'status',
    'dispatch',
    'active',
    'Risk Status',
    'Next Dispatch',
    'Product Code',
    'Positive Cells',
  ])
    await page.getByLabel(name, { exact: true }).check()
  await page.getByRole('button', { name: 'Check compatibility', exact: true }).click()
  await expect(
    page.getByText('The full selection can be plotted together in a shown context.', {
      exact: true,
    }),
  ).toBeVisible()
  await page.getByRole('button', { name: 'Hide compatibility', exact: true }).click()
  await page.getByRole('button', { name: 'Update pivot', exact: true }).click()
  await expect(page.getByRole('cell', { name: 'Mixed', exact: true })).toBeVisible()
  await expect(page.getByRole('cell', { name: '2026-11-05', exact: true })).toBeVisible()
  await expect(page.getByRole('cell', { name: 'True', exact: true }).first()).toBeVisible()
  await expect(page.getByRole('cell', { name: '001', exact: true })).toBeVisible()
  await expect(page.getByRole('cell', { name: '001.00', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: /^Edit / })).toHaveCount(0)
  await expect(page.getByRole('cell', { name: '2.00', exact: true })).toBeVisible()
  await page.screenshot({ path: 'test-results/typed-measures-pivot.png', fullPage: true })
})
