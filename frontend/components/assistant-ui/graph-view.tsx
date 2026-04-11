"use client";

import { useEffect, useState, useCallback } from "react";
import Graph from "graphology";
import forceAtlas2 from "graphology-layout-forceatlas2";
import { SigmaContainer, useLoadGraph, useRegisterEvents } from "@react-sigma/core";
// CSS is bundled automatically by @react-sigma/core; no separate import needed
import { Trash2Icon, XIcon } from "lucide-react";
import { Button } from "@/components/ui/button";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface GraphNode {
  id: string;
  label: string;
  name: string;
  content?: string;
  access_count?: number;
  created_at?: string;
}

export interface GraphEdge {
  source_id: string;
  target_id: string;
  type: string;
  weight: number;
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

// ---------------------------------------------------------------------------
// Visual encoding constants
// ---------------------------------------------------------------------------

const LABEL_COLORS: Record<string, string> = {
  Concept: "#6366f1", // indigo
  Project: "#10b981", // emerald
  Note:    "#f59e0b", // amber
  Tag:     "#8b5cf6", // violet
};

const DEFAULT_COLOR = "#9ca3af";

function nodeSize(access_count: number): number {
  return Math.max(4, Math.log(access_count + 1) * 3);
}

// ---------------------------------------------------------------------------
// Inner components (must live inside SigmaContainer)
// ---------------------------------------------------------------------------

function GraphLoader({
  data,
  onGraphReady,
}: {
  data: GraphData;
  onGraphReady: (nodeMap: Map<string, GraphNode>) => void;
}) {
  const loadGraph = useLoadGraph();

  useEffect(() => {
    const g = new Graph({ multi: false, type: "directed" });
    const nodeMap = new Map<string, GraphNode>();

    for (const node of data.nodes) {
      if (!node.id) continue;
      nodeMap.set(node.id, node);
      g.addNode(node.id, {
        label: node.name,
        size: nodeSize(node.access_count ?? 0),
        color: LABEL_COLORS[node.label] ?? DEFAULT_COLOR,
        x: Math.random() * 100,
        y: Math.random() * 100,
      });
    }

    for (const edge of data.edges) {
      if (!edge.source_id || !edge.target_id) continue;
      if (!g.hasNode(edge.source_id) || !g.hasNode(edge.target_id)) continue;
      try {
        g.addEdge(edge.source_id, edge.target_id, {
          label: edge.type,
          size: Math.max(0.5, (edge.weight ?? 1) * 0.5),
          color: "#cbd5e1",
        });
      } catch {
        // Skip duplicate edges
      }
    }

    if (g.order > 0) {
      forceAtlas2.assign(g, {
        iterations: 50,
        settings: forceAtlas2.inferSettings(g),
      });
    }

    loadGraph(g);
    onGraphReady(nodeMap);
  }, [data, loadGraph, onGraphReady]);

  return null;
}

function EventHandlers({
  onNodeClick,
}: {
  onNodeClick: (nodeId: string) => void;
}) {
  const registerEvents = useRegisterEvents();

  useEffect(() => {
    registerEvents({
      clickNode: ({ node }: { node: string }) => onNodeClick(node),
      clickStage: () => onNodeClick(""),
    });
  }, [registerEvents, onNodeClick]);

  return null;
}

// ---------------------------------------------------------------------------
// Node detail panel
// ---------------------------------------------------------------------------

function NodeDetail({
  node,
  onClose,
  onDelete,
}: {
  node: GraphNode;
  onClose: () => void;
  onDelete: (node: GraphNode) => Promise<void>;
}) {
  const [deleting, setDeleting] = useState(false);

  const handleDelete = async () => {
    setDeleting(true);
    await onDelete(node);
  };

  return (
    <div className="absolute bottom-0 left-0 right-0 border-t bg-sidebar p-3 text-sm">
      <div className="mb-2 flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span
              className="inline-block size-2 shrink-0 rounded-full"
              style={{ background: LABEL_COLORS[node.label] ?? DEFAULT_COLOR }}
            />
            <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              {node.label}
            </span>
          </div>
          <p className="mt-0.5 font-medium text-sidebar-foreground">{node.name}</p>
        </div>
        <Button variant="ghost" size="icon-sm" onClick={onClose}>
          <XIcon className="size-3.5" />
        </Button>
      </div>

      {node.content && (
        <p className="mb-2 text-xs text-muted-foreground leading-relaxed">
          {node.content}
        </p>
      )}

      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>
          Accessed {node.access_count ?? 0}×
          {node.created_at ? ` · ${new Date(node.created_at).toLocaleDateString()}` : ""}
        </span>
        <button
          type="button"
          onClick={handleDelete}
          disabled={deleting}
          className="flex items-center gap-1 rounded px-1.5 py-0.5 hover:bg-destructive/10 hover:text-destructive disabled:opacity-50"
        >
          <Trash2Icon className="size-3" />
          <span>Delete</span>
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Legend
// ---------------------------------------------------------------------------

function Legend() {
  const entries = [
    { label: "Concept", color: LABEL_COLORS.Concept },
    { label: "Project", color: LABEL_COLORS.Project },
    { label: "Note",    color: LABEL_COLORS.Note },
    { label: "Tag",     color: LABEL_COLORS.Tag },
  ];
  return (
    <div className="absolute left-2 top-2 flex flex-col gap-1 rounded-md border bg-sidebar/90 px-2 py-1.5 text-xs backdrop-blur-sm">
      {entries.map(({ label, color }) => (
        <div key={label} className="flex items-center gap-1.5">
          <span className="inline-block size-2 rounded-full" style={{ background: color }} />
          <span className="text-muted-foreground">{label}</span>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main export
// ---------------------------------------------------------------------------

export function GraphView({
  onDelete,
}: {
  onDelete: (label: string, name: string) => Promise<void>;
}) {
  const [data, setData] = useState<GraphData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [nodeMap, setNodeMap] = useState<Map<string, GraphNode>>(new Map());
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);

  useEffect(() => {
    setLoading(true);
    fetch("/api/graph")
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((d: GraphData) => setData(d))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  const handleGraphReady = useCallback((map: Map<string, GraphNode>) => {
    setNodeMap(map);
  }, []);

  const handleNodeClick = useCallback(
    (nodeId: string) => {
      if (!nodeId) {
        setSelectedNode(null);
        return;
      }
      const node = nodeMap.get(nodeId);
      setSelectedNode(node ?? null);
    },
    [nodeMap],
  );

  const handleDelete = useCallback(
    async (node: GraphNode) => {
      await onDelete(node.label, node.name);
      setSelectedNode(null);
      // Refresh graph data
      setLoading(true);
      fetch("/api/graph")
        .then((r) => r.json())
        .then((d: GraphData) => setData(d))
        .catch((e) => setError(e.message))
        .finally(() => setLoading(false));
    },
    [onDelete],
  );

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        Loading graph…
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-destructive">
        {error}
      </div>
    );
  }

  if (!data || data.nodes.length === 0) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        No entities yet. Start a conversation to populate the graph.
      </div>
    );
  }

  return (
    <div className="relative h-full w-full">
      <SigmaContainer
        style={{ height: "100%", width: "100%", background: "transparent" }}
        settings={{
          renderEdgeLabels: false,
          defaultEdgeColor: "#cbd5e1",
          defaultNodeColor: DEFAULT_COLOR,
          labelSize: 11,
          labelWeight: "500",
        }}
      >
        <GraphLoader data={data} onGraphReady={handleGraphReady} />
        <EventHandlers onNodeClick={handleNodeClick} />
      </SigmaContainer>

      <Legend />

      {selectedNode && (
        <NodeDetail
          node={selectedNode}
          onClose={() => setSelectedNode(null)}
          onDelete={handleDelete}
        />
      )}
    </div>
  );
}
