export const accountKeys = {
  all: ["account"] as const,
  dashboard: () => [...accountKeys.all, "dashboard"] as const,
};

export const awayModeKeys = {
  all: ["away-mode"] as const,
  state: () => [...awayModeKeys.all, "state"] as const,
};
