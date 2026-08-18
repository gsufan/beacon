import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import Toggle from "../src/components/Toggle";

describe("Toggle", () => {
  it("refleja checked=true en aria-pressed", () => {
    render(<Toggle checked onChange={() => {}} label="watcher" />);
    expect(screen.getByRole("button", { name: "watcher" })).toHaveAttribute("aria-pressed", "true");
  });

  it("refleja checked=false en aria-pressed", () => {
    render(<Toggle checked={false} onChange={() => {}} label="watcher" />);
    expect(screen.getByRole("button", { name: "watcher" })).toHaveAttribute("aria-pressed", "false");
  });

  it("al hacer click llama a onChange con el valor invertido", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Toggle checked={false} onChange={onChange} label="watcher" />);

    await user.click(screen.getByRole("button", { name: "watcher" }));

    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it("estando en true, el click llama a onChange con false", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Toggle checked onChange={onChange} label="watcher" />);

    await user.click(screen.getByRole("button", { name: "watcher" }));

    expect(onChange).toHaveBeenCalledWith(false);
  });
});
