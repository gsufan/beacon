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
    expect(screen.getByRole("heading", { name: "Fuentes de la pregunta 1" })).toBeInTheDocument();
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
    expect(screen.getByText("Sin fuentes")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reintentar" })).toBeInTheDocument();
  });

  it("conserva las preguntas anteriores y permite volver a sus fuentes", async () => {
    const user = userEvent.setup();
    renderApp();
    await screen.findByRole("option", { name: "Demo" });

    await user.type(screen.getByRole("textbox"), "Primera pregunta");
    await user.click(screen.getByRole("button", { name: "Preguntar" }));
    await screen.findByRole("heading", { name: "Fuentes de la pregunta 1" });

    await user.type(screen.getByRole("textbox"), "Segunda pregunta");
    await user.click(screen.getByRole("button", { name: "Preguntar" }));
    expect(await screen.findByRole("heading", { name: "Fuentes de la pregunta 2" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Primera pregunta/ }));
    expect(screen.getByRole("heading", { name: "Fuentes de la pregunta 1" })).toBeInTheDocument();
  });

  it("reintenta una pregunta fallida sin volver a escribirla", async () => {
    let attempts = 0;
    installFakeServer({
      "POST /projects/demo/query": () =>
        ++attempts === 1
          ? { status: 500, body: { detail: "Error en el motor RAG: sin conexión" } }
          : { body: { answer: "Respuesta tras reintentar.", sources: [SOURCE] } },
    });
    const user = userEvent.setup();
    renderApp();
    await screen.findByRole("option", { name: "Demo" });

    await user.type(screen.getByRole("textbox"), "¿Qué hace este servicio?");
    await user.click(screen.getByRole("button", { name: "Preguntar" }));
    await user.click(await screen.findByRole("button", { name: "Reintentar" }));

    expect(await screen.findByText("Respuesta tras reintentar.")).toBeInTheDocument();
    expect(calls.filter((c) => c.path.endsWith("/query"))).toHaveLength(2);
  });

  it("envía una pregunta de ejemplo con un clic", async () => {
    const user = userEvent.setup();
    renderApp();
    await screen.findByRole("option", { name: "Demo" });
    await user.click(screen.getByRole("button", { name: "¿Dónde se manejan los errores?" }));
    await screen.findByRole("heading", { name: "Fuentes de la pregunta 1" });
    expect(calls.find((c) => c.path.endsWith("/query"))?.body).toMatchObject({
      question: "¿Dónde se manejan los errores?",
    });
  });

  it("avisa en la cabecera cuando Ollama no responde", async () => {
    installFakeServer({
      "GET /system/ai-status": () => ({
        body: { reachable: false, llm_model_available: false, embedding_model_available: false },
      }),
    });
    renderApp();
    expect(await screen.findByText("Ollama no responde")).toBeInTheDocument();
  });

  it("no muestra avisos cuando Ollama y los modelos están disponibles", async () => {
    installFakeServer({
      "GET /system/ai-status": () => ({
        body: { reachable: true, llm_model_available: true, embedding_model_available: true },
      }),
    });
    renderApp();
    await screen.findByText("llama3:8b");
    expect(screen.queryByText("Ollama no responde")).not.toBeInTheDocument();
    expect(screen.queryByText("Falta instalar un modelo configurado")).not.toBeInTheDocument();
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

describe("documentación: filtro", () => {
  it("filtra la lista de archivos por texto", async () => {
    installFakeServer({
      "GET /projects/demo/docs/tree": () => ({
        body: { files: ["src/requests/sessions.py.md", "src/requests/adapters.py.md"] },
      }),
    });
    const user = userEvent.setup();
    renderApp("/docs");
    await screen.findByRole("button", { name: "src/requests/adapters.py.md" });

    await user.type(screen.getByRole("searchbox", { name: "Filtrar archivos" }), "sess");
    expect(screen.getByRole("button", { name: "src/requests/sessions.py.md" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "src/requests/adapters.py.md" })).not.toBeInTheDocument();
  });
});

describe("documentación: árbol", () => {
  const FILES = ["src/requests/sessions.py.md", "src/requests/packages/compat.py.md", "tests/test_sessions.py.md", "setup.py.md"];

  it("agrupa por carpetas plegables y deja cerradas las anidadas", async () => {
    installFakeServer({ "GET /projects/demo/docs/tree": () => ({ body: { files: FILES } }) });
    const user = userEvent.setup();
    renderApp("/docs");

    const folder = await screen.findByRole("button", { name: "src/requests" });
    expect(folder).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("button", { name: "src/requests/sessions.py.md" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "setup.py.md" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "src/requests/packages/compat.py.md" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "packages" }));
    expect(screen.getByRole("button", { name: "src/requests/packages/compat.py.md" })).toBeInTheDocument();

    await user.click(folder);
    expect(folder).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("button", { name: "src/requests/sessions.py.md" })).not.toBeInTheDocument();
  });

  it("al filtrar muestra también lo que está en carpetas cerradas", async () => {
    installFakeServer({ "GET /projects/demo/docs/tree": () => ({ body: { files: FILES } }) });
    const user = userEvent.setup();
    renderApp("/docs");
    await screen.findByRole("button", { name: "src/requests" });

    await user.type(screen.getByRole("searchbox", { name: "Filtrar archivos" }), "compat");
    expect(screen.getByRole("button", { name: "src/requests/packages/compat.py.md" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "setup.py.md" })).not.toBeInTheDocument();
  });
});

