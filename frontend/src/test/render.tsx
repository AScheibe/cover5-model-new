import { render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { AppRoutes, NavBar, Providers } from "../App";

export function renderApp(route: string) {
  const user = userEvent.setup();
  const utils = render(
    <MemoryRouter initialEntries={[route]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <Providers pollMs={0}>
        <NavBar />
        <AppRoutes />
      </Providers>
    </MemoryRouter>,
  );
  return { user, ...utils };
}
