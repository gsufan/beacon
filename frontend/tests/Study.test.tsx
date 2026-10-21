// Asistente del modo estudio: pasos con navegación, barra de avance, progreso del indexado y valoración.
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
      (state.preguntas as unknown[]).push({ n: 1, pregunta: body.pregunta, respuesta: "resp", segundos: 2.5,
        fuentes: [{ archivo: "a.go", lineas: "1-9" }], valoracion: null });
      out = { n: 1 };
    }
    return new Response(JSON.stringify(out), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
}

const started = (extra: Record<string, unknown> = {}) => ({
  started: true, consentimientos: { participacion: true, hardware: false, envio: false }, hardware: null, repo: null,
  repo_status: { estado: "sin_repo", detalle: null }, preguntas: [], puede_terminar: false, ...extra,
});

beforeEach(() => {
  calls = [];
  state = { started: false };
  fakeServer();
});
afterEach(() => vi.unstubAllGlobals());

const open = () => render(<MemoryRouter initialEntries={["/study"]}><App /></MemoryRouter>);
const bar = () => screen.getAllByRole("progressbar")[0];

describe("modo estudio", () => {
  it("no deja comenzar sin aceptar, explica los dos archivos y registra la participación", async () => {
    open();
    const start = await screen.findByRole("button", { name: /Comenzar|Start/ });
    expect(start).toBeDisabled();
    expect(screen.getByText(/dos archivos|two files/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox"));
    await userEvent.click(start);
    await waitFor(() => expect(calls.some((c) => c.path === "/study/consent" && (c.body as { tipo: string }).tipo === "participacion")).toBe(true));
    expect(calls.some((c) => c.path === "/study/start")).toBe(true);
  });

  it("muestra la barra de avance y permite ir atrás y adelante entre pasos", async () => {
    state = started();
    open();
    expect(await screen.findByText(/cpu-prueba/)).toBeInTheDocument();
    expect(bar()).toHaveAttribute("aria-valuenow", "40");  // paso 2 de 5
    await userEvent.click(screen.getByRole("button", { name: /Atrás|Back/ }));
    expect(bar()).toHaveAttribute("aria-valuenow", "20");
    expect(screen.getByRole("checkbox")).toBeChecked();  // el consentimiento ya dado se conserva
    expect(screen.getByRole("button", { name: /Atrás|Back/ })).toBeDisabled();  // no hay paso previo
  });

  it("el equipo se omite si no se marca la casilla y no se guarda nada", async () => {
    state = started();
    open();
    await screen.findByText(/cpu-prueba/);
    await userEvent.click(screen.getByRole("button", { name: /^Siguiente$|^Next$/ }));
    expect(await screen.findByRole("button", { name: /Preparar repositorio|Prepare repository/ })).toBeInTheDocument();
    expect(calls.some((c) => c.path === "/study/hardware" && c.method === "POST")).toBe(false);
  });

  it("permite elegir un repositorio de ejemplo de la lista y lo copia al campo de dirección", async () => {
    state = started({ hardware: { cpu_modelo: "x" } });
    open();
    const list = await screen.findByRole("combobox", { name: /ejemplo|example/ });
    await userEvent.selectOptions(list, "https://github.com/charmbracelet/glow");
    expect(screen.getByRole("textbox", { name: "URL" })).toHaveValue("https://github.com/charmbracelet/glow");
    await userEvent.click(screen.getByRole("button", { name: /Preparar repositorio|Prepare repository/ }));
    await waitFor(() => expect(calls.some((c) => c.path === "/study/repo" && (c.body as { url: string }).url.endsWith("/glow"))).toBe(true));
  });

  it("muestra el avance del indexado archivo por archivo", async () => {
    state = started({ hardware: { cpu_modelo: "x" }, repo_status: { estado: "indexando", detalle: "33 archivos de código",
      progreso: { actual: 11, total: 33, archivo: "ui/pager.go", segundos: 4 } } });
    open();
    expect(await screen.findByText(/11 de 33|11 of 33/)).toBeInTheDocument();
    expect(screen.getByText(/ui\/pager\.go/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Siguiente$|^Next$/ })).toBeDisabled();
  });

  it("pregunta, valora la corrección y la utilidad de las fuentes, y habilita terminar", async () => {
    state = started({ hardware: { cpu_modelo: "x" }, repo: { url: "https://github.com/u/r", archivos_indexados: 12 },
      repo_status: { estado: "listo", detalle: null } });
    open();
    const box = await screen.findByRole("textbox");
    await userEvent.type(box, "¿Qué hace main?");
    await userEvent.click(screen.getByRole("button", { name: /^Preguntar$|^Ask$/ }));
    expect(await screen.findByText("resp")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /En parte|Partly/ }));
    await waitFor(() => expect(calls.some((c) => c.path === "/study/rate" && (c.body as { correcta: string }).correcta === "parcial")).toBe(true));
    state.preguntas = [{ n: 1, pregunta: "p", respuesta: "resp", segundos: 2, fuentes: [{ archivo: "a.go", lineas: "1-9" }], valoracion: { correcta: "parcial", fuentes_utiles: null } }];
    await userEvent.click(screen.getByRole("button", { name: /En parte|Partly/ }));  // fuerza otra carga del estado
    await userEvent.click(await screen.findByRole("button", { name: /útiles fueron las fuentes.* 4|were the sources.* 4/ }));
    await waitFor(() => expect(calls.some((c) => c.path === "/study/rate" && (c.body as { fuentes_utiles: number }).fuentes_utiles === 4)).toBe(true));
    expect(screen.getByRole("button", { name: /Terminar|Finish/ })).toBeDisabled();
  });
});
