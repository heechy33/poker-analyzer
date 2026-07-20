export const OFFLINE_STUDY_TITLE = "Offline study only";

export const OFFLINE_STUDY_MESSAGE =
  "Use Poker Analyzer only after your CoinPoker session, with the CoinPoker client fully closed.";

export const CLIENT_CLOSED_CONFIRMATION = "I confirm the CoinPoker client is closed.";

export function canStartStudyAction(clientClosed: boolean): boolean {
  return clientClosed;
}
