export const accountKeys = {
  all: ["account"] as const,
  dashboard: () => [...accountKeys.all, "dashboard"] as const,
  // Under "account" so the refetch that follows every account refresh redraws
  // today's point with it.
  returnCurve: () => [...accountKeys.all, "return-curve"] as const,
};

export const awayModeKeys = {
  all: ["away-mode"] as const,
  state: () => [...awayModeKeys.all, "state"] as const,
};
