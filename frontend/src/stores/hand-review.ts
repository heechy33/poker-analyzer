import { create } from "zustand";

import type { Street } from "@/types/api";

export type HandReviewTab = "replayer" | "solver" | "coach";

interface HandReviewState {
  handId: string | null;
  activeTab: HandReviewTab;
  selectedStreet: Street;
  openHandReview: (id: string, tab?: HandReviewTab) => void;
  closeHandReview: () => void;
  setTab: (tab: HandReviewTab) => void;
  setStreet: (street: Street) => void;
}

export const useHandReviewStore = create<HandReviewState>((set) => ({
  handId: null,
  activeTab: "replayer",
  selectedStreet: "flop",
  openHandReview: (id, tab = "replayer") => set({ handId: id, activeTab: tab }),
  closeHandReview: () => set({ handId: null, activeTab: "replayer", selectedStreet: "flop" }),
  setTab: (tab) => set({ activeTab: tab }),
  setStreet: (street) => set({ selectedStreet: street }),
}));
