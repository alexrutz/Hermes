import '@testing-library/jest-dom/vitest'

// jsdom has no clipboard and no ResizeObserver; both are used by UI chrome that
// the markdown tests render through.
Object.defineProperty(navigator, 'clipboard', {
  value: { writeText: async () => {} },
  writable: true,
})

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
;(globalThis as any).ResizeObserver ??= ResizeObserverStub
