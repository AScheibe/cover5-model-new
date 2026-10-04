import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, expect, vi } from "vitest";

// recharts' ResponsiveContainer needs ResizeObserver, which jsdom lacks.
class RO {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as unknown as { ResizeObserver: typeof RO }).ResizeObserver ??= RO;

// The app must not log errors or React warnings: fail any test that does.
let errorSpy: ReturnType<typeof vi.spyOn>;
let warnSpy: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  errorSpy = vi.spyOn(console, "error");
  warnSpy = vi.spyOn(console, "warn");
});

afterEach(() => {
  cleanup();
  const errors = errorSpy.mock.calls.map((c) => c.map(String).join(" "));
  // recharts measures 0x0 containers under jsdom; that's the test DOM, not the app.
  const warns = warnSpy.mock.calls.map((c) => c.map(String).join(" ")).filter((w) => !/width\(0\) and height\(0\)/.test(w));
  errorSpy.mockRestore();
  warnSpy.mockRestore();
  expect(errors, "console.error calls").toEqual([]);
  expect(warns, "console.warn calls").toEqual([]);
});
