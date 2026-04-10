"use client";

import { useEffect, useState } from "react";
import { RefreshCwIcon, ChevronDownIcon, Trash2Icon } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";

interface GraphNode {
  label: string;
  name: string;
  content?: string;
}

interface GroupedNodes {
  [label: string]: GraphNode[];
}

const LABEL_ORDER = ["Concept", "Project", "Note", "Tag"];

interface GraphPanelProps {
  threadId: string;
}

export function GraphPanel({ threadId }: GraphPanelProps) {
  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchNodes = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/nodes");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setNodes(Array.isArray(data.nodes) ? data.nodes : []);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  };

  // Refresh whenever the active thread changes (new entities may have been extracted)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { fetchNodes(); }, [threadId]);

  const grouped: GroupedNodes = {};
  for (const node of nodes) {
    const label = node.label ?? "Other";
    grouped[label] = grouped[label] ?? [];
    grouped[label].push(node);
  }

  const labels = [
    ...LABEL_ORDER.filter((l) => grouped[l]),
    ...Object.keys(grouped).filter((l) => !LABEL_ORDER.includes(l)),
  ];

  return (
    <aside className="flex h-dvh w-80 shrink-0 flex-col border-l bg-sidebar">
      <div className="flex items-center justify-between border-b px-3 py-3">
        <span className="text-sm font-semibold text-sidebar-foreground">
          Knowledge Graph
        </span>
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={fetchNodes}
          disabled={loading}
          title="Refresh"
        >
          <RefreshCwIcon className={cn(loading && "animate-spin")} />
        </Button>
      </div>

      <div className="flex-1 overflow-y-auto py-2">
        {error && (
          <p className="px-3 py-2 text-xs text-destructive">{error}</p>
        )}

        {!error && labels.length === 0 && !loading && (
          <p className="px-3 py-4 text-center text-xs text-muted-foreground">
            No entities yet. Start a conversation to populate the graph.
          </p>
        )}

        {labels.map((label) => (
          <NodeGroup
            key={label}
            label={label}
            nodes={grouped[label]}
            onDelete={async (name) => {
              await fetch("/api/nodes", {
                method: "DELETE",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ label, name }),
              });
              fetchNodes();
            }}
          />
        ))}
      </div>
    </aside>
  );
}

function NodeGroup({
  label,
  nodes,
  onDelete,
}: {
  label: string;
  nodes: GraphNode[];
  onDelete: (name: string) => Promise<void>;
}) {
  const [open, setOpen] = useState(true);

  return (
    <Collapsible open={open} onOpenChange={setOpen} className="mb-1">
      <CollapsibleTrigger asChild>
        <button
          type="button"
          className="flex w-full items-center justify-between px-3 py-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground hover:text-foreground transition-colors"
        >
          <span>{label}</span>
          <span className="flex items-center gap-1">
            <span className="tabular-nums">{nodes.length}</span>
            <ChevronDownIcon
              className={cn(
                "size-3.5 transition-transform",
                open && "rotate-180",
              )}
            />
          </span>
        </button>
      </CollapsibleTrigger>

      <CollapsibleContent>
        {nodes.map((node) => (
          <NodeRow
            key={`${node.label}-${node.name}`}
            node={node}
            onDelete={onDelete}
          />
        ))}
      </CollapsibleContent>
    </Collapsible>
  );
}

function NodeRow({
  node,
  onDelete,
}: {
  node: GraphNode;
  onDelete: (name: string) => Promise<void>;
}) {
  const [deleting, setDeleting] = useState(false);

  const handleDelete = async (e: React.MouseEvent) => {
    e.stopPropagation();
    setDeleting(true);
    await onDelete(node.name);
    // Parent re-fetches so this component will unmount; no need to reset
  };

  return (
    <div
      className="group mx-2 mb-0.5 flex items-start gap-1 rounded px-2 py-1.5 text-sm hover:bg-accent/50 transition-colors"
      title={node.content ?? node.name}
    >
      <div className="min-w-0 flex-1">
        <span className="truncate block text-sidebar-foreground">
          {node.name}
        </span>
        {node.content && (
          <span className="truncate block text-xs text-muted-foreground">
            {node.content}
          </span>
        )}
      </div>
      <button
        type="button"
        onClick={handleDelete}
        disabled={deleting}
        className="mt-0.5 shrink-0 rounded p-0.5 opacity-0 transition-opacity group-hover:opacity-100 hover:bg-destructive/10 hover:text-destructive disabled:opacity-50"
        title={`Delete ${node.name} and any orphaned connections`}
      >
        <Trash2Icon className="size-3.5" />
      </button>
    </div>
  );
}
