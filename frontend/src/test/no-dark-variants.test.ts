import { describe, expect, it } from "vitest";

/**
 * This app has one palette and no way to switch it: `:root` alone, no toggle,
 * nothing that sets a `dark` class. Tailwind's `dark:` variant follows the
 * operating system regardless of that, so a stray `dark:` repaints whatever it
 * is on — and nothing else — the moment someone's Mac turns dark at sunset.
 *
 * Eight of them reached the build before anyone opened the page in dark
 * appearance. This fails the moment a ninth arrives; delete it when the app
 * grows a real theme.
 *
 * Read through Vite rather than `node:fs`, which the app's tsconfig has no
 * types for — a test that breaks `tsc` is not a test anyone keeps.
 */

// Tailwind classes only ever live in the components, so this looks at the
// TypeScript sources. A stylesheet would spell dark mode as a selector, not as
// a `dark:` class, and there is none to find.
const SOURCES = Object.fromEntries(
  Object.entries(
    import.meta.glob("../**/*.{ts,tsx}", {
      query: "?raw",
      import: "default",
      eager: true,
    }),
  ).filter((entry): entry is [string, string] => typeof entry[1] === "string"),
);

const DARK_VARIANT = /(?:^|[\s"'`{])dark:[a-z[]/;

function isProse(line: string) {
  const trimmed = line.trimStart();
  return trimmed.startsWith("*") || trimmed.startsWith("//");
}

describe("styling", () => {
  it("has no dark-mode variants while the app has no dark mode", () => {
    const offenders = Object.entries(SOURCES)
      .filter(([, source]) =>
        source
          .split("\n")
          .some((line) => !isProse(line) && DARK_VARIANT.test(line)),
      )
      .map(([path]) => path);

    expect(offenders).toEqual([]);
  });

  it("actually looks at the source it is guarding", () => {
    // Without this, a glob that silently matched nothing would pass forever.
    expect(Object.keys(SOURCES).length).toBeGreaterThan(50);
  });
});
