import { render, renderHook, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { LanguageProvider, LanguageSelector, useLanguage } from "../src/components/LanguageSelector";

beforeEach(() => {
  localStorage.clear();
});

describe("useLanguage", () => {
  it("lanza un error si se usa fuera de LanguageProvider", () => {
    expect(() => renderHook(() => useLanguage())).toThrow(
      "useLanguage debe usarse dentro de LanguageProvider"
    );
  });

  it("por defecto arranca en español si no hay nada guardado", () => {
    const { result } = renderHook(() => useLanguage(), { wrapper: LanguageProvider });
    expect(result.current.language).toBe("es");
    expect(result.current.t("nav_chat")).toBe("Chat");
  });

  it("respeta el idioma guardado en localStorage al montar", () => {
    localStorage.setItem("beacon:language", "en");
    const { result } = renderHook(() => useLanguage(), { wrapper: LanguageProvider });
    expect(result.current.language).toBe("en");
    expect(result.current.t("nav_chat")).toBe("Chat"); // coincide en ambos idiomas
    expect(result.current.t("nav_settings")).toBe("Settings"); // este sí difiere
  });
});

describe("LanguageSelector", () => {
  it("cambiar la selección persiste el idioma en localStorage", async () => {
    const user = userEvent.setup();
    render(
      <LanguageProvider>
        <LanguageSelector />
      </LanguageProvider>
    );

    const select = screen.getByRole("combobox") as HTMLSelectElement;
    expect(select.value).toBe("es");

    await user.selectOptions(select, "en");

    expect(select.value).toBe("en");
    expect(localStorage.getItem("beacon:language")).toBe("en");
  });
});
