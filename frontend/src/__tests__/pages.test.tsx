import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Settings } from "../api/types";
import { mockServer } from "../test/mockApi";
import { renderApp } from "../test/render";

const backtest = {
  dataset: "nflverse 2007-2025",
  games_used: 4967,
  seasons: [2007, 2025],
  strategies: {
    RANDOM: { weeks: 330, mean_week: -0.027, p_week_positive: 0.4965, z_vs_random: null },
    "MOVEMENT-TOP5": { weeks: 330, mean_week: 10.73, p_week_positive: 0.6521, z_vs_random: 6.48 },
  },
  oracle_check: { slope_se: 0.105, buckets: [{ bucket: "0", n: 10830, cover_rate: 0.511 }, { bucket: "1", n: 900, cover_rate: 0.55 }] },
};

describe("backtest page", () => {
  it("renders strategies and nested arrays as tables", async () => {
    const server = mockServer();
    server.on("GET /api/backtest", () => backtest);
    renderApp("/backtest");
    const strategies = await screen.findByRole("heading", { name: "Strategies" });
    const table = within(strategies.closest("section")!).getByRole("table");
    expect(within(table).getByRole("rowheader", { name: "MOVEMENT-TOP5" })).toBeInTheDocument();
    expect(within(table).getByText("65.2%")).toBeInTheDocument();
    expect(within(table).getByText("10.73")).toBeInTheDocument();
    expect(screen.getByText("nflverse 2007-2025")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Buckets" })).toBeInTheDocument();
    expect(screen.getByText("10,830")).toBeInTheDocument();
    expect(screen.getByText("2007, 2025")).toBeInTheDocument();
  });

  it("explains how to generate results on 404", async () => {
    const server = mockServer();
    server.on("GET /api/backtest", () => ({ status: 404, body: { detail: "no backtest results" } }));
    renderApp("/backtest");
    expect(await screen.findByText("No backtest results yet")).toBeInTheDocument();
    expect(screen.getByText("python -m cover5.backtest_movement")).toBeInTheDocument();
  });
});

const settings: Settings = {
  provider: "oddsapi",
  odds_api_key_set: true,
  odds_api_key_hint: "…1a2b",
  ntfy_topic: "my-topic",
  ntfy_server: "https://ntfy.sh",
  webhook_url: "",
  swap_margin: 0.5,
  flip_margin: 0.5,
  scheduler: { enabled: false, interval_minutes: 60, sunday_interval_minutes: 15, auto_init_wednesday: true },
};

describe("settings page", () => {
  it("saves only changed fields", async () => {
    const server = mockServer();
    server.on("GET /api/settings", () => settings);
    server.on("PUT /api/settings", (c) => {
      const b = c.body as Partial<Settings>;
      return { ...settings, ...b, scheduler: { ...settings.scheduler, ...b.scheduler } };
    });
    const { user } = renderApp("/settings");
    await screen.findByRole("heading", { name: "Settings" });
    expect(screen.getByText(/A key is saved \(…1a2b\)/)).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Provider"), "espn");
    const topic = screen.getByLabelText("ntfy topic");
    await user.clear(topic);
    await user.type(topic, "new-topic");
    await user.click(screen.getByLabelText(/Fetch lines and alert automatically/));
    const sun = screen.getByLabelText("Sunday interval (minutes)");
    await user.clear(sun);
    await user.type(sun, "10");
    await user.type(screen.getByLabelText("Odds API key"), "secret123");
    await user.click(screen.getByRole("button", { name: /Save settings/ }));
    await waitFor(() => expect(server.mutations()).toHaveLength(1));
    expect(server.mutations()[0]!.body).toEqual({
      provider: "espn",
      ntfy_topic: "new-topic",
      scheduler: { enabled: true, sunday_interval_minutes: 10 },
      odds_api_key: "secret123",
    });
    expect(await screen.findByText("Settings saved.")).toBeInTheDocument();
  });

  it("clears the key and runs the scheduler", async () => {
    const server = mockServer();
    server.on("GET /api/settings", () => settings);
    server.on("PUT /api/settings", () => ({ ...settings, odds_api_key_set: false, odds_api_key_hint: null }));
    server.on("POST /api/scheduler/run", () => ({ ran: false, message: "Nothing due: next update at 5:30 PM" }));
    const { user } = renderApp("/settings");
    await user.click(await screen.findByRole("button", { name: "Clear key" }));
    await waitFor(() => expect(server.mutations()).toHaveLength(1));
    expect(server.mutations()[0]!.body).toEqual({ odds_api_key: "" });
    expect(await screen.findByText("No key saved.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Run scheduler now/ }));
    expect(await screen.findByText("Nothing due: next update at 5:30 PM")).toBeInTheDocument();
  });
});
