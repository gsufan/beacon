import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, getApiKey, setApiKey } from "../src/lib/api";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("getApiKey / setApiKey", () => {
  it("empieza vacío si no hay nada guardado", () => {
    expect(getApiKey()).toBe("");
  });

  it("persiste la clave en localStorage", () => {
    setApiKey("mi-clave");
    expect(getApiKey()).toBe("mi-clave");
    expect(localStorage.getItem("beacon:apiKey")).toBe("mi-clave");
  });

  it("borra la clave guardada si se setea con string vacío", () => {
    setApiKey("mi-clave");
    setApiKey("");
    expect(getApiKey()).toBe("");
    expect(localStorage.getItem("beacon:apiKey")).toBeNull();
  });
});

describe("api.listProjects (request wrapper)", () => {
  it("hace GET a /projects y devuelve el JSON parseado", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(jsonResponse([{ id: "demo" }]));

    const result = await api.listProjects();

    expect(result).toEqual([{ id: "demo" }]);
    expect(fetch).toHaveBeenCalledWith("/projects", expect.objectContaining({ headers: expect.any(Object) }));
  });

  it("no manda header X-API-Key si no hay clave guardada", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(jsonResponse([]));

    await api.listProjects();

    const [, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(init.headers).not.toHaveProperty("X-API-Key");
  });

  it("manda el header X-API-Key cuando hay una clave guardada", async () => {
    setApiKey("secreto123");
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(jsonResponse([]));

    await api.listProjects();

    const [, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(init.headers["X-API-Key"]).toBe("secreto123");
  });
});

describe("request(): manejo de errores", () => {
  it("lanza ApiError con el status y el detail del body cuando la respuesta no es ok", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      jsonResponse({ detail: "Proyecto no encontrado" }, 404)
    );

    await expect(api.listProjects()).rejects.toMatchObject({
      status: 404,
      message: "Proyecto no encontrado",
    });
  });

  it("la excepción lanzada es una instancia de ApiError", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(jsonResponse({ detail: "boom" }, 500));

    try {
      await api.listProjects();
      expect.unreachable("debería haber lanzado");
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
    }
  });

  it("usa el statusText si el body de error no es JSON válido", async () => {
    const res = new Response("no es json", { status: 502, statusText: "Bad Gateway" });
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(res);

    await expect(api.listProjects()).rejects.toMatchObject({
      status: 502,
      message: "Bad Gateway",
    });
  });
});

describe("request(): respuestas sin cuerpo", () => {
  it("204 No Content devuelve undefined en vez de intentar parsear JSON", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(new Response(null, { status: 204 }));

    const result = await api.deleteProject("cualquier-id");

    expect(result).toBeUndefined();
  });
});

describe("api.doc: encoding de query params", () => {
  it("codifica el file_path en la URL", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(jsonResponse({ content: "# doc" }));

    await api.doc("demo", "src/a b/archivo raro.py");

    const [url] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe("/projects/demo/docs?file_path=src%2Fa%20b%2Farchivo%20raro.py");
  });
});
