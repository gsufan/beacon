// Team mode (Beacon Hub): login, navigation by role, "My work" with GitHub issues and "Where do I start?".
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../src/App";

type Handler = (url: URL, init?: RequestInit) => { status?: number; body: unknown };

function sessionFor(admin: boolean, roles: Record<string, string>) {
  return {
    session: "s.sig",
    license: "l.sig",
    user: { sub: 2, org: 1, name: "Diego Dev", email: "dev@acme.test", admin, github: "diego", roles,
            exp: Math.floor(Date.now() / 1000) + 3600 },
  };
}

const ISSUE = {
  id: "7", ref: "#7", source: "github",
  number: 7, title: "Login falla con tildes", body: "Al ingresar «José» se cae.", state: "open",
  url: "https://github.com/acme/api/issues/7", author: "ana", assignees: ["diego"], labels: ["bug"],
  updated_at: new Date().toISOString(), comments: 0,
};

let calls: { method: string; path: string; headers: Record<string, string>; body?: unknown }[];

function installFakeServer(overrides: Record<string, Handler> = {}) {
  const table: Record<string, Handler> = {
    "GET /auth/status": () => ({ body: { mode: "team", hub_url: "http://hub.test" } }),
    "POST /hub/auth/login": () => ({ body: sessionFor(false, { api: "developer" }) }),
    "POST /hub/auth/refresh": () => ({ body: sessionFor(false, { api: "developer" }) }),
    "GET /projects": () => ({ body: [{ id: "api", name: "API" }] }),
    "GET /projects/api/health": () => ({ body: { status: "ok", total_chunks_indexados: 120 } }),
    "GET /hub/projects/api/issues": () => ({ body: { source: "github", where: "acme/api", issues: [ISSUE] } }),
    "GET /hub/org": () => ({ body: { id: 1, name: "Acme", plan: "trial", plan_label: "Prueba", seats: 5, seats_used: 2, period_end: Date.now() / 1000 + 86400,
                                     days_left: 29, expired: false, github_connected: true, jira_connected: false, jira_site: null, slack_connected: true } }),
    "POST /hub/projects/api/issues/7/comment": () => ({ body: { url: "https://github.com/acme/api/issues/7#c1" } }),
    "GET /projects/api/experts": () => ({ body: { files: [], overall: [{ name: "Ana Rojas", commits: 12, last: null }] } }),
    "GET /hub/projects/api/github/activity": () => ({
      body: { repo: "acme/api", commits: [{ sha: "abc1234", message: "fix: normaliza tildes (#7)", author: "ana",
                                            date: new Date().toISOString(), url: "c", issues: [7] }] },
    }),
    "POST /projects/api/issues/7/start": () => ({
      body: { issue: { number: 7, title: ISSUE.title, url: ISSUE.url, labels: ["bug"] },
              answer: "Revisa `normalize_name` en auth.py.",
              sources: [{ file_path: "auth.py", chunk_type: "function", name: "normalize_name", start_line: 10,
                          end_line: 30, distance: 0.2, expanded: false }] },
    }),
    ...overrides,
  };
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const url = new URL(input, "http://beacon.test");
    const method = init?.method ?? "GET";
    calls.push({ method, path: url.pathname, headers: (init?.headers ?? {}) as Record<string, string>,
                 body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const handler = table[`${method} ${url.pathname}`];
    const { status = 200, body } = handler ? handler(url, init) : { status: 404, body: { detail: "no existe" } };
    return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  }));
}

function renderApp(path = "/") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
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

