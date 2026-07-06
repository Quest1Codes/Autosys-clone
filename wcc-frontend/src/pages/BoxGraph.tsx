import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useParams, Link } from 'react-router-dom';
import * as d3 from 'd3';
import { fetchBoxGraph } from '../api/boxes';
import { useSSE } from '../hooks/useSSE';
import Header from '../components/Header';
import type { GraphNode, GraphEdge, JobStatus } from '../types';

const STATUS_COLORS: Record<string, string> = {
  SUCCESS: '#00A650', FAILURE: '#CC0000', RUNNING: '#0055A5',
  STARTING: '#FF8C00', ACTIVATED: '#4A90D9', ON_HOLD: '#FFD700',
  ON_ICE: '#808080', INACTIVE: '#CCCCCC', TERMINATED: '#8B0000', QUE_WAIT: '#6A0DAD',
};

const NODE_W = 160;
const NODE_H = 44;
const LEVEL_W = 220;
const ROW_H = 70;
const PAD = 40;

interface LayoutNode extends GraphNode {
  x: number;
  y: number;
}

function computeLayout(nodes: GraphNode[], edges: GraphEdge[]): LayoutNode[] {
  if (!nodes.length) return [];

  const outEdges = new Map<string, string[]>();
  const inDegree = new Map<string, number>();

  for (const n of nodes) { outEdges.set(n.id, []); inDegree.set(n.id, 0); }
  for (const e of edges) {
    outEdges.get(e.source)?.push(e.target);
    inDegree.set(e.target, (inDegree.get(e.target) ?? 0) + 1);
  }

  // Kahn BFS for topological levels
  const levels = new Map<string, number>();
  const queue: string[] = [];
  for (const [id, deg] of inDegree) { if (deg === 0) { queue.push(id); levels.set(id, 0); } }

  let qi = 0;
  while (qi < queue.length) {
    const cur = queue[qi++];
    const curLvl = levels.get(cur) ?? 0;
    for (const nxt of outEdges.get(cur) ?? []) {
      const nxtLvl = Math.max(levels.get(nxt) ?? 0, curLvl + 1);
      levels.set(nxt, nxtLvl);
      if (!levels.has(nxt) || levels.get(nxt) === nxtLvl) {
        queue.push(nxt);
      }
    }
  }

  // Handle disconnected nodes
  for (const n of nodes) { if (!levels.has(n.id)) levels.set(n.id, 0); }

  // Group by level
  const byLevel = new Map<number, string[]>();
  for (const [id, lvl] of levels) {
    const arr = byLevel.get(lvl) ?? [];
    arr.push(id);
    byLevel.set(lvl, arr);
  }

  const nodeMap = new Map(nodes.map(n => [n.id, n]));
  const layoutNodes: LayoutNode[] = [];

  for (const [lvl, ids] of byLevel) {
    ids.forEach((id, idx) => {
      const node = nodeMap.get(id);
      if (!node) return;
      layoutNodes.push({
        ...node,
        x: PAD + lvl * LEVEL_W,
        y: PAD + idx * ROW_H,
      });
    });
  }

  return layoutNodes;
}

const LEGEND_STATUSES = ['RUNNING','STARTING','SUCCESS','FAILURE','ACTIVATED','ON_HOLD','ON_ICE','INACTIVE','TERMINATED','QUE_WAIT'];

