import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { clone, mockServer, samples } from "../test/mockApi";
import { renderApp } from "../test/render";

describe("history page", () => {
  it("renders records with pick chips and saves an inline edit", async () => {
    const server = mockServer();
    server.on("PUT /api/history/2026/4", (c) => ({ ...clone(samples.weekRecord), ...(c.body as object) }));
    const { user } = renderApp("/history?season=2026");
    const table = await screen.findByRole("table");
    const row = within(table).getAllByRole("row")[1]!;
    expect(within(row).getByRole("link", { name: "Wk 4" })).toHaveAttribute("href", "/week/2026/4");
    expect(within(row).getByText("ARI")).toBeInTheDocument();
    expect(within(row).getAllByText("+8").length).toBeGreaterThan(0);
    expect(within(row).getByText("+11.5")).toBeInTheDocument();

    await user.click(within(row).getByRole("button", { name: "Edit App points, week 4" }));
    const input = within(row).getByRole("textbox", { name: "App points, week 4" });
    await user.type(input, "12.5{Enter}");
    await waitFor(() => expect(server.mutations()).toHaveLength(1));
    expect(server.mutations()[0]).toMatchObject({ method: "PUT", path: "/api/history/2026/4", body: { app_points: 12.5 } });
    expect(await within(row).findByRole("button", { name: "Edit App points, week 4" })).toHaveTextContent("+12.5");

    await user.click(within(row).getByRole("button", { name: "Edit Notes, week 4" }));
    const notes = within(row).getByRole("textbox", { name: "Notes, week 4" });
    await user.clear(notes);
    await user.type(notes, "late swap{Enter}");
    await waitFor(() => expect(server.mutations()).toHaveLength(2));
    expect(server.mutations()[1]!.body).toEqual({ notes: "late swap" });

    // Clearing a number sends null.
    await user.click(within(row).getByRole("button", { name: "Edit Week rank, week 4" }));
    await user.clear(within(row).getByRole("textbox", { name: "Week rank, week 4" }));
    await user.keyboard("{Enter}");
    await waitFor(() => expect(server.mutations()).toHaveLength(3));
    expect(server.mutations()[2]!.body).toEqual({ week_rank: null });
  });

  it("rejects a non-integer rank without sending", async () => {
    const server = mockServer();
    const { user } = renderApp("/history?season=2026");
    const table = await screen.findByRole("table");
    await user.click(within(table).getByRole("button", { name: "Edit Overall rank, week 4" }));
    await user.type(within(table).getByRole("textbox", { name: "Overall rank, week 4" }), "2.5{Enter}");
    expect(within(table).getByText("Whole number")).toBeInTheDocument();
    expect(server.mutations()).toHaveLength(0);
    await user.keyboard("{Escape}");
    expect(within(table).getByRole("button", { name: "Edit Overall rank, week 4" })).toHaveTextContent("2");
  });

  it("refreshes records and links the export", async () => {
    const server = mockServer();
    server.on("POST /api/history/refresh", () => ({ updated: 3, weeks: [] }));
    const { user } = renderApp("/history?season=2026");
    await screen.findByRole("table");
    expect(screen.getByRole("link", { name: "Export JSON" })).toHaveAttribute("href", "/api/history/export");
    await user.click(screen.getByRole("button", { name: /Refresh records/ }));
    expect(await screen.findByText("Refreshed 3 week records.")).toBeInTheDocument();
    expect(server.mutations()[0]).toMatchObject({ path: "/api/history/refresh", body: { season: 2026 } });
  });
});
