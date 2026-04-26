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
 * `ResizeObserver` polyfill for the test environment.
 *
 * jsdom does not implement `ResizeObserver`. Libraries like `@xyflow/react`
 * use it internally to measure mounted node dimensions before un-hiding
 * them — without a working observer, every node stays `visibility: hidden`
 * and `@testing-library` queries can't find them.
 *
 * The polyfill fires its callback synchronously on `observe()` with stub
 * dimensions matching our hardcoded card size. That's enough to release
 * xyflow's visibility lock; we don't actually rely on the dimensions in
 * any assertion.
 */
const STUB_RECT: DOMRectReadOnly = {
  x: 0,
  y: 0,
  width: 168,
  height: 52,
  top: 0,
  left: 0,
  right: 168,
  bottom: 52,
  toJSON() {
    return this
  },
}

class FakeResizeObserver implements ResizeObserver {
  constructor(private cb: ResizeObserverCallback) {}
  observe(target: Element): void {
    this.cb(
      [
        {
          target,
          contentRect: STUB_RECT,
          borderBoxSize: [{ inlineSize: 168, blockSize: 52 }],
          contentBoxSize: [{ inlineSize: 168, blockSize: 52 }],
          devicePixelContentBoxSize: [{ inlineSize: 168, blockSize: 52 }],
        },
      ],
      this,
    )
  }
  unobserve(): void {}
  disconnect(): void {}
}

;(globalThis as unknown as { ResizeObserver: typeof FakeResizeObserver }).ResizeObserver =
  FakeResizeObserver

if (typeof window !== 'undefined') {
  ;(window as unknown as { ResizeObserver: typeof FakeResizeObserver }).ResizeObserver =
    FakeResizeObserver
}

/**
 * `DOMMatrixReadOnly` stub for the test environment.
 *
 * jsdom does not implement `DOMMatrixReadOnly`. xyflow's
 * `updateNodeInternals` constructs one from a CSS transform string to
 * read translation/scale values. We parse `matrix(a,b,c,d,e,f)` and
 * `translate(x,y)` (the two forms xyflow emits) and expose the
 * standard 2D matrix fields plus the `m41`/`m42` translation slots.
 */
class FakeDOMMatrixReadOnly {
  a = 1
  b = 0
  c = 0
  d = 1
  e = 0
  f = 0
  m11 = 1
  m12 = 0
  m13 = 0
  m14 = 0
  m21 = 0
  m22 = 1
  m23 = 0
  m24 = 0
  m31 = 0
  m32 = 0
  m33 = 1
  m34 = 0
  m41 = 0
  m42 = 0
  m43 = 0
  m44 = 1
  is2D = true
  isIdentity = true

  constructor(init?: string | number[]) {
    if (typeof init === 'string') {
      const matrix = /matrix\(([^)]+)\)/.exec(init)
      if (matrix) {
        const parts = matrix[1].split(',').map((v) => parseFloat(v.trim()))
        const [a = 1, b = 0, c = 0, d = 1, e = 0, f = 0] = parts
        Object.assign(this, {
          a,
          b,
          c,
          d,
          e,
          f,
          m11: a,
          m12: b,
          m21: c,
          m22: d,
          m41: e,
          m42: f,
        })
      }
      const translate = /translate\(([^)]+)\)/.exec(init)
      if (translate) {
        const parts = translate[1].split(',').map((v) => parseFloat(v.trim()))
        const [x = 0, y = 0] = parts
        this.e = x
        this.m41 = x
        this.f = y
        this.m42 = y
      }
    } else if (Array.isArray(init) && init.length === 6) {
      const [a, b, c, d, e, f] = init
      Object.assign(this, {
        a,
        b,
        c,
        d,
        e,
        f,
        m11: a,
        m12: b,
        m21: c,
        m22: d,
        m41: e,
        m42: f,
      })
    }
  }
}

;(globalThis as unknown as { DOMMatrixReadOnly: typeof FakeDOMMatrixReadOnly }).DOMMatrixReadOnly =
  FakeDOMMatrixReadOnly

if (typeof window !== 'undefined') {
  ;(window as unknown as { DOMMatrixReadOnly: typeof FakeDOMMatrixReadOnly }).DOMMatrixReadOnly =
    FakeDOMMatrixReadOnly
}
