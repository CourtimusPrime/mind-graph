"use client";

import { PlusIcon, Trash2Icon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { StoredThread } from "@/lib/thread-store";

interface ThreadListProps {
  threads: StoredThread[];
  activeId: string;
  onNew: () => void;
  onSwitch: (id: string) => void;
  onDelete: (id: string) => void;
}

function relativeDate(ts: number): string {
  const diff = Date.now() - ts;
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

export function ThreadList({
  threads,
  activeId,
  onNew,
  onSwitch,
  onDelete,
}: ThreadListProps) {
  return (
    <aside className="flex h-dvh w-64 shrink-0 flex-col border-r bg-sidebar">
      <div className="flex items-center justify-between border-b px-3 py-3">
        <span className="text-sm font-semibold text-sidebar-foreground">
          Chats
        </span>
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={onNew}
          title="New chat"
        >
          <PlusIcon />
        </Button>
      </div>

      <nav className="flex-1 overflow-y-auto py-1">
        {threads.map((thread) => (
          <div
            key={thread.id}
            className={cn(
              "group relative flex cursor-pointer select-none flex-col rounded-md mx-1 px-3 py-2 text-sm transition-colors",
              thread.id === activeId
                ? "bg-accent text-accent-foreground"
                : "text-sidebar-foreground hover:bg-accent/50",
            )}
            onClick={() => onSwitch(thread.id)}
          >
            <span className="truncate font-medium" suppressHydrationWarning>{thread.title}</span>
            <span className="text-xs text-muted-foreground" suppressHydrationWarning>
              {relativeDate(thread.updatedAt)}
            </span>

            {/* Delete button — visible on hover */}
            <button
              type="button"
              className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 opacity-0 transition-opacity hover:bg-destructive/20 hover:text-destructive group-hover:opacity-100"
              onClick={(e) => {
                e.stopPropagation();
                if (window.confirm(`Delete "${thread.title}"?`)) {
                  onDelete(thread.id);
                }
              }}
              title="Delete chat"
            >
              <Trash2Icon className="size-3.5" />
            </button>
          </div>
        ))}
      </nav>
    </aside>
  );
}
