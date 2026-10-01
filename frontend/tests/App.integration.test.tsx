// Pruebas de integración de la interfaz: la aplicación completa (rutas,
// contexto de proyecto e idioma, llamadas a la API) con un servidor simulado
// en `fetch`. Cubren los flujos que antes solo se probaban a mano en el
// navegador: navegar entre vistas, preguntar y ver fuentes, leer la
// documentación y mostrar los errores de la API.
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../src/App";

type Handler = (url: URL, init?: RequestInit) => { status?: number; body: unknown };

const SOURCE = {
  file_path: "src/requests/sessions.py", chunk_type: "function", name: "rebuild_method",
  start_line: 370, end_line: 392, distance: 0.21, expanded: false,
};

const routes: Record<string, Handler> = {
  "GET /projects": () => ({ body: [{ id: "demo", name: "Demo" }] }),
  "POST /projects/demo/query": () => ({
    body: { answer: "La función rebuild_method cambia POST a GET tras un 303.", sources: [SOURCE] },
  }),
  "GET /projects/demo/docs/tree": () => ({ body: { files: ["src/requests/sessions.py.md"] } }),
  "GET /projects/demo/docs": (url) => ({
    body: { file_path: url.searchParams.get("file_path"), content_markdown: "# sessions.py\n\nManeja la sesión." },
  }),
  "GET /config": () => ({
    body: {
      ai_provider: { provider: "ollama", ollama_host: "http://localhost:11434",
                     embedding_model: "qwen3-embedding:0.6b", llm_model: "llama3:8b" },
      projects: [{ id: "demo", name: "Demo", repo_path: "/repos/demo", source_type: "local", auto_watch: false }],
    },
  }),
  "GET /system/providers": () => ({ body: { providers: [{ id: "ollama", label: "Ollama" }] } }),
  "GET /system/available-models": () => ({ body: { models: ["llama3:8b", "qwen3-embedding:0.6b"] } }),
};

let calls: { method: string; path: string; body?: unknown }[];

function installFakeServer(overrides: Record<string, Handler> = {}) {
  const table = { ...routes, ...overrides };
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const url = new URL(input, "http://beacon.test");
    const method = init?.method ?? "GET";
    calls.push({ method, path: url.pathname, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const handler = table[`${method} ${url.pathname}`];
    const { status = 200, body } = handler ? handler(url, init) : { status: 404, body: { detail: "no existe" } };
    return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  }));
}

function renderApp(path = "/") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>
  );
}

beforeEach(() => {
  localStorage.clear();
  calls = [];
  installFakeServer();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("navegación", () => {
  it("parte en el chat y marca el enlace activo", async () => {
    renderApp();
    expect(await screen.findByRole("heading", { name: "Preguntar al código" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Chat/ })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: /Docs/ })).not.toHaveAttribute("aria-current");
  });

  it("cambia de vista con los enlaces de la barra lateral", async () => {
    const user = userEvent.setup();
    renderApp();
    await user.click(screen.getByRole("link", { name: /Docs/ }));
    expect(await screen.findByText("Archivos documentados")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Docs/ })).toHaveAttribute("aria-current", "page");

    await user.click(screen.getByRole("link", { name: /Configuración/ }));
    expect(await screen.findByText("Proveedor de IA")).toBeInTheDocument();
  });

  it("carga los proyectos y elige el primero", async () => {
    renderApp();
    expect(await screen.findByRole("option", { name: "Demo" })).toBeInTheDocument();
    await waitFor(() => expect(localStorage.getItem("beacon:projectId")).toBe("demo"));
  });
});

describe("chat", () => {
  it("envía la pregunta con el idioma y muestra respuesta y fuentes", async () => {
    const user = userEvent.setup();
    renderApp();
    await screen.findByRole("option", { name: "Demo" });

    await user.type(screen.getByRole("textbox"), "¿Por qué POST pasa a GET?");
    await user.click(screen.getByRole("button", { name: "Preguntar" }));

    expect(await screen.findByText(/rebuild_method cambia POST a GET/)).toBeInTheDocument();
    expect(screen.getByText("Fuentes")).toBeInTheDocument();
    expect(screen.getByText(/src\/requests\/sessions\.py/)).toBeInTheDocument();
    const query = calls.find((c) => c.path === "/projects/demo/query");
    expect(query?.method).toBe("POST");
    expect(query?.body).toMatchObject({ question: "¿Por qué POST pasa a GET?", language: "es" });
  });

  it("muestra el mensaje de la API cuando el índice es de otro modelo (409)", async () => {
    installFakeServer({
      "POST /projects/demo/query": () => ({
        status: 409,
        body: { detail: "El índice se construyó con nomic-embed-text. Sincroniza el proyecto." },
      }),
    });
    const user = userEvent.setup();
    renderApp();
    await screen.findByRole("option", { name: "Demo" });

    await user.type(screen.getByRole("textbox"), "¿Qué hace este servicio?");
    await user.click(screen.getByRole("button", { name: "Preguntar" }));

    expect(await screen.findByText(/Sincroniza el proyecto/)).toBeInTheDocument();
    expect(screen.queryByText("Fuentes")).not.toBeInTheDocument();
  });

  it("no consulta con preguntas demasiado cortas", async () => {
    const user = userEvent.setup();
    renderApp();
    await screen.findByRole("option", { name: "Demo" });
    await user.type(screen.getByRole("textbox"), "ok");
    await user.click(screen.getByRole("button", { name: "Preguntar" }));
    expect(calls.some((c) => c.path.endsWith("/query"))).toBe(false);
  });
});

describe("documentación", () => {
  it("lista los archivos y muestra el elegido", async () => {
    const user = userEvent.setup();
    renderApp("/docs");
    const file = await screen.findByRole("button", { name: "src/requests/sessions.py.md" });
    expect(screen.getByText("Elige un archivo para ver su documentación.")).toBeInTheDocument();

    await user.click(file);
    const doc = await screen.findByRole("heading", { name: "sessions.py" });
    expect(within(doc.closest("div")!).getByText("Maneja la sesión.")).toBeInTheDocument();
    const docCall = calls.find((c) => c.path === "/projects/demo/docs");
    expect(docCall).toBeDefined();
  });
});

describe("configuración", () => {
  it("muestra el proveedor y los modelos detectados en Ollama", async () => {
    renderApp("/settings");
    expect(await screen.findByDisplayValue("http://localhost:11434")).toBeInTheDocument();
    await waitFor(() => expect(calls.some((c) => c.path === "/system/available-models")).toBe(true));
    expect(screen.queryByText(/No se detectaron modelos/)).not.toBeInTheDocument();
  });
});
