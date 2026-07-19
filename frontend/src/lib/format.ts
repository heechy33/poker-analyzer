/** Standard 6-max position order used in filters and dashboard tables. */
export const POSITIONS = ["UTG", "HJ", "CO", "BTN", "SB", "BB"] as const;

export type Position = (typeof POSITIONS)[number];

const POSITION_RANK = new Map(POSITIONS.map((p, i) => [p, i]));

export function sortByPosition<T extends { position: string }>(rows: T[]): T[] {
  return [...rows].sort((a, b) => {
    const aRank = POSITION_RANK.get(a.position as Position) ?? 99;
    const bRank = POSITION_RANK.get(b.position as Position) ?? 99;
    return aRank - bRank;
  });
}

export function formatPct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return `${value.toFixed(digits)}%`;
}

export function formatBb100(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(digits)}`;
}

export function formatDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatPot(
  pot: string | number,
  unit: "bb" | "chips" = "bb",
  stakeBb?: string | number,
): string {
  const potNum = typeof pot === "number" ? pot : Number.parseFloat(pot);
  if (!Number.isFinite(potNum)) return String(pot);

  if (unit === "chips") {
    return `₮${potNum.toFixed(2)}`;
  }

  const bb =
    stakeBb !== undefined && stakeBb !== null
      ? potNum / (typeof stakeBb === "number" ? stakeBb : Number.parseFloat(String(stakeBb)))
      : potNum;

  return `${Number.isFinite(bb) ? bb.toFixed(1) : potNum.toFixed(1)} bb`;
}

/** ISO date string for `since` query params (UTC midnight, N days ago). */
export function sinceDaysAgo(days: number): string {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() - days);
  d.setUTCHours(0, 0, 0, 0);
  return d.toISOString().slice(0, 10);
}

/** Title-case a snake_case leak tag for display. */
export function humanizeLeakTag(tag: string): string {
  return tag
    .split("_")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

/** SHA-256 hex digest of a File (for upload dedup / presign). */
export async function sha256Hex(file: File | Blob | ArrayBuffer): Promise<string> {
  const buffer =
    file instanceof ArrayBuffer
      ? file
      : await (file as Blob).arrayBuffer();
  const hash = await crypto.subtle.digest("SHA-256", buffer);
  return Array.from(new Uint8Array(hash))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}
