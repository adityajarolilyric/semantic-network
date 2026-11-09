import dagre from '@dagrejs/dagre'
import { MarkerType, Position, type Edge, type Node } from '@xyflow/react'
import type { ContextGraph, ContextNode, ContextEdge } from './model'

export type ContextFlowNode = Node<ContextNode & Record<string, unknown>, 'context'>
export type ContextFlowEdge = Edge<ContextEdge & Record<string, unknown>>
export const NODE_WIDTH = 228
export const NODE_HEIGHT = 94
export const relationColors = {
  population: '#94a3b8',
  projection: '#8170d6',
  binding: '#8170d6',
  mapping: '#5088bd',
  output: '#27958c',
  reference: '#5088bd',
  formula: '#c18a42',
  calendar: '#9d7cba',
}

/** Dagre handles arbitrary dimension depth and shared parents; canvas positions are UI-only. */
export function layoutContextGraph(graph: ContextGraph): {
  nodes: ContextFlowNode[]
  edges: ContextFlowEdge[]
} {
  const layout = new dagre.graphlib.Graph({ multigraph: true })
  layout.setGraph({ rankdir: 'LR', nodesep: 36, ranksep: 100, marginx: 38, marginy: 38 })
  layout.setDefaultEdgeLabel(() => ({}))
  for (const node of graph.nodes)
    layout.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT })
  for (const edge of graph.edges)
    layout.setEdge(
      edge.source,
      edge.target,
      { weight: edge.relation === 'output' ? 3 : 1 },
      edge.id,
    )
  dagre.layout(layout)
  return {
    nodes: graph.nodes.map((node) => {
      const point = layout.node(node.id)
      return {
        id: node.id,
        type: 'context',
        data: node,
        position: { x: point.x - NODE_WIDTH / 2, y: point.y - NODE_HEIGHT / 2 },
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
        width: NODE_WIDTH,
        height: NODE_HEIGHT,
        ariaLabel: `${node.kind === 'grain' ? 'Transaction grain' : node.kind} ${node.name}`,
        deletable: false,
      }
    }),
    edges: graph.edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      type: 'smoothstep',
      data: edge,
      ariaLabel: edge.label,
      deletable: false,
      selectable: true,
      style: {
        stroke: relationColors[edge.relation],
        strokeWidth: edge.relation === 'output' ? 2.2 : 1.6,
        ...(edge.relation === 'reference'
          ? { strokeDasharray: '2 6', strokeLinecap: 'round' as const }
          : edge.relation === 'formula'
            ? { strokeDasharray: '7 5' }
            : {}),
      },
      markerEnd: {
        type: MarkerType.ArrowClosed,
        color: relationColors[edge.relation],
        width: 16,
        height: 16,
      },
    })),
  }
}
