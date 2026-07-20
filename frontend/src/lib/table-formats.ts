import type { TableFormat } from "@/types/api";

export const TABLE_FORMATS = ["hu_2max", "6max", "9max"] as const satisfies readonly TableFormat[];

export function isTableFormat(value: string): value is TableFormat {
  return TABLE_FORMATS.some((tableFormat) => tableFormat === value);
}

export function formatTableFormat(tableFormat: TableFormat): string {
  return tableFormat === "hu_2max" ? "2-max" : tableFormat.replace("max", "-max");
}
