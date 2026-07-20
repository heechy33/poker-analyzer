import { ShieldCheck } from "lucide-react";

import {
  CLIENT_CLOSED_CONFIRMATION,
  OFFLINE_STUDY_MESSAGE,
  OFFLINE_STUDY_TITLE,
} from "@/lib/offline-study";
import { cn } from "@/lib/utils";

interface OfflineStudyNoticeProps {
  confirmed?: boolean;
  disabled?: boolean;
  onConfirmedChange?: (confirmed: boolean) => void;
  requireConfirmation?: boolean;
  compact?: boolean;
}

export function OfflineStudyNotice({
  confirmed = false,
  disabled = false,
  onConfirmedChange,
  requireConfirmation = false,
  compact = false,
}: OfflineStudyNoticeProps) {
  return (
    <div
      className={cn(
        "rounded-lg border border-amber-500/40 bg-amber-500/10 text-amber-100",
        compact ? "px-3 py-2" : "px-4 py-3",
      )}
      role="note"
    >
      <div className="flex items-start gap-3">
        <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-amber-300" aria-hidden="true" />
        <div className="space-y-2">
          <div>
            <p className="text-sm font-semibold">{OFFLINE_STUDY_TITLE}</p>
            <p className="text-xs leading-5 text-amber-100/80">{OFFLINE_STUDY_MESSAGE}</p>
          </div>
          {requireConfirmation && (
            <label className="flex items-start gap-2 text-sm font-medium">
              <input
                type="checkbox"
                checked={confirmed}
                disabled={disabled}
                onChange={(event) => onConfirmedChange?.(event.target.checked)}
                className="mt-0.5 h-4 w-4 rounded accent-amber-400"
              />
              <span>{CLIENT_CLOSED_CONFIRMATION}</span>
            </label>
          )}
        </div>
      </div>
    </div>
  );
}
