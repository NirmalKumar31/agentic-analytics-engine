import '@testing-library/jest-dom/vitest'

// jsdom has no EventSource; tests that need the stream install their own.
if (!('EventSource' in globalThis)) {
  class MissingEventSource {
    static readonly CLOSED = 2
    readonly readyState = MissingEventSource.CLOSED
    addEventListener(): void {}
    removeEventListener(): void {}
    close(): void {}
    onerror: (() => void) | null = null
  }
  Object.defineProperty(globalThis, 'EventSource', {
    value: MissingEventSource,
    writable: true,
  })
}
