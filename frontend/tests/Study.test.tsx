// Study mode wizard: steps with navigation, progress bar, indexing progress and rating.
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../src/App";

let calls: { method: string; path: string; body?: unknown }[];
let state: Record<string, unknown>;
const HEALTHY = [
  { id: "ollama", estado: "ok", valor: "http://localhost:11434" },
  { id: "model_embedding", estado: "ok", valor: "qwen3-embedding:0.6b" },
  { id: "model_llm", estado: "ok", valor: "llama3:8b" },
  { id: "gpu", estado: "ok", valor: "GPU de prueba (8 GB)" },
  { id: "disk", estado: "ok", valor: "50 GB" },
  { id: "network", estado: "ok", valor: "github.com" },
];
let health: { id: string; estado: string; valor: string }[];

function fakeServer() {
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, path: input, body });
    let out: unknown = { ok: true };
    if (input === "/study/state") out = state;
    if (input === "/study/health") out = { items: health };
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
  health = HEALTHY;
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

  it("revisa Ollama, los modelos, la GPU, el disco y la red antes de comenzar y bloquea si falta algo esencial", async () => {
    health = [
      { id: "ollama", estado: "ok", valor: "http://localhost:11434" },
      { id: "model_embedding", estado: "error", valor: "qwen3-embedding:0.6b" },
      { id: "model_llm", estado: "ok", valor: "llama3:8b" },
      { id: "gpu", estado: "warn", valor: "" },
      { id: "disk", estado: "ok", valor: "50 GB" },
      { id: "network", estado: "ok", valor: "github.com" },
    ];
    open();
    expect(await screen.findByText(/ollama pull qwen3-embedding:0\.6b/)).toBeInTheDocument();
    expect(screen.getByText(/No se detectó una GPU|No NVIDIA GPU/)).toBeInTheDocument();  // a warning, it does not block
    await userEvent.click(screen.getByRole("checkbox"));
    expect(screen.getByRole("button", { name: /Comenzar|Start/ })).toBeDisabled();
    health = HEALTHY;  // the participant installs the model and retries
    await userEvent.click(screen.getByRole("button", { name: /Reintentar|Retry/ }));
    await waitFor(() => expect(screen.getByRole("button", { name: /Comenzar|Start/ })).toBeEnabled());
  });

  it("permite cancelar una pregunta mientras el modelo responde", async () => {
    state = started({ hardware: { cpu_modelo: "x" }, repo: { url: "https://github.com/u/r", archivos_indexados: 3 },
      repo_status: { estado: "listo", detalle: null } });
    let release: () => void = () => undefined;
    const original = globalThis.fetch;
    vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
      if (input === "/study/ask") {
        calls.push({ method: "POST", path: input });
        await new Promise<void>((resolve) => { release = resolve; });
        return new Response(JSON.stringify({ detail: "Pregunta cancelada." }), { status: 409, headers: { "Content-Type": "application/json" } });
      }
      return original(input, init);
    }));
    open();
    await userEvent.type(await screen.findByRole("textbox"), "una pregunta");
    await userEvent.click(screen.getByRole("button", { name: /^Preguntar$|^Ask$/ }));
    await userEvent.click(await screen.findByRole("button", { name: /^Cancelar$|^Cancel$/ }));
    await waitFor(() => expect(calls.some((c) => c.path === "/study/cancel" && c.method === "POST")).toBe(true));
    release();
    expect(await screen.findByRole("alert")).toHaveTextContent(/cancelada/);
  });

  it("muestra la barra de avance y permite ir atrás y adelante entre pasos", async () => {
    state = started();
    open();
    expect(await screen.findByText(/cpu-prueba/)).toBeInTheDocument();
    expect(bar()).toHaveAttribute("aria-valuenow", "40");  // step 2 of 5
    await userEvent.click(screen.getByRole("button", { name: /Atrás|Back/ }));
    expect(bar()).toHaveAttribute("aria-valuenow", "20");
    expect(screen.getByRole("checkbox")).toBeChecked();  // a consent already given is kept
    expect(screen.getByRole("button", { name: /Atrás|Back/ })).toBeDisabled();  // there is no previous step
  });

  it("el equipo se omite si no se marca la casilla y no se guarda nada", async () => {
    state = started();
    open();
    await screen.findByText(/cpu-prueba/);
    await userEvent.click(screen.getByRole("button", { name: /^Siguiente$|^Next$/ }));
    expect(await screen.findByRole("button", { name: /Preparar repositorio|Prepare repository/ })).toBeInTheDocument();
    expect(calls.some((c) => c.path === "/study/hardware" && c.method === "POST")).toBe(false);
  });

  it("muestra una lista de repositorios de ejemplo con descripción y copia el elegido al campo de dirección", async () => {
    state = started({ hardware: { cpu_modelo: "x" }, carpeta_repo: "/datos/estudio" });
    open();
    const list = await screen.findByRole("list", { name: /ejemplo|example/ });
    expect(list.querySelectorAll("li").length).toBeGreaterThanOrEqual(10);
    expect(screen.getByRole("link", { name: "charmbracelet/glow" })).toHaveAttribute("href", "https://github.com/charmbracelet/glow");
    expect(screen.getByText(/Lector de Markdown|Markdown reader/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /charmbracelet\/glow/ }));
    expect(screen.getByRole("textbox", { name: "URL" })).toHaveValue("https://github.com/charmbracelet/glow");
    expect(screen.getByText(/\/datos\/estudio/)).toBeInTheDocument();  // where cloned repos are stored
    await userEvent.click(screen.getByRole("button", { name: /Preparar repositorio|Prepare repository/ }));
    await waitFor(() => expect(calls.some((c) => c.path === "/study/repo" && (c.body as { url: string }).url.endsWith("/glow"))).toBe(true));
    expect(await screen.findByRole("link", { name: "https://github.com/charmbracelet/glow" })).toBeInTheDocument();  // "Working on"
  });

  it("descartar y salir detiene el servidor y avisa que ya se puede cerrar la pestaña", async () => {
    state = started({ hardware: { cpu_modelo: "x" }, repo: { url: "https://github.com/u/r", archivos_indexados: 3 },
      repo_status: { estado: "listo", detalle: null }, puede_terminar: true });
    open();
    await userEvent.click(await screen.findByRole("button", { name: /Terminar|Finish/ }));
    await userEvent.click(await screen.findByRole("button", { name: /Descartar todo y salir|Discard everything and quit/ }));
    expect(await screen.findByText(/Ya puedes cerrar esta pestaña|You can close this tab now/)).toBeInTheDocument();
    expect(calls.some((c) => c.path === "/study/quit" && c.method === "POST")).toBe(true);
  });

  it("«elegir otro repositorio» abre el formulario y se mantiene abierto hasta que el nuevo está listo", async () => {
    state = started({ hardware: { cpu_modelo: "x" }, repo: { url: "https://github.com/u/r", archivos_indexados: 3 },
      repo_status: { estado: "listo", detalle: null }, carpeta_repo: "/datos/estudio" });
    open();
    await userEvent.click(await screen.findByRole("button", { name: /Atrás|Back/ }));  // from step 4 to 3
    await userEvent.click(await screen.findByRole("button", { name: /Elegir otro repositorio|Pick another repository/ }));
    expect(await screen.findByRole("textbox", { name: "URL" })).toBeInTheDocument();
    await new Promise((r) => setTimeout(r, 50));  // the effect that closed the form ran right after
    expect(screen.getByRole("textbox", { name: "URL" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Siguiente$|^Next$/ })).toBeDisabled();  // until the new one is ready
    expect(screen.getByRole("textbox", { name: "URL" })).toHaveValue("");  // starts empty, not repeating the previous one
  });

  it("elegir el mismo repositorio que ya está listo no vuelve a indexarlo", async () => {
    state = started({ hardware: { cpu_modelo: "x" }, repo: { url: "https://github.com/u/r", archivos_indexados: 3 },
      repo_status: { estado: "listo", detalle: null } });
    open();
    await userEvent.click(await screen.findByRole("button", { name: /Atrás|Back/ }));
    await userEvent.click(await screen.findByRole("button", { name: /Elegir otro repositorio|Pick another repository/ }));
    await userEvent.type(screen.getByRole("textbox", { name: "URL" }), "https://github.com/u/r");
    await userEvent.click(screen.getByRole("button", { name: /Preparar repositorio|Prepare repository/ }));
    expect(calls.some((c) => c.path === "/study/repo")).toBe(false);
    expect(screen.getByText(/Repositorio listo|Repository ready/)).toBeInTheDocument();
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
    await userEvent.click(screen.getByRole("button", { name: /En parte|Partly/ }));  // forces another state load
    await userEvent.click(await screen.findByRole("button", { name: /útiles fueron las fuentes.* 4|were the sources.* 4/ }));
    await waitFor(() => expect(calls.some((c) => c.path === "/study/rate" && (c.body as { fuentes_utiles: number }).fuentes_utiles === 4)).toBe(true));
    expect(screen.getByRole("button", { name: /Terminar|Finish/ })).toBeDisabled();
  });
});
