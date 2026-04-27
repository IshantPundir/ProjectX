import '@testing-library/jest-dom/vitest'

/**
 * In-memory `Storage` polyfill for the test environment.
 *
 * On Node 25 + Vitest 4, Node's `--experimental-webstorage` flag installs
 * a no-op `localStorage`/`sessionStorage` stub that shadows jsdom's
 * implementation. The result: `window.localStorage` is an empty object
 * with no `getItem`/`setItem`/`clear` methods, breaking any test that
 * exercises Storage-backed code.
 *
 * This polyfill replaces both storages with a class-backed in-memory
 * implementation. Exposing `globalThis.Storage = StoragePolyfill` lets
 * tests monkey-patch `Storage.prototype.setItem` to simulate quota
 * errors, since both instances inherit from the same prototype.
 */
class StoragePolyfill {
  private store: Map<string, string> = new Map()

  get length(): number {
    return this.store.size
  }

  clear(): void {
    this.store.clear()
  }

  getItem(key: string): string | null {
    return this.store.get(key) ?? null
  }

  key(index: number): string | null {
    return Array.from(this.store.keys())[index] ?? null
  }

  removeItem(key: string): void {
    this.store.delete(key)
  }

  setItem(key: string, value: string): void {
    this.store.set(key, String(value))
  }
}

if (typeof window !== 'undefined') {
  Object.defineProperty(window, 'localStorage', {
    value: new StoragePolyfill(),
    writable: true,
    configurable: true,
  })
  Object.defineProperty(window, 'sessionStorage', {
    value: new StoragePolyfill(),
    writable: true,
    configurable: true,
  })
}

;(globalThis as unknown as { Storage: typeof StoragePolyfill }).Storage =
  StoragePolyfill

/**
 * `window.matchMedia` polyfill. jsdom does not implement it, but app
 * code (and GSAP-driven motion gating) calls it to honor
 * `prefers-reduced-motion`. The stub always reports "not matched",
 * which is correct: tests run with motion enabled.
 */
if (typeof window !== 'undefined' && typeof window.matchMedia !== 'function') {
  window.matchMedia = (query: string): MediaQueryList => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }) as unknown as MediaQueryList
}