describe("configuración: proyectos y modelos", () => {
  it("pide confirmación antes de eliminar un proyecto", async () => {
    installFakeServer({ "DELETE /projects/demo": () => ({ body: { status: "deleted" } }) });
    const user = userEvent.setup();
    renderApp("/settings");

    await user.click(await screen.findByRole("button", { name: "Eliminar" }));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);

    const confirm = screen.getByRole("alert");
    expect(confirm).toHaveTextContent("¿Eliminar 'demo' de Beacon?");
    await user.click(within(confirm).getByRole("button", { name: "Eliminar" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.path === "/projects/demo")).toBe(true));
  });

  it("cancelar la confirmación no elimina nada", async () => {
    const user = userEvent.setup();
    renderApp("/settings");
    await user.click(await screen.findByRole("button", { name: "Eliminar" }));
    await user.click(screen.getByRole("button", { name: "Cancelar" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
  });

  it("sigue la sincronización de cada proyecto por separado", async () => {
    const twoProjects = {
      ai_provider: { provider: "ollama", ollama_host: "http://localhost:11434",
                     embedding_model: "qwen3-embedding:0.6b", llm_model: "llama3:8b" },
      projects: [
        { id: "demo", name: "Demo", repo_path: "/repos/demo", source_type: "local", auto_watch: false },
        { id: "otro", name: "Otro", repo_path: "/repos/otro", source_type: "local", auto_watch: false },
      ],
    };
    installFakeServer({
      "GET /config": () => ({ body: twoProjects }),
      "POST /projects/demo/sync": () => ({ status: 202, body: { status: "started" } }),
      "POST /projects/otro/sync": () => ({ status: 202, body: { status: "started" } }),
    });
    const user = userEvent.setup();
    renderApp("/settings");

    const syncButtons = await screen.findAllByRole("button", { name: "Sincronizar" });
    await user.click(syncButtons[0]);
    await user.click(syncButtons[1]);

    expect(await screen.findAllByText("Sincronizando...")).toHaveLength(2);
    expect(syncButtons[0]).toBeDisabled();
    expect(syncButtons[1]).toBeDisabled();
  });

  it("retoma una sincronización que ya estaba en curso al abrir la página", async () => {
    installFakeServer({
      "GET /projects/demo/sync-status": () => ({ body: { status: "running", detail: null } }),
    });
    renderApp("/settings");
    expect(await screen.findByText("Sincronizando...")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sincronizar" })).toBeDisabled();
  });

  it("avisa cuando un modelo configurado no está instalado", async () => {
    installFakeServer({ "GET /system/available-models": () => ({ body: { models: ["llama3:8b"] } }) });
    renderApp("/settings");
    expect(await screen.findByText("Este modelo no está instalado en Ollama.")).toBeInTheDocument();
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