describe("modo equipo", () => {
  it("pide iniciar sesión y entra a «Mi trabajo» con la sesión del Hub", async () => {
    const user = userEvent.setup();
    renderApp();
    await user.type(await screen.findByLabelText("Correo"), "dev@acme.test");
    await user.type(screen.getByLabelText("Contraseña"), "secreta123");
    await user.click(screen.getByRole("button", { name: "Entrar" }));
    expect(await screen.findByRole("heading", { name: "Hola, Diego" })).toBeInTheDocument();
    expect(await screen.findByText(/Login falla con tildes/)).toBeInTheDocument();
    // Later requests carry the session and the license signed by the Hub.
    const issues = calls.find((c) => c.path === "/hub/projects/api/issues");
    expect(issues?.headers.Authorization).toBe("Bearer s.sig");
    expect(issues?.headers["X-Beacon-License"]).toBe("l.sig");
  });

  it("un desarrollador no ve Equipo ni Configuración", async () => {
    localStorage.setItem("beacon:session", JSON.stringify(sessionFor(false, { api: "developer" })));
    renderApp();
    expect(await screen.findByRole("link", { name: /Mi trabajo/ })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Equipo/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Configuración/ })).not.toBeInTheDocument();
    expect(screen.getAllByText("Desarrollador").length).toBeGreaterThan(0);
  });

  it("«¿Por dónde empiezo?» muestra la respuesta con sus fuentes", async () => {
    localStorage.setItem("beacon:session", JSON.stringify(sessionFor(false, { api: "developer" })));
    const user = userEvent.setup();
    renderApp();
    await user.click(await screen.findByRole("button", { name: /Por dónde empiezo/ }));
    expect(await screen.findByText("normalize_name", { selector: "code" })).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST" && c.path === "/projects/api/issues/7/start")).toBe(true);
  });

  it("comenta la respuesta en la issue y muestra quién conoce los archivos", async () => {
    localStorage.setItem("beacon:session", JSON.stringify(sessionFor(false, { api: "developer" })));
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    renderApp();
    await user.click(await screen.findByRole("button", { name: /Por dónde empiezo/ }));
    expect(await screen.findByText("Ana Rojas")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Compartir en Slack/ })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Comentar en la issue/ }));
    expect(await screen.findByText("Comentario publicado.")).toBeInTheDocument();
    const comment = calls.find((c) => c.path === "/hub/projects/api/issues/7/comment");
    expect(String((comment?.body as { body: string }).body)).toContain("normalize_name");
    expect(String((comment?.body as { body: string }).body)).toContain("auth.py (10-30)");
  });

  it("arma el borrador del daily con mis issues y commits, y lo comparte en Slack", async () => {
    localStorage.setItem("beacon:session", JSON.stringify(sessionFor(false, { api: "developer" })));
    vi.spyOn(window, "confirm").mockReturnValue(true);
    installFakeServer({
      "POST /projects/api/standup": () => ({
        body: { draft: "**Ayer**\n- Normalicé tildes (#7)", since: "2026-11-27", local_commits: [], remote_commits: 1 },
      }),
      "POST /hub/projects/api/slack": () => ({ body: { status: "ok" } }),
      "GET /hub/projects/api/github/activity": () => ({
        body: { repo: "acme/api", commits: [
          { sha: "abc1234", message: "fix: normaliza tildes (#7)", author: "diego", date: new Date().toISOString(), url: "c", issues: [7] },
          { sha: "def5678", message: "docs: README", author: "ana", date: new Date().toISOString(), url: "d", issues: [] },
        ] },
      }),
    });
    const user = userEvent.setup();
    renderApp();
    await screen.findByText(/Login falla con tildes/);
    await screen.findByText("docs: README");
    await user.click(await screen.findByRole("button", { name: /Borrador del daily/ }));
    expect(await screen.findByText(/Normalicé tildes/)).toBeInTheDocument();
    const body = calls.find((c) => c.path === "/projects/api/standup")?.body as { person: string; git_author: string; issues: unknown[]; commits: unknown[] };
    expect(body.person).toBe("Diego Dev");
    expect(body.git_author).toBe("Diego Dev\\|diego");
    expect(body.issues).toHaveLength(1);
    expect(body.commits).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: /Compartir en Slack/ }));
    expect(calls.some((c) => c.path === "/hub/projects/api/slack")).toBe(true);
  });

  it("el líder técnico verifica un documento y el chat muestra una respuesta verificada", async () => {
    localStorage.setItem("beacon:session", JSON.stringify(sessionFor(false, { api: "tech_lead" })));
    installFakeServer({
      "POST /hub/auth/refresh": () => ({ body: sessionFor(false, { api: "tech_lead" }) }),
      "GET /projects/api/docs/tree": () => ({ body: { files: ["auth.py"], reviews: { "auth.py": "draft" } } }),
      "GET /projects/api/docs": () => ({ body: { file_path: "auth.py", content_markdown: "# auth.py", review: { status: "draft" }, draft_markdown: null } }),
      "POST /projects/api/docs/verify": () => ({ body: { review: { status: "verified", reviewer: "Diego Dev", ts: 1 } } }),
      "POST /projects/api/query": () => ({
        body: { answer: "Respuesta del modelo", sources: [],
                verified: { id: "a1", question: "¿Dónde está el login?", answer: "En auth.login.", reviewer: "Tania", ts: 1, corrected: true, similarity: 0.95, stale_files: [] } },
      }),
    });
    const user = userEvent.setup();
    renderApp("/docs");
    await user.click(await screen.findByRole("button", { name: "auth.py" }));
    expect(await screen.findByText("Borrador IA")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Verificar" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/projects/api/docs/verify")).toBe(true));
  });

  it("«¿Qué probar?» calcula el impacto y el plan de pruebas, y la issue muestra su código asociado", async () => {
    localStorage.setItem("beacon:session", JSON.stringify(sessionFor(false, { api: "developer" })));
    const ref = { name: "login", file_path: "views.py", start_line: 1, end_line: 9 };
    installFakeServer({
      "POST /projects/api/qa-plan": () => ({
        body: { plan: "1. Probar un nombre con tildes.", impact: { files: ["views.py"], targets: [
          { target: "normalize_name", definitions: [], callers: [ref], indirect: [], tests: [], untested: true }] } },
      }),
      "GET /projects/api/issues/7/code": () => ({ body: { commits: [{ sha: "abc1234", message: "fix: tildes (#7)", author: "Ana", date: "" }],
                                                          files: [{ path: "auth.py", commits: 1 }] } }),
    });
    const user = userEvent.setup();
    renderApp();
    await user.click(await screen.findByRole("button", { name: /Qué probar/ }));
    expect(await screen.findByText("Probar un nombre con tildes.")).toBeInTheDocument();
    expect(screen.getByText("sin pruebas")).toBeInTheDocument();
    const qa = calls.find((c) => c.path === "/projects/api/qa-plan");
    expect((qa?.body as { targets: string[] }).targets).toEqual(["normalize_name"]);
    await user.click(screen.getByRole("button", { name: /Por dónde empiezo/ }));
    expect(await screen.findByText("Código asociado a #7")).toBeInTheDocument();
  });

  it("un desarrollador no ve Panorama; el líder técnico ve el factor bus del proyecto", async () => {
    localStorage.setItem("beacon:session", JSON.stringify(sessionFor(false, { api: "developer" })));
    const { unmount } = renderApp();
    expect(await screen.findByRole("link", { name: /Mi trabajo/ })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Panorama/ })).not.toBeInTheDocument();
    unmount();
    localStorage.setItem("beacon:session", JSON.stringify(sessionFor(false, { api: "tech_lead" })));
    installFakeServer({
      "POST /hub/auth/refresh": () => ({ body: sessionFor(false, { api: "tech_lead" }) }),
      "GET /projects/api/knowledge": () => ({ body: { project_bus_factor: 1, people: 3, modules: [
        { module: "src/auth", files: 4, commits: 20, people: 2, bus_factor: 1, risk: "alto", tests: 1, experts: [{ name: "Ana Rojas", commits: 18 }], last: null }] } }),
    });
    renderApp("/panorama");
    expect(await screen.findByRole("link", { name: /Panorama/ })).toBeInTheDocument();
    expect(await screen.findByText(/1 · riesgo alto/)).toBeInTheDocument();
    expect(screen.getByText("Ana Rojas")).toBeInTheDocument();
  });

  it("Soporte recibe las respuestas sin código: la opción viene marcada y no se puede quitar", async () => {
    localStorage.setItem("beacon:session", JSON.stringify(sessionFor(false, { api: "support" })));
    installFakeServer({
      "POST /hub/auth/refresh": () => ({ body: sessionFor(false, { api: "support" }) }),
      "POST /projects/api/query": () => ({ body: { answer: "El sistema rechaza nombres con tildes.", sources: [], plain: true } }),
    });
    const user = userEvent.setup();
    renderApp("/chat");
    const box = await screen.findByRole("checkbox", { name: "Explicar sin código" });
    expect(box).toBeChecked();
    expect(box).toBeDisabled();
    expect(screen.getAllByText("Soporte").length).toBeGreaterThan(0);
    await user.type(screen.getByPlaceholderText(/./, { selector: "textarea" }), "¿Por qué falla el login?");
    await user.keyboard("{Control>}{Enter}{/Control}");
    expect(await screen.findByText("El sistema rechaza nombres con tildes.")).toBeInTheDocument();
    expect((calls.find((c) => c.path === "/projects/api/query")?.body as { plain: boolean }).plain).toBe(true);
  });

  it("una licencia vencida muestra el aviso", async () => {
    localStorage.setItem("beacon:session", JSON.stringify(sessionFor(true, {})));
    installFakeServer({ "GET /projects": () => ({ status: 402, body: { detail: "La licencia venció." } }) });
    renderApp();
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/licencia de tu organización venció/));
  });
});
