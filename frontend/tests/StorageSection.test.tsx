// Storage section of Settings: sizes per project and selective deletion with confirmation.
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import StorageSection, { formatBytes } from "../src/components/StorageSection";
import { LanguageProvider } from "../src/components/LanguageSelector";

let calls: { method: string; path: string; body?: unknown }[];
let storage: Record<string, unknown>;

beforeEach(() => {
  calls = [];
  storage = {
    total_bytes: 3_000_000 + 700,
    projects: [
      { id: "clonado", name: "Clonado", source_type: "git", clone_bytes: 2_000_000, index_bytes: 1_000_000, docs_bytes: 0, total_bytes: 3_000_000, managed_clone: true },
      { id: "local", name: "Local", source_type: "local", clone_bytes: 0, index_bytes: 700, docs_bytes: 0, total_bytes: 700, managed_clone: false },
    ],
  };
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    calls.push({ method, path: input, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const out = input === "/system/storage" ? storage : { freed_bytes: 2_000_000 };
    return new Response(JSON.stringify(out), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
});
afterEach(() => vi.unstubAllGlobals());

const open = () => render(<LanguageProvider><StorageSection /></LanguageProvider>);

describe("almacenamiento", () => {
  it("formatea tamaños", () => {
    expect(formatBytes(500)).toBe("500 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(5 * 1024 ** 3)).toBe("5.0 GB");
  });

  it("muestra los tamaños, no ofrece borrar la carpeta de un proyecto local y exige confirmar", async () => {
    open();
    expect(await screen.findByText(/Clonado/)).toBeInTheDocument();
    expect(screen.getAllByText(/tu carpeta|your own folder/).length).toBe(1);  // a local project has no managed clone
    await userEvent.click(screen.getByRole("button", { name: /(Borrar|Delete) (Clon del repositorio|Repository clone) clonado/ }));
    expect(screen.getByRole("note")).toHaveTextContent(/se vuelve a descargar|downloaded again/i);  // says what will happen
    expect(calls.some((c) => c.method === "POST")).toBe(false);  // nothing deleted yet
    await userEvent.click(screen.getByRole("button", { name: /Confirmar|Confirm/ }));
    await waitFor(() => expect(calls.some((c) => c.path === "/projects/clonado/clean" && JSON.stringify(c.body) === JSON.stringify({ clone: true }))).toBe(true));
    expect(await screen.findByText(/liberaron|Freed/)).toBeInTheDocument();
  });
});
