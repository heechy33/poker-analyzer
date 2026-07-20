import { describe, expect, it } from "vitest";

import { formatTableFormat, isTableFormat, TABLE_FORMATS } from "./table-formats";

describe("table formats", () => {
  it("keeps each exact table configuration separate", () => {
    expect(TABLE_FORMATS).toEqual(["hu_2max", "6max", "9max"]);
  });

  it.each([
    ["hu_2max", "2-max"],
    ["6max", "6-max"],
    ["9max", "9-max"],
  ] as const)("formats %s as %s", (tableFormat, label) => {
    expect(formatTableFormat(tableFormat)).toBe(label);
  });

  it("does not accept postflop participation labels as table formats", () => {
    expect(isTableFormat("multiway")).toBe(false);
    expect(isTableFormat("heads_up")).toBe(false);
  });
});
