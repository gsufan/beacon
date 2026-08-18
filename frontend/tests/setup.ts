import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";
import "@testing-library/jest-dom/vitest";

// Node 22+ define un `localStorage` global propio (Web Storage API nativa)
// que pisa al de jsdom pero no funciona sin el flag `--localstorage-file`
// (queda como stub roto: "localStorage.getItem is not a function"). Se
// reemplaza por una implementación en memoria simple para no depender de
// esa flag ni de qué versión de Node/jsdom corre en cada máquina o en CI.
class MemoryStorage implements Storage {
  private store = new Map<string, string>();

  get length() {
    return this.store.size;
  }

  clear() {
    this.store.clear();
  }

  getItem(key: string) {
    return this.store.has(key) ? this.store.get(key)! : null;
  }

  key(index: number) {
    return Array.from(this.store.keys())[index] ?? null;
  }

  removeItem(key: string) {
    this.store.delete(key);
  }

  setItem(key: string, value: string) {
    this.store.set(key, String(value));
  }
}

Object.defineProperty(globalThis, "localStorage", {
  value: new MemoryStorage(),
  writable: true,
  configurable: true,
});

// No usamos `test.globals: true`, así que @testing-library/react no detecta
// automáticamente un framework de test para desmontar entre pruebas —
// sin esto, cada render() se acumula y los queries por rol/texto empiezan
// a matchear contra el DOM de tests anteriores.
afterEach(() => {
  cleanup();
});
