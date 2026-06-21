"use client";

import { useCallback, useState } from "react";

import {
  UploadDropzone,
  type UploadFileStatus,
  type UploadQueueItem,
} from "@/components/UploadDropzone";
import { completeUpload, fetchUpload, presignUpload } from "@/lib/api";
import { sha256Hex } from "@/lib/format";

const POLL_MS = 2000;
const MAX_POLL_MS = 5 * 60 * 1000;
const IS_DEV = process.env.NODE_ENV === "development";

export default function UploadPage() {
  const [items, setItems] = useState<UploadQueueItem[]>([]);
  const [devRawText, setDevRawText] = useState(IS_DEV);
  const [busy, setBusy] = useState(false);

  const updateItem = useCallback((id: string, patch: Partial<UploadQueueItem>) => {
    setItems((prev) => prev.map((it) => (it.id === id ? { ...it, ...patch } : it)));
  }, []);

  const pollUntilDone = useCallback(
    async (queueId: string, uploadId: string) => {
      const started = Date.now();
      while (Date.now() - started < MAX_POLL_MS) {
        await new Promise((r) => setTimeout(r, POLL_MS));
        const upload = await fetchUpload(uploadId);
        if (upload.status === "parsed") {
          updateItem(queueId, {
            status: "parsed",
            handCount: upload.hand_count,
            parseWarnings: upload.parse_warnings,
          });
          return;
        }
        if (upload.status === "error") {
          updateItem(queueId, {
            status: "error",
            errorMessage: upload.error_message ?? "Parse failed",
            parseWarnings: upload.parse_warnings,
          });
          return;
        }
        updateItem(queueId, { status: "parsing" });
      }
      updateItem(queueId, {
        status: "error",
        errorMessage: "Timed out after 5 minutes",
      });
    },
    [updateItem],
  );

  const processFile = useCallback(
    async (queueId: string, file: File) => {
      try {
        updateItem(queueId, { status: "uploading" });
        const hash = await sha256Hex(file);
        const presign = await presignUpload({
          filename: file.name,
          bytes: file.size,
          sha256: hash,
        });

        const uploadId = presign.id;
        updateItem(queueId, { uploadId });

        if (presign.deduplicated) {
          if (presign.status === "parsed") {
            updateItem(queueId, {
              status: "parsed",
              handCount: presign.hand_count,
              parseWarnings: presign.parse_warnings,
            });
            return;
          }
          if (presign.status === "parsing") {
            updateItem(queueId, { status: "parsing" });
            await pollUntilDone(queueId, uploadId);
            return;
          }
          // queued/error: prior attempt never finished — resume upload + complete below
          updateItem(queueId, { status: "deduplicated" });
        }

        if (IS_DEV && devRawText) {
          const text = await file.text();
          await completeUpload(uploadId, { raw_content: text });
        } else {
          if (!presign.signed_url) {
            throw new Error("No signed upload URL returned");
          }
          const putRes = await fetch(presign.signed_url, {
            method: "PUT",
            headers: { "Content-Type": "text/plain" },
            body: file,
          });
          if (!putRes.ok) {
            throw new Error(`Storage upload failed: ${putRes.status}`);
          }
          await completeUpload(uploadId);
        }

        updateItem(queueId, { status: "parsing" });
        await pollUntilDone(queueId, uploadId);
      } catch (err) {
        updateItem(queueId, {
          status: "error",
          errorMessage: err instanceof Error ? err.message : "Upload failed",
        });
      }
    },
    [devRawText, pollUntilDone, updateItem],
  );

  const onFilesSelected = useCallback(
    (files: File[]) => {
      const newItems: UploadQueueItem[] = files.map((file) => ({
        id: `${file.name}-${file.size}-${Date.now()}-${Math.random()}`,
        file,
        status: "queued" as UploadFileStatus,
      }));
      setItems((prev) => [...prev, ...newItems]);
      setBusy(true);
      void (async () => {
        for (const item of newItems) {
          await processFile(item.id, item.file);
        }
        setBusy(false);
      })();
    },
    [processFile],
  );

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Upload</h1>
        <p className="text-muted-foreground">Import CoinPoker hand history files</p>
      </div>

      {IS_DEV && (
        <label className="flex items-center gap-2 text-sm text-muted-foreground">
          <input
            type="checkbox"
            checked={devRawText}
            onChange={(e) => setDevRawText(e.target.checked)}
            className="h-4 w-4 rounded accent-emerald-500"
          />
          Send raw text to API (dev only — skips file storage, still saves hands to DB)
        </label>
      )}

      <UploadDropzone items={items} onFilesSelected={onFilesSelected} disabled={busy} />
    </div>
  );
}
