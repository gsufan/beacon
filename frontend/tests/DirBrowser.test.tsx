import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { LanguageProvider } from "../src/components/LanguageSelector";
import DirBrowser from "../src/components/DirBrowser";
import { api } from "../src/lib/api";

vi.mock("../src/lib/api", () => ({
  api: { browseDirs: vi.fn() },
}));

function renderDirBrowser(value = "") {
  const onChange = vi.fn();
  render(
    <LanguageProvider>
      <DirBrowser value={value} onChange={onChange} />
    </LanguageProvider>
  );
  return { onChange };
}

beforeEach(() => {
  vi.mocked(api.browseDirs).mockReset();
});

describe("DirBrowser", () => {
  it("al montar, carga el directorio inicial y avisa el path resuelto por onChange", async () => {
    vi.mocked(api.browseDirs).mockResolvedValueOnce({
      path: "C:/Users/gabri",
      parent: "C:/Users",
      directories: ["Desktop", "Documents"],
    });

    const { onChange } = renderDirBrowser();

    await waitFor(() => expect(onChange).toHaveBeenCalledWith("C:/Users/gabri"));
    expect(screen.getByText("Desktop")).toBeInTheDocument();
    expect(screen.getByText("Documents")).toBeInTheDocument();
  });

  it("muestra el mensaje de 'sin subcarpetas' cuando la lista viene vacía", async () => {
    vi.mocked(api.browseDirs).mockResolvedValueOnce({
      path: "C:/vacio",
      parent: "C:/",
      directories: [],
    });

    renderDirBrowser();

    expect(await screen.findByText("Sin subcarpetas.")).toBeInTheDocument();
  });

  it("no muestra el botón 'Subir' cuando no hay carpeta padre (raíz)", async () => {
    vi.mocked(api.browseDirs).mockResolvedValueOnce({
      path: "C:/",
      parent: null,
      directories: ["Users"],
    });

    renderDirBrowser();

    await screen.findByText("Users");
    expect(screen.queryByText("Subir")).not.toBeInTheDocument();
  });

  it("hacer click en una subcarpeta navega hacia adentro (nueva llamada a browseDirs)", async () => {
    const user = userEvent.setup();
    vi.mocked(api.browseDirs)
      .mockResolvedValueOnce({ path: "C:/raiz", parent: "C:/", directories: ["hijo"] })
      .mockResolvedValueOnce({ path: "C:/raiz/hijo", parent: "C:/raiz", directories: [] });

    renderDirBrowser();
    await screen.findByText("hijo");

    await user.click(screen.getByText("hijo"));

    await waitFor(() => expect(api.browseDirs).toHaveBeenCalledWith("C:/raiz/hijo"));
  });

  it("si la API falla, muestra el mensaje de error en vez de quedar en blanco", async () => {
    vi.mocked(api.browseDirs).mockRejectedValueOnce(new Error("permiso denegado"));

    renderDirBrowser();

    expect(await screen.findByText("permiso denegado")).toBeInTheDocument();
  });
});
