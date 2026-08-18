import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { LanguageProvider } from "../src/components/LanguageSelector";
import SourceCitation from "../src/components/SourceCitation";
import type { SourceItem } from "../src/lib/types";

function baseSource(overrides: Partial<SourceItem> = {}): SourceItem {
  return {
    file_path: "src/cartservice/src/services/CartService.cs",
    chunk_type: "class",
    name: "CartService",
    start_line: 24,
    end_line: 50,
    distance: 0.291,
    expanded: false,
    ...overrides,
  };
}

function renderWithLanguage(source: SourceItem) {
  return render(
    <LanguageProvider>
      <SourceCitation source={source} />
    </LanguageProvider>
  );
}

describe("SourceCitation", () => {
  it("muestra archivo, rango de líneas, tipo y nombre del chunk", () => {
    renderWithLanguage(baseSource());
    expect(screen.getByText("src/cartservice/src/services/CartService.cs")).toBeInTheDocument();
    expect(screen.getByText("(24-50)")).toBeInTheDocument();
    expect(screen.getByText("class · CartService")).toBeInTheDocument();
  });

  it("cuando NO es un chunk expandido por grafo de llamadas, muestra la distancia", () => {
    renderWithLanguage(baseSource({ expanded: false, distance: 0.4567 }));
    expect(screen.getByText(/dist\. 0\.457/)).toBeInTheDocument();
  });

  it("cuando SÍ es un chunk expandido por grafo de llamadas, muestra el badge en vez de la distancia", () => {
    renderWithLanguage(baseSource({ expanded: true }));
    expect(screen.getByText("grafo de llamadas")).toBeInTheDocument();
    expect(screen.queryByText(/dist\./)).not.toBeInTheDocument();
  });

  it("no muestra el nombre del chunk si viene vacío", () => {
    renderWithLanguage(baseSource({ name: "" }));
    expect(screen.getByText("class")).toBeInTheDocument();
    expect(screen.queryByText(/·/)).not.toBeInTheDocument();
  });
});
