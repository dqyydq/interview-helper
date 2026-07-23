import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { CommandPalette } from "./CommandPalette";

function LocationProbe() {
  const location = useLocation();
  return <output aria-label="当前位置">{location.pathname}</output>;
}

function renderPalette() {
  return render(
    <MemoryRouter initialEntries={["/interviews"]}>
      <CommandPalette />
      <Routes>
        <Route path="*" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("CommandPalette", () => {
  it("opens from the keyboard, filters commands and navigates", () => {
    renderPalette();

    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    expect(screen.getByRole("dialog", { name: "COMMAND INDEX" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("搜索页面"), { target: { value: "证据" } });
    expect(screen.getByRole("button", { name: /评估报告/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /评估报告/ }));

    expect(screen.getByLabelText("当前位置")).toHaveTextContent("/reports");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
