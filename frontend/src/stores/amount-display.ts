import { create } from "zustand";

export type AmountUnit = "bb" | "chips";

interface AmountDisplayState {
  unit: AmountUnit;
  setUnit: (unit: AmountUnit) => void;
}

function loadUnit(): AmountUnit {
  if (typeof window === "undefined") return "bb";
  const stored = localStorage.getItem("amount_display:unit");
  if (stored === "chips") return "chips";
  return "bb";
}

function persistUnit(unit: AmountUnit) {
  if (typeof window !== "undefined") {
    localStorage.setItem("amount_display:unit", unit);
  }
}

export const useAmountDisplay = create<AmountDisplayState>((set) => ({
  unit: loadUnit(),
  setUnit: (unit) => {
    persistUnit(unit);
    set({ unit });
  },
}));