import { expect, it } from 'vitest'
import { buildContextGraph, lineageIds } from '../src/metadata/contextGraph/model'
import { layoutContextGraph } from '../src/metadata/contextGraph/layout'
import { contextGraphFixture } from './fixtures/contextGraph'

it('distinguishes a referenced measure’s output dimension from its transaction value source', () => {
  const graph = buildContextGraph(contextGraphFixture(), 'base')
  expect(graph.edges.filter((edge) => edge.target === 'measure:fqty')).toEqual(
    expect.arrayContaining([
      expect.objectContaining({ source: 'dimension:base', relation: 'output' }),
      expect.objectContaining({ source: 'transaction:forecast-tx', relation: 'reference' }),
    ]),
  )
  expect(graph.nodes.find((n) => n.id === 'measure:fqty')?.fields).toContainEqual({
    label: 'Output grain',
    value: 'Product Location Week',
  })
  expect(graph.nodes.find((n) => n.id === 'dimension:base')?.base).toBe(true)
  expect(graph.nodes.some((n) => n.name.includes('stale-context'))).toBe(false)
})
it('shows multiple population inputs, parent projections and public dimension bindings once', () => {
  const graph = buildContextGraph(contextGraphFixture(), 'base')
  expect(graph.nodes.filter((n) => n.name === 'Product')).toHaveLength(1)
  expect(graph.edges).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        source: 'table:intersections',
        target: 'dimension:base',
        relation: 'population',
      }),
      expect.objectContaining({
        source: 'table:calendar',
        target: 'dimension:base',
        relation: 'population',
      }),
      expect.objectContaining({
        source: 'dimension:product',
        target: 'dimension:base',
        relation: 'binding',
      }),
      expect.objectContaining({
        source: 'dimension:base',
        target: 'dimension:derived',
        relation: 'projection',
      }),
      expect.objectContaining({
        source: 'dimension:derived',
        target: 'transaction:sales-tx',
        relation: 'mapping',
      }),
    ]),
  )
})
it('retains a standalone transaction grain without inventing a dimension or matching names', () => {
  const data = contextGraphFixture()
  const graph = buildContextGraph(data, null)
  expect(graph.edges).toContainEqual(
    expect.objectContaining({
      source: 'grain:standalone',
      target: 'measure:orders',
      relation: 'output',
    }),
  )
  expect(graph.nodes.find((n) => n.id === 'grain:standalone')?.kind).toBe('grain')
  expect(
    graph.edges.some((e) => e.source === 'dimension:derived' && e.target === 'measure:orders'),
  ).toBe(false)
  expect(data.dimensions).toHaveLength(3)
})
it('adds calculated dependencies without claiming a source transaction or another native dimension', () => {
  const graph = buildContextGraph(contextGraphFixture(), null)
  expect(
    graph.edges.filter((e) => e.target === 'measure:error').map((e) => [e.source, e.relation]),
  ).toEqual(
    expect.arrayContaining([
      ['dimension:derived', 'output'],
      ['measure:fqty', 'formula'],
      ['measure:sqty', 'formula'],
    ]),
  )
  expect(graph.edges.some((e) => e.target === 'measure:error' && e.relation === 'reference')).toBe(
    false,
  )
})
it('never creates dangling edges when metadata is missing and handles empty metadata', () => {
  const data = contextGraphFixture()
  data.sources = []
  data.dimensions = []
  data.dimensionMembers = []
  const graph = buildContextGraph(data, null)
  const ids = new Set(graph.nodes.map((n) => n.id))
  expect(graph.edges.every((e) => ids.has(e.source) && ids.has(e.target))).toBe(true)
  expect(
    buildContextGraph(
      { sources: [], dimensions: [], dimensionMembers: [], transactions: [], measures: [] },
      null,
    ),
  ).toEqual({ nodes: [], edges: [] })
})
it('keeps sibling branches out of lineage and terminates even for malformed cycles', () => {
  const graph = buildContextGraph(contextGraphFixture(), null)
  const ids = lineageIds(graph, 'measure:orders')
  expect(ids.has('grain:standalone')).toBe(true)
  expect(ids.has('measure:sqty')).toBe(false)
  const cyclic = {
    ...graph,
    edges: [...graph.edges, { ...graph.edges[0], source: 'measure:orders', target: 'table:sales' }],
  }
  expect(lineageIds(cyclic, 'measure:orders').has('measure:orders')).toBe(true)
})
it('lays out deep dimension lineage without overlapping nodes and renders reference links dotted', () => {
  const graph = buildContextGraph(contextGraphFixture(), 'base')
  const layout = layoutContextGraph(graph)
  const parent = layout.nodes.find((n) => n.id === 'dimension:base')!
  const child = layout.nodes.find((n) => n.id === 'dimension:derived')!
  expect(child.position.x).toBeGreaterThan(parent.position.x)
  for (const [i, node] of layout.nodes.entries())
    for (const other of layout.nodes.slice(i + 1)) {
      expect(
        Math.abs(node.position.x - other.position.x) >= 228 ||
          Math.abs(node.position.y - other.position.y) >= 94,
      ).toBe(true)
    }
  expect(layout.edges.find((e) => e.data?.relation === 'reference')?.style?.strokeDasharray).toBe(
    '2 6',
  )
  expect(
    layout.edges.find((e) => e.data?.relation === 'output')?.style?.strokeDasharray,
  ).toBeUndefined()
})
