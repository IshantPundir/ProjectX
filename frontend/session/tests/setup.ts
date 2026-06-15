import '@testing-library/jest-dom/vitest'
import { vi } from 'vitest'

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

/**
 * `ResizeObserver` and `IntersectionObserver` polyfills. jsdom doesn't
 * implement these, but @radix-ui primitives and motion/react both call
 * them on mount — without stubs, every test that mounts a Radix-backed
 * component throws.
 */
if (typeof globalThis.ResizeObserver === 'undefined') {
  globalThis.ResizeObserver = class {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  } as unknown as typeof ResizeObserver
}

if (typeof globalThis.IntersectionObserver === 'undefined') {
  globalThis.IntersectionObserver = class {
    readonly root = null
    readonly rootMargin = ''
    readonly thresholds: ReadonlyArray<number> = []
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
    takeRecords(): IntersectionObserverEntry[] {
      return []
    }
  } as unknown as typeof IntersectionObserver
}

/**
 * `getUserMedia` polyfill. jsdom does not implement
 * `navigator.mediaDevices`. ReadyStage + the LiveKit publish path
 * touch it on mount; tests that don't explicitly exercise device
 * selection get a sane no-op default and can override per-test.
 */
if (typeof navigator !== 'undefined' && !navigator.mediaDevices) {
  Object.defineProperty(navigator, 'mediaDevices', {
    value: {
      getUserMedia: vi.fn().mockResolvedValue({
        getTracks: () => [],
        getVideoTracks: () => [],
        getAudioTracks: () => [],
      }),
      enumerateDevices: vi.fn().mockResolvedValue([]),
      addEventListener: () => {},
      removeEventListener: () => {},
    },
    writable: true,
    configurable: true,
  })
}
