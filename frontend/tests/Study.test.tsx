// Asistente del modo estudio: consentimiento, paso de equipo, repositorio, preguntas con valoración y cierre.
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../src/App";

let calls: { method: string; path: string; body?: unknown }[];
let state: Record<string, unknown>;

function fakeServer() {
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, path: input, body });
    let out: unknown = { ok: true };
    if (input === "/study/state") out = state;
    if (input === "/study/hardware-preview") out = { cpu_modelo: "cpu-prueba", gpus: [] };
    if (input === "/study/ask") {
      (state.preguntas as unknown[]).push({ n: 1, pregunta: body.pregunta, respuesta: "resp", segundos: 2.5, fuentes: [], valoracion: null });
      out = { n: 1 };
    }
    return new Response(JSON.stringify(out), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
}

beforeEach(() => {
  calls = [];
  state = { started: false };
  fakeServer();
});
afterEach(() => vi.unstubAllGlobals());

const open = () => render(<MemoryRouter initialEntries={["/study"]}><App /></MemoryRouter>);

describe("modo estudio", () => {
  it("no deja comenzar sin aceptar y registra la participación al aceptar", async () => {
    open();
    const start = await screen.findByRole("button", { name: /Comenzar|Start/ });
    expect(start).toBeDisabled();
    await userEvent.click(screen.getByRole("checkbox"));
    await userEvent.click(start);
    await waitFor(() => expect(calls.some((c) => c.path === "/study/consent" && (c.body as { tipo: string }).tipo === "participacion")).toBe(true));
    expect(calls.some((c) => c.path === "/study/start")).toBe(true);
  });

  it("muestra los datos del equipo antes de guardarlos y permite omitirlos", async () => {
    state = { started: true, consentimientos: { participacion: true, hardware: false, envio: false }, hardware: null, repo: null, repo_status: { estado: "sin_repo", detalle: null }, preguntas: [] };
    open();
    expect(await screen.findByText(/cpu-prueba/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Prefiero no compartirlos|I prefer not/ }));
    expect(await screen.findByRole("button", { name: /Preparar repositorio|Prepare repository/ })).toBeInTheDocument();
    expect(calls.some((c) => c.path === "/study/hardware" && c.method === "POST")).toBe(false);
  });

  it("pregunta, valora y habilita terminar con el mínimo de preguntas", async () => {
    state = { started: true, consentimientos: { participacion: true, hardware: false, envio: false }, hardware: null,
      repo: { url: "https://github.com/u/r", archivos_indexados: 12 }, repo_status: { estado: "listo", detalle: null }, preguntas: [], puede_terminar: false };
    open();
    const box = await screen.findByRole("textbox");
    await userEvent.type(box, "¿Qué hace main?");
    await userEvent.click(screen.getByRole("button", { name: /^Preguntar$|^Ask$/ }));
    expect(await screen.findByText("resp")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /En parte|Partly/ }));
    await waitFor(() => expect(calls.some((c) => c.path === "/study/rate" && (c.body as { correcta: string }).correcta === "parcial")).toBe(true));
    expect(screen.getByRole("button", { name: /Terminar|Finish/ })).toBeDisabled();
  });
});
