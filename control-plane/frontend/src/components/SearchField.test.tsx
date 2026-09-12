import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SearchField } from "./SearchField";

afterEach(() => {
  cleanup();
});

describe("SearchField", () => {
  it("offers a focused clear action only when the query is non-empty", () => {
    const onChange = vi.fn();
    render(
      <SearchField
        id="search"
        label="Search"
        value="agent"
        onChange={onChange}
        clearLabel="Clear search"
      />,
    );

    const input = screen.getByLabelText("Search");
    fireEvent.click(screen.getByRole("button", { name: "Clear search" }));

    expect(onChange).toHaveBeenCalledWith("");
    expect(document.activeElement).toBe(input);
  });

  it("does not add a false clear affordance for an empty query", () => {
    render(<SearchField id="search" label="Search" value="" onChange={vi.fn()} clearLabel="Clear search" />);

    expect(screen.queryByRole("button", { name: "Clear search" })).not.toBeInTheDocument();
  });
});
