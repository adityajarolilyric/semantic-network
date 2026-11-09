import { expect, test } from '@playwright/test'
import { contextGraphFixture } from '../fixtures/contextGraph'

test('context graph shows lineage, output grains and dotted transaction references with interactive navigation', async ({
  page,
}) => {
  const data = contextGraphFixture()
  const model = {
    modelId: 'graph_model',
    name: 'Graph model',
    displayName: 'Graph model',
    version: 1,
    draftRevisionId: 'draft',
    publishedRevisionId: null,
    revisionCounter: 1,
  }
  const revision = {
    modelId: 'graph_model',
    revisionId: 'draft',
    version: 1,
    revisionNumber: 1,
    approvalStatus: 'draft',
    metadataContractVersion: 2,
    baseDimensionId: 'base',
    changeReason: '',
    parentRevisionId: null,
  }
  const root = '/api/v1/models/graph_model/revisions/draft'
  const pages: Record<string, unknown[]> = {
    '/api/v1/models': [model],
    '/api/v1/models/graph_model/revisions': [revision],
    [root + '/sources']: data.sources,
    [root + '/dimensions']: data.dimensions,
    [root + '/transactions']: data.transactions,
    [root + '/measures']: data.measures,
    [root + '/managed-tables']: [],
  }
  const writes: string[] = []
  await page.route('**/api/v1/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (route.request().method() !== 'GET') writes.push(path)
    if (pages[path]) return route.fulfill({ json: { items: pages[path], nextCursor: null } })
    await route.continue()
  })
  await page.goto('/?model=graph_model&revision=draft&tab=context-graph')
  const graph = page.getByRole('region', { name: 'Semantic context graph', exact: true })
  await expect(graph).toBeVisible()
  const node = (id: string) => graph.locator(`.react-flow__node[data-id="${id}"]`)
  await expect(node('dimension:base')).toContainText('Product Location Week')
  await expect(node('dimension:base')).toContainText('Base')
  await expect(node('dimension:derived')).toContainText('Brand Zone Week')
  await expect(node('transaction:forecast-tx')).toBeVisible()
  const reference = graph.locator(
    '.react-flow__edge[data-id="reference:transaction:forecast-tx:measure:fqty"] path.react-flow__edge-path',
  )
  await expect(reference).toHaveCSS('stroke-dasharray', '2px, 6px')
  await expect(
    graph.locator('.react-flow__edge[data-id="output:dimension:base:measure:fqty"]'),
  ).toBeAttached()
  const initialPosition = await node('dimension:derived').evaluate(
    (el) => (el as HTMLElement).style.transform,
  )
  await node('measure:fqty').click()
  const details = page.getByRole('complementary', { name: 'Selected graph object' })
  await expect(details.getByRole('heading', { name: 'Forecast Qty', exact: true })).toBeVisible()
  await expect(details.getByText('Product Location Week', { exact: true }).first()).toBeVisible()
  await expect(details.getByText('Forecast · forecast_qty', { exact: true })).toBeVisible()
  expect(
    await node('dimension:derived').evaluate((el) => (el as HTMLElement).style.transform),
  ).toBe(initialPosition)
  await expect
    .poll(() =>
      graph
        .locator('.react-flow__viewport')
        .evaluate((el) => new DOMMatrix((el as HTMLElement).style.transform).a),
    )
    .toBeGreaterThanOrEqual(0.649)
  await page.screenshot({ path: 'test-results/context-graph-lineage.png', fullPage: true })
  const originalBasePosition = await node('dimension:base').evaluate(
    (el) => (el as HTMLElement).style.transform,
  )
  const bounds = (await node('dimension:base').boundingBox())!
  await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2)
  await page.mouse.down()
  await page.mouse.move(bounds.x + bounds.width / 2 + 45, bounds.y + bounds.height / 2 - 30, {
    steps: 8,
  })
  await page.mouse.up()
  await expect
    .poll(() => node('dimension:base').evaluate((el) => (el as HTMLElement).style.transform))
    .not.toBe(originalBasePosition)
  await graph.getByRole('button', { name: 'Reset layout', exact: true }).click()
  await expect
    .poll(() => node('dimension:base').evaluate((el) => (el as HTMLElement).style.transform))
    .toBe(originalBasePosition)
  await node('measure:fqty').click()
  await graph.getByRole('button', { name: 'Focus on this lineage', exact: true }).click()
  await expect(node('measure:orders')).toHaveCount(0)
  await graph.getByRole('button', { name: 'Show full network', exact: true }).click()
  await graph.getByRole('button', { name: 'Close graph details' }).click()
  await graph.getByLabel('Find an object').fill('Absolute Error')
  await graph.getByRole('button', { name: 'Absolute Error Measure', exact: true }).click()
  await expect(details.getByRole('heading', { name: 'Absolute Error' })).toBeVisible()
  await graph.getByLabel('Formula dependencies', { exact: true }).check()
  await expect(
    graph.locator('.react-flow__edge[data-id="formula:measure:fqty:measure:error"]'),
  ).toBeAttached()
  await graph.getByLabel('Source tables', { exact: true }).uncheck()
  await expect(graph.locator('.context-node.table')).toHaveCount(0)
  await expect(graph.getByRole('button', { name: 'Zoom in graph' })).toBeVisible()
  await expect(graph.getByRole('button', { name: 'Fit graph to view' })).toBeVisible()
  await page.setViewportSize({ width: 390, height: 844 })
  await graph.getByRole('button', { name: 'Close graph details' }).click()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  )
  await page.screenshot({ path: 'test-results/context-graph-mobile.png', fullPage: true })
  expect(writes).toEqual([])
})
