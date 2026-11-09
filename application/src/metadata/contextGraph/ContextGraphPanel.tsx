import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Background,
  Controls,
  Handle,
  MiniMap,
  Position,
  ReactFlow,
  useNodesState,
  type NodeProps,
  type ReactFlowInstance,
} from '@xyflow/react'
import { Boxes, Database, GitBranch, Layers, Sigma, Table2, X } from 'lucide-react'
import { CompatibilityGraphPanel } from '../CompatibilityGraphPanel'
import type { Snapshot } from '../types'
import { buildContextGraph, kindLabels, lineageIds, type ContextNodeKind } from './model'
import { layoutContextGraph, type ContextFlowNode, type ContextFlowEdge } from './layout'
import '@xyflow/react/dist/style.css'
import './contextGraph.css'

const icons = {
  table: Database,
  dimension: Boxes,
  transaction: Table2,
  grain: Layers,
  measure: Sigma,
}
function GraphNode({ data, selected }: NodeProps<ContextFlowNode>) {
  const Icon = icons[data.kind]
  return (
    <div className={`context-node ${data.kind} ${selected ? 'is-selected' : ''}`} title={data.name}>
      <Handle type="target" position={Position.Left} isConnectable={false} />
      <div className="context-node-kind">
        <Icon size={13} />
        <span>{kindLabels[data.kind]}</span>
        {data.base && <b>Base</b>}
      </div>
      <strong>{data.name}</strong>
      <small>{data.subtitle}</small>
      <Handle type="source" position={Position.Right} isConnectable={false} />
    </div>
  )
}
const nodeTypes = { context: GraphNode }
const nodeColors: Record<ContextNodeKind, string> = {
  table: '#94a3b8',
  dimension: '#8170d6',
  transaction: '#5088bd',
  grain: '#b18955',
  measure: '#27958c',
}

