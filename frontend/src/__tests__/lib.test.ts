import { describe, expect, it } from "vitest";
import { ApiError, api } from "../api/client";
import { fmtPoints, fmtSpread, parseSpreadInput, pickLabel, teamSpread } from "../lib/format";
import { replacePick, togglePick } from "../lib/picks";
import { TEAMS, teamInfo, teamInk } from "../lib/teams";
import { vi } from "vitest";

describe("format", () => {
  it("formats spreads like the league app", () => {
    expect(fmtSpread(-3.5)).toBe("-3.5");
    expect(fmtSpread(11.5)).toBe("+11.5");
    expect(fmtSpread(-3)).toBe("-3");
    expect(fmtSpread(0)).toBe("PK");
    expect(fmtSpread(null)).toBe("—");
    expect(pickLabel("IND", "WAS", -3.5, false)).toBe("IND -3.5 @ WAS");
    expect(pickLabel("CHI", "NYJ", 0, true)).toBe("CHI PK vs NYJ");
    expect(teamSpread(-7, false)).toBe(7);
    expect(teamSpread(0, false)).toBe(0);
    expect(fmtPoints(8)).toBe("+8");
    expect(fmtPoints(-3.5)).toBe("-3.5");
    expect(fmtPoints(4, 1)).toBe("+4.0");
  });

  it("parses typed spreads", () => {
    expect(parseSpreadInput("+7")).toBe(7);
    expect(parseSpreadInput(" -3.5 ")).toBe(-3.5);
    expect(parseSpreadInput("pk")).toBe("PK");
    expect(parseSpreadInput("0")).toBe("PK");
    expect(parseSpreadInput("x")).toBeNull();
  });
});

describe("pick set toggling", () => {
  const g = { away: "IND", home: "WAS" };
  it("adds, removes, switches and asks when full", () => {
    expect(togglePick(["A"], "IND", g, 5)).toEqual({ kind: "set", teams: ["A", "IND"], action: "add" });
    expect(togglePick(["A", "WAS"], "WAS", g, 5)).toEqual({ kind: "set", teams: ["A"], action: "remove" });
    expect(togglePick(["A", "WAS", "B"], "IND", g, 5)).toEqual({ kind: "set", teams: ["A", "IND", "B"], action: "switch", from: "WAS" });
    expect(togglePick(["A", "B", "C", "D", "E"], "IND", g, 5)).toEqual({ kind: "choose", team: "IND", current: ["A", "B", "C", "D", "E"] });
    expect(togglePick(["A", "B", "C", "D", "WAS"], "IND", g, 5).kind).toBe("set");
    expect(replacePick(["A", "B", "C"], "B", "X")).toEqual(["A", "X", "C"]);
  });
});

describe("teams", () => {
  it("has all 32 teams and resolves app aliases", () => {
    expect(Object.keys(TEAMS)).toHaveLength(32);
    expect(teamInfo("LAR").name).toBe("Los Angeles Rams");
    expect(teamInfo("WSH").name).toBe("Washington Commanders");
    expect(teamInk("PIT")).toBe("#ffffff");
    expect(teamInk("NO")).toBe("#0b0f19");
  });
});

describe("api client", () => {
  it("surfaces {detail} as the error message", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "unknown team 'XYZ'" }), { status: 400 })));
    await expect(api.setPicks(2026, 4, ["XYZ"])).rejects.toMatchObject({ status: 400, message: "unknown team 'XYZ'" });
  });

  it("reports a down server clearly", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
    const err = await api.meta().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).message).toMatch(/Can't reach the Cover 5 server/);
  });

  it("encodes override kinds as repeated query params", async () => {
    const f = vi.fn(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", f);
    await api.clearOverride(2026, 4, "ARI", ["line", "score"]);
    expect(f).toHaveBeenCalledWith("/api/week/2026/4/overrides/ARI?kind=line&kind=score", expect.objectContaining({ method: "DELETE" }));
  });
});
