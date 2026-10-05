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

// jsdom has no ResizeObserver, and `Chart` uses one to size the plot from
// its container -- which is the mechanism that fixed charts being sized by
// their cardinality. A stub that never fires is correct here: these tests
// assert report structure, and the plot's measured width is asserted in the
// browser suite, where there is a real layout to measure.
if (!('ResizeObserver' in globalThis)) {
  class StubResizeObserver {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  }
  Object.defineProperty(globalThis, 'ResizeObserver', {
    value: StubResizeObserver,
    writable: true,
  })
}