export default function ContextGraphPanel({
  data,
  baseDimensionId,
  base,
  revisionVersion,
}: {
  data: Snapshot
  baseDimensionId: string | null
  base: string
  revisionVersion: number
}) {
  const graph = useMemo(() => buildContextGraph(data, baseDimensionId), [data, baseDimensionId])
  const [selectedId, select] = useState<string | null>(null)
  const [focusRequest, focusObject] = useState<{ id: string } | null>(null)
  const [selectedEdgeId, selectEdge] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [showTables, setShowTables] = useState(true)
  const [showDependencies, setShowDependencies] = useState(false)
  const [focusLineage, setFocusLineage] = useState(false)
  const [compatibilityOpen, setCompatibilityOpen] = useState(false)
  const [layoutVersion, resetLayout] = useState(0)
  const instance = useRef<ReactFlowInstance<ContextFlowNode, ContextFlowEdge> | null>(null)
  const selected = graph.nodes.find((node) => node.id === selectedId)
  const selectedEdge = graph.edges.find((edge) => edge.id === selectedEdgeId)
  const inspected = selected ?? graph.nodes.find((node) => node.id === selectedEdge?.target)
  const enabledGraph = useMemo(() => {
    const nodes = graph.nodes.filter((node) => showTables || node.kind !== 'table')
    const ids = new Set(nodes.map((node) => node.id))
    return {
      nodes,
      edges: graph.edges.filter(
        (edge) =>
          ids.has(edge.source) &&
          ids.has(edge.target) &&
          (showDependencies || edge.relation !== 'formula'),
      ),
    }
  }, [graph, showTables, showDependencies])
  useEffect(() => {
    if (selectedId && !enabledGraph.nodes.some((node) => node.id === selectedId)) {
      select(null)
      setFocusLineage(false)
    }
    if (selectedEdgeId && !enabledGraph.edges.some((edge) => edge.id === selectedEdgeId))
      selectEdge(null)
  }, [enabledGraph, selectedId, selectedEdgeId])
  const lineage = useMemo(
    () => (selectedId ? lineageIds(enabledGraph, selectedId) : new Set<string>()),
    [enabledGraph, selectedId],
  )
  const visibleGraph = useMemo(
    () =>
      !focusLineage || !selectedId
        ? enabledGraph
        : {
            nodes: enabledGraph.nodes.filter((node) => lineage.has(node.id)),
            edges: enabledGraph.edges.filter(
              (edge) => lineage.has(edge.source) && lineage.has(edge.target),
            ),
          },
    [enabledGraph, focusLineage, selectedId, lineage],
  )
  const laidOut = useMemo(() => layoutContextGraph(visibleGraph), [visibleGraph, layoutVersion])
  const [nodes, setNodes, onNodesChange] = useNodesState<ContextFlowNode>(laidOut.nodes)
  useEffect(() => {
    setNodes(laidOut.nodes)
    const frame = requestAnimationFrame(() => {
      void instance.current?.fitView({ padding: 0.15, maxZoom: 1, duration: 250 })
    })
    return () => cancelAnimationFrame(frame)
  }, [laidOut, setNodes])
  useEffect(() => {
    if (!focusRequest || focusLineage || selectedId !== focusRequest.id) return
    const neighbours = new Set([focusRequest.id])
    for (const edge of enabledGraph.edges) {
      if (edge.source === selectedId || edge.target === selectedId) {
        neighbours.add(edge.source)
        neighbours.add(edge.target)
      }
    }
    const frame = requestAnimationFrame(() => {
      void instance.current?.fitView({
        nodes: [...neighbours].map((id) => ({ id })),
        padding: 0.25,
        minZoom: 0.65,
        maxZoom: 1,
        duration: 300,
      })
    })
    return () => cancelAnimationFrame(frame)
  }, [focusRequest, selectedId, focusLineage, enabledGraph])
  // Selection/search never rearranges the graph or discards user-dragged positions.
  const displayedNodes = useMemo(
    () =>
      nodes.map((node) => ({
        ...node,
        selected: node.id === selectedId,
        style: { opacity: selectedId && !lineage.has(node.id) ? 0.25 : 1 },
      })),
    [nodes, selectedId, lineage],
  )
  const edges = useMemo(
    () =>
      laidOut.edges.map((edge) => ({
        ...edge,
        label:
          (selectedId && (edge.source === selectedId || edge.target === selectedId)) ||
          edge.id === selectedEdgeId
            ? edge.data?.label
            : undefined,
        selected: edge.id === selectedEdgeId,
        style: {
          ...edge.style,
          opacity:
            selectedId && (!lineage.has(edge.source) || !lineage.has(edge.target)) ? 0.13 : 1,
        },
        labelStyle: { fill: '#52617b', fontSize: 11 },
        labelBgStyle: { fill: '#fff', fillOpacity: 0.95 },
      })),
    [laidOut.edges, selectedId, selectedEdgeId, lineage],
  )
  const inspectNode = useCallback((id: string) => {
    select(id)
    selectEdge(null)
    focusObject({ id })
  }, [])
  const matches = useMemo(
    () =>
      search.trim()
        ? enabledGraph.nodes
            .filter((node) =>
              node.name.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()),
            )
            .slice(0, 12)
        : [],
    [enabledGraph.nodes, search],
  )
  const connections = inspected
    ? graph.edges.filter((edge) => edge.source === inspected.id || edge.target === inspected.id)
    : []
  return (
    <section className="context-graph-panel" aria-label="Semantic context graph">
      <div className="context-graph-heading">
        <div>
          <h2>Semantic network</h2>
          <p>
            Trace dimensions, transactions and measures. Select an object to follow its lineage.
          </p>
        </div>
        <span className="context-graph-count">
          {visibleGraph.nodes.length} objects · {visibleGraph.edges.length} relationships
        </span>
      </div>
      <div className="context-graph-toolbar">
        <div className="context-graph-search">
          <label htmlFor="context-object-search">Find an object</label>
          <input
            id="context-object-search"
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Dimension, transaction or measure"
          />
          {!!search.trim() && (
            <div className="context-search-results" aria-label="Matching graph objects">
              {matches.length ? (
                matches.map((node) => (
                  <button
                    key={node.id}
                    type="button"
                    onClick={() => {
                      if (node.kind === 'table') setShowTables(true)
                      inspectNode(node.id)
                      setSearch('')
                    }}
                  >
                    <span>{node.name}</span>
                    <small>{kindLabels[node.kind]}</small>
                  </button>
                ))
              ) : (
                <p>No matching objects.</p>
              )}
            </div>
          )}
        </div>
        <label className="context-graph-toggle">
          <input
            type="checkbox"
            checked={showTables}
            onChange={(e) => setShowTables(e.target.checked)}
          />
          Source tables
        </label>
        <label className="context-graph-toggle">
          <input
            type="checkbox"
            checked={showDependencies}
            onChange={(e) => setShowDependencies(e.target.checked)}
          />
          Formula dependencies
        </label>
        <button
          type="button"
          className="secondary-button"
          onClick={() => resetLayout((version) => version + 1)}
        >
          Reset layout
        </button>
      </div>
      <div className="context-graph-legend" aria-label="Graph legend">
        <span>
          <i className="dimension" />
          Dimension
        </span>
        <span>
          <i className="transaction" />
          Transaction
        </span>
        <span>
          <i className="measure" />
          Measure
        </span>
        <span>
          <i className="line solid" />
          Population / mapping / output grain
        </span>
        <span>
          <i className="line dotted" />
          Referenced value source
        </span>
        {showDependencies && (
          <span>
            <i className="line dashed" />
            Formula input
          </span>
        )}
      </div>
      {!graph.nodes.length ? (
        <p className="context-graph-empty">
          Upload a source table or create a Dimension to start your semantic network.
        </p>
      ) : (
        <div className={`context-graph-body ${inspected ? 'has-inspector' : ''}`}>
          <div className="context-graph-canvas" aria-label="Semantic network canvas">
            <ReactFlow<ContextFlowNode, ContextFlowEdge>
              nodes={displayedNodes}
              edges={edges}
              nodeTypes={nodeTypes}
              onNodesChange={(changes) => {
                const chosen = changes.find((change) => change.type === 'select' && change.selected)
                if (chosen?.type === 'select') {
                  select(chosen.id)
                  selectEdge(null)
                }
                onNodesChange(changes)
              }}
              onInit={(flow) => {
                instance.current = flow
              }}
              onNodeClick={(_, node) => inspectNode(node.id)}
              onEdgeClick={(_, edge) => {
                select(null)
                selectEdge(edge.id)
              }}
              onPaneClick={() => {
                select(null)
                selectEdge(null)
                setFocusLineage(false)
              }}
              nodesConnectable={false}
              edgesReconnectable={false}
              deleteKeyCode={null}
              minZoom={0.08}
              maxZoom={1.8}
              fitView
              fitViewOptions={{ padding: 0.15, maxZoom: 1 }}
              onlyRenderVisibleElements
              ariaLabelConfig={{
                'controls.zoomIn.ariaLabel': 'Zoom in graph',
                'controls.zoomOut.ariaLabel': 'Zoom out graph',
                'controls.fitView.ariaLabel': 'Fit graph to view',
              }}
            >
              <Background gap={22} size={1} color="#dbe2ed" />
              <Controls showInteractive={false} />
              <MiniMap<ContextFlowNode>
                pannable
                zoomable
                nodeColor={(node) => nodeColors[node.data.kind]}
                ariaLabel="Graph overview"
              />
            </ReactFlow>
          </div>
          {inspected && (
            <aside className="context-graph-inspector" aria-label="Selected graph object">
              <button
                className="context-inspector-close"
                type="button"
                aria-label="Close graph details"
                onClick={() => {
                  select(null)
                  selectEdge(null)
                  setFocusLineage(false)
                }}
              >
                <X size={17} />
              </button>
              <span className={`context-detail-kind ${inspected.kind}`}>
                {kindLabels[inspected.kind]}
                {inspected.base ? ' · Base' : ''}
              </span>
              <h3>{inspected.name}</h3>
              {inspected.description && <p>{inspected.description}</p>}
              {selectedEdge && (
                <div className="context-edge-explanation">
                  <strong>{selectedEdge.label}</strong>
                  <p>{selectedEdge.explanation}</p>
                </div>
              )}
              <dl>
                {inspected.fields.map((field) => (
                  <div key={field.label}>
                    <dt>{field.label}</dt>
                    <dd>{field.value || 'Not configured'}</dd>
                  </div>
                ))}
              </dl>
              {selectedId && (
                <button
                  type="button"
                  className="secondary-button context-focus-button"
                  aria-pressed={focusLineage}
                  onClick={() => setFocusLineage(!focusLineage)}
                >
                  <GitBranch size={14} />
                  {focusLineage ? 'Show full network' : 'Focus on this lineage'}
                </button>
              )}
              <h4>Connections</h4>
              <ul>
                {connections.map((edge) => {
                  const other = graph.nodes.find(
                    (node) =>
                      node.id === (edge.source === inspected.id ? edge.target : edge.source),
                  )!
                  return (
                    <li key={edge.id}>
                      <button type="button" onClick={() => inspectNode(other.id)}>
                        <span>{edge.label}</span>
                        <strong>{other.name}</strong>
                      </button>
                      <p>{edge.explanation}</p>
                    </li>
                  )
                })}
              </ul>
              {inspected.kind === 'measure' && (
                <p className="context-graph-footnote">
                  Output grain shows where the measure is defined. Use compatibility checks to
                  confirm where it can be plotted.
                </p>
              )}
            </aside>
          )}
        </div>
      )}
      <p className="context-graph-footnote">
        Drag to pan · Scroll to zoom · Drag objects to rearrange · Dotted lines show a value source,
        not another output grain.
      </p>
      <details
        className="context-compatibility"
        onToggle={(event) => setCompatibilityOpen(event.currentTarget.open)}
      >
        <summary>Check plotting compatibility</summary>
        {compatibilityOpen && (
          <CompatibilityGraphPanel base={base} revisionVersion={revisionVersion} />
        )}
      </details>
    </section>
  )
}
