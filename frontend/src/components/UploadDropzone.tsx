"use client";

import { FileText, Upload } from "lucide-react";
import { useCallback, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export type UploadFileStatus =
  | "queued"
  | "uploading"
  | "parsing"
  | "parsed"
  | "error"
  | "deduplicated";

export interface UploadQueueItem {
  id: string;
  file: File;
  status: UploadFileStatus;
  uploadId?: string;
  handCount?: number | null;
  errorMessage?: string | null;
  parseWarnings?: string | null;
}

export function UploadDropzone({
  items,
  onFilesSelected,
  disabled,
}: {
  items: UploadQueueItem[];
  onFilesSelected: (files: File[]) => void;
  disabled?: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);

  const handleFiles = useCallback(
    (fileList: FileList | null) => {
      if (!fileList || disabled) return;
      const txtFiles = Array.from(fileList).filter(
        (f) => f.name.endsWith(".txt") || f.type === "text/plain",
      );
      if (txtFiles.length > 0) {
        onFilesSelected(txtFiles);
      }
    },
    [disabled, onFilesSelected],
  );

  return (
    <div className="space-y-4">
      {/* Dev tip: sample hand histories live in backend/tests/fixtures/coinpoker/*.txt */}
      <div
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") inputRef.current?.click();
        }}
        onDragOver={(e) => {
          e.preventDefault();
          if (!disabled) setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          handleFiles(e.dataTransfer.files);
        }}
        onClick={() => !disabled && inputRef.current?.click()}
        className={cn(
          "flex cursor-pointer flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed px-6 py-12 transition-colors",
          dragOver
            ? "border-emerald-500 bg-emerald-500/5"
            : "border-zinc-700 bg-zinc-950/30 hover:border-zinc-600",
          disabled && "pointer-events-none opacity-50",
        )}
      >
        <Upload className="h-10 w-10 text-emerald-400/80" />
        <div className="text-center">
          <p className="font-medium">Drop CoinPoker hand history files here</p>
          <p className="mt-1 text-sm text-muted-foreground">.txt files only</p>
        </div>
        <Button type="button" variant="outline" size="sm" disabled={disabled}>
          Browse files
        </Button>
        <input
          ref={inputRef}
          type="file"
          accept=".txt,text/plain"
          multiple
          className="hidden"
          onChange={(e) => {
            handleFiles(e.target.files);
            e.target.value = "";
          }}
        />
      </div>

      <p className="text-xs text-muted-foreground">
        Sample files: <code className="text-emerald-400/90">backend/tests/fixtures/coinpoker/*.txt</code>
      </p>

      {items.length > 0 && (
        <ul className="space-y-2">
          {items.map((item) => (
            <li
              key={item.id}
              className="flex items-center gap-3 rounded-lg border border-border bg-card px-4 py-3"
            >
              <FileText className="h-5 w-5 shrink-0 text-emerald-400/70" />
              <div className="min-w-0 flex-1">
                <p className="truncate font-medium">{item.file.name}</p>
                <p className="text-xs text-muted-foreground">
                  {statusLabel(item)}
                  {statusDetail(item) ? ` · ${statusDetail(item)}` : null}
                </p>
              </div>
              <StatusBadge item={item} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function isDuplicateOnly(item: UploadQueueItem): boolean {
  return Boolean(
    item.parseWarnings?.includes("0 hands") ||
      item.parseWarnings?.toLowerCase().includes("duplicate"),
  );
}

function statusLabel(item: UploadQueueItem): string {
  switch (item.status) {
    case "queued":
      return "Queued";
    case "uploading":
      return "Uploading…";
    case "parsing":
      return "Importing…";
    case "parsed":
      return isDuplicateOnly(item) ? "Already imported" : "Imported";
    case "error":
      return "Error";
    case "deduplicated":
      return "Resuming prior upload";
    default:
      return item.status;
  }
}

function statusDetail(item: UploadQueueItem): string | null {
  if (item.status === "parsed") {
    if (item.parseWarnings) return item.parseWarnings;
    if (item.handCount != null) {
      return `${item.handCount} hand${item.handCount === 1 ? "" : "s"} saved to your library`;
    }
    return "Hands saved to your library";
  }
  if (item.status === "error" && item.errorMessage) {
    return item.errorMessage;
  }
  return null;
}

function StatusBadge({ item }: { item: UploadQueueItem }) {
  const styles: Record<UploadFileStatus, string> = {
    queued: "bg-zinc-800 text-zinc-300",
    uploading: "bg-amber-500/15 text-amber-400",
    parsing: "bg-sky-500/15 text-sky-400",
    parsed: "bg-emerald-500/15 text-emerald-400",
    error: "bg-red-500/15 text-red-400",
    deduplicated: "bg-violet-500/15 text-violet-400",
  };
  const label =
    item.status === "parsed"
      ? isDuplicateOnly(item)
        ? "Duplicate"
        : "Imported"
      : item.status === "parsing"
        ? "Importing"
        : item.status;
  return (
    <span
      className={cn(
        "shrink-0 rounded-full px-2.5 py-0.5 text-xs font-medium capitalize",
        item.status === "parsed" && isDuplicateOnly(item)
          ? "bg-violet-500/15 text-violet-400"
          : styles[item.status],
      )}
    >
      {label}
    </span>
  );
}
