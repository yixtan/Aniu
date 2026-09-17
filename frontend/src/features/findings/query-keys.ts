export const findingKeys = {
  all: ["open-findings"] as const,
  list: () => [...findingKeys.all, "list"] as const,
};