const BoxGraph: React.FC = () => {
  const { name } = useParams<{ name: string }>();
  const svgRef = useRef<SVGSVGElement>(null);
  const [rawNodes, setRawNodes]   = useState<GraphNode[]>([]);
  const [edges, setEdges]         = useState<GraphEdge[]>([]);
  const [statuses, setStatuses]   = useState<Map<string, JobStatus>>(new Map());
  const [tooltip, setTooltip]     = useState<{ x: number; y: number; text: string } | null>(null);
  const [loading, setLoading]     = useState(true);
  const [error, setError]         = useState<string | null>(null);
  const [sseStatus, setSseStatus] = useState<'connecting'|'live'|'disconnected'>('connecting');

  const load = useCallback(async () => {
    if (!name) return;
    setLoading(true);
    setError(null);
    try {
      const data = await fetchBoxGraph(name);
      setRawNodes(data.nodes);
      setEdges(data.edges);
      const m = new Map<string, JobStatus>();
      data.nodes.forEach(n => m.set(n.id, n.status));
      setStatuses(m);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load graph');
    } finally {
      setLoading(false);
    }
  }, [name]);

  useEffect(() => { load(); }, [load]);

  const handleSSEUpdate = useCallback((updates: Array<{ job_name: string; status: string }>) => {
    setStatuses(prev => {
      const next = new Map(prev);
      updates.forEach(u => {
        if (next.has(u.job_name)) next.set(u.job_name, u.status as JobStatus);
      });
      return next;
    });
    setSseStatus('live');
  }, []);

  const sseState = useSSE(handleSSEUpdate);
  useEffect(() => { setSseStatus(sseState); }, [sseState]);

  // Merge live statuses into nodes
  const nodes = rawNodes.map(n => ({ ...n, status: statuses.get(n.id) ?? n.status }));
  const layoutNodes = computeLayout(nodes, edges);

  // D3 render
  useEffect(() => {
    if (!svgRef.current || layoutNodes.length === 0) return;

    const svgEl = svgRef.current;
    const container = svgEl.parentElement!;
    const W = container.clientWidth || 900;
    const H = container.clientHeight || 600;

    const maxX = Math.max(...layoutNodes.map(n => n.x + NODE_W)) + PAD;
    const maxY = Math.max(...layoutNodes.map(n => n.y + NODE_H)) + PAD;
    const svgW = Math.max(W, maxX);
    const svgH = Math.max(H, maxY);

    const svg = d3.select(svgEl);
    svg.selectAll('*').remove();
    svg.attr('width', svgW).attr('height', svgH);

    // Arrow marker
    const defs = svg.append('defs');
    defs.append('marker')
      .attr('id', 'arrow')
      .attr('viewBox', '0 -5 10 10')
      .attr('refX', 10)
      .attr('refY', 0)
      .attr('markerWidth', 8)
      .attr('markerHeight', 8)
      .attr('orient', 'auto')
      .append('path')
      .attr('d', 'M0,-5L10,0L0,5')
      .attr('fill', '#666');

    // Zoom/pan group
    const g = svg.append('g');
    svg.call(d3.zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.3, 3])
      .on('zoom', (event) => { g.attr('transform', event.transform.toString()); }));

    const nodeMap = new Map(layoutNodes.map(n => [n.id, n]));

    // Edges
    const edgeGroup = g.append('g').attr('class', 'edges');
    edgeGroup.selectAll('path')
      .data(edges)
      .enter()
      .append('path')
      .attr('d', (e) => {
        const src = nodeMap.get(e.source);
        const tgt = nodeMap.get(e.target);
        if (!src || !tgt) return '';
        const x1 = src.x + NODE_W;
        const y1 = src.y + NODE_H / 2;
        const x2 = tgt.x;
        const y2 = tgt.y + NODE_H / 2;
        const mx = (x1 + x2) / 2;
        return `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`;
      })
      .attr('fill', 'none')
      .attr('stroke', '#888')
      .attr('stroke-width', 1.5)
      .attr('marker-end', 'url(#arrow)');

    // Node groups
    const nodeGroups = g.append('g').attr('class', 'nodes')
      .selectAll('g')
      .data(layoutNodes)
      .enter()
      .append('g')
      .attr('transform', d => `translate(${d.x},${d.y})`)
      .style('cursor', 'pointer')
      .on('mouseover', (event: MouseEvent, d: LayoutNode) => {
        setTooltip({ x: event.clientX + 8, y: event.clientY - 28, text: `${d.id} — ${d.status}` });
      })
      .on('mouseout', () => setTooltip(null))
      .on('click', (_event: MouseEvent, d: LayoutNode) => {
        window.location.href = `/jobs/${encodeURIComponent(d.id)}`;
      });

    // Node rect
    nodeGroups.append('rect')
      .attr('width', NODE_W)
      .attr('height', NODE_H)
      .attr('rx', 3)
      .attr('ry', 3)
      .attr('fill', d => STATUS_COLORS[d.status] ?? '#CCC')
      .attr('stroke', d => d.type === 'BOX' ? '#003D7C' : '#666')
      .attr('stroke-width', d => d.type === 'BOX' ? 2.5 : 1.5)
      .attr('opacity', 0.9);

    // Job type badge
    nodeGroups.append('rect')
      .attr('x', NODE_W - 30)
      .attr('y', 2)
      .attr('width', 28)
      .attr('height', 14)
      .attr('rx', 2)
      .attr('fill', 'rgba(0,0,0,0.25)');

    nodeGroups.append('text')
      .attr('x', NODE_W - 16)
      .attr('y', 12)
      .attr('text-anchor', 'middle')
      .attr('fill', '#FFF')
      .attr('font-size', 8)
      .attr('font-family', 'Arial, Helvetica, sans-serif')
      .text(d => d.type);

    // Job name
    nodeGroups.append('text')
      .attr('x', 8)
      .attr('y', NODE_H / 2 - 4)
      .attr('fill', '#FFFFFF')
      .attr('font-size', 11)
      .attr('font-weight', 'bold')
      .attr('font-family', 'Arial, Helvetica, sans-serif')
      .text(d => d.id.length > 18 ? d.id.slice(0, 16) + '…' : d.id);

    // Status text
    nodeGroups.append('text')
      .attr('x', 8)
      .attr('y', NODE_H / 2 + 10)
      .attr('fill', 'rgba(255,255,255,0.85)')
      .attr('font-size', 9)
      .attr('font-family', 'Arial, Helvetica, sans-serif')
      .text(d => d.status);

  }, [layoutNodes, edges]);

  return (
    <div className="wcc-shell">
      <Header sseStatus={sseStatus} />
      <div className="wcc-content">
        <div className="box-graph-page">
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '4px 0', flexShrink: 0 }}>
            <Link to="/" className="back-link">← Job Monitor</Link>
            <span style={{ color: '#AAA' }}>›</span>
            <span style={{ fontWeight: 700, color: '#003D7C', fontSize: 13 }}>BOX Graph: {name}</span>
            <button className="wcc-btn wcc-btn-secondary" onClick={load} style={{ marginLeft: 'auto' }}>↺ Refresh</button>
          </div>

          {/* Legend */}
          <div className="graph-legend">
            {LEGEND_STATUSES.map(s => (
              <div key={s} className="legend-item">
                <span className="legend-dot" style={{ background: STATUS_COLORS[s] }} />
                <span style={{ fontSize: 10 }}>{s}</span>
              </div>
            ))}
          </div>

          <div className="box-graph-canvas">
            {loading && <div className="loading-state">Loading graph…</div>}
            {error  && <div className="empty-state" style={{ color: '#CC0000' }}>Error: {error}</div>}
            {!loading && !error && layoutNodes.length === 0 && (
              <div className="empty-state">No graph data available for this BOX.</div>
            )}
            <svg ref={svgRef} style={{ width: '100%', height: '100%' }} />
          </div>

          {tooltip && (
            <div style={{
              position: 'fixed', left: tooltip.x, top: tooltip.y,
              background: 'rgba(0,0,0,0.8)', color: '#FFF',
              fontSize: 11, padding: '4px 8px', borderRadius: 3,
              pointerEvents: 'none', zIndex: 999, whiteSpace: 'nowrap',
            }}>
              {tooltip.text}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default BoxGraph;
