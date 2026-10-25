// Overview summaries as sources: no line range and no distance.
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import SourceCitation from "../src/components/SourceCitation";
import { LanguageProvider } from "../src/components/LanguageSelector";
import type { SourceItem } from "../src/lib/types";

const base: SourceItem = { file_path: "src/app.py", chunk_type: "function", name: "run", start_line: 3, end_line: 9, distance: 0.25, expanded: false, code: "def run(): pass" };

const show = (source: SourceItem) => render(<LanguageProvider><SourceCitation source={source} /></LanguageProvider>);

describe("SourceCitation", () => {
  it("shows the line range and the distance of a code chunk", () => {
    show(base);
    expect(screen.getByText("(3-9)")).toBeInTheDocument();
    expect(screen.getByText(/0\.250/)).toBeInTheDocument();
  });

  it("shows an overview summary without lines or distance", () => {
    show({ ...base, file_path: "resumen del proyecto", chunk_type: "overview", name: "", start_line: 0, end_line: 0, distance: -1, code: "Resumen." });
    expect(screen.getByText("resumen del proyecto")).toBeInTheDocument();
    expect(screen.getByText(/^(resumen|summary)$/)).toBeInTheDocument();
    expect(screen.queryByText("(0-0)")).not.toBeInTheDocument();
    expect(screen.queryByText(/-1\.000/)).not.toBeInTheDocument();
  });
});
