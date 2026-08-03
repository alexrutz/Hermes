import { useCallback, useEffect, useRef, useState } from 'react'
import type { PageInfo } from '../lib/api'
import { bytes, num } from '../lib/format'

/**
 * Full-screen page viewer.
 *
 * Shows the exact file that was base64-encoded into the prompt — served
 * straight from disk, never a separately generated thumbnail. That is the whole
 * point: you have to be able to judge the quality the VLM actually receives.
 *
 * 1:1 means one image pixel per CSS pixel, so "is this legible to the model"
 * becomes a question you can answer by looking.
 */

const ZOOM_STEPS = [0.1, 0.15, 0.25, 0.33, 0.5, 0.66, 1, 1.5, 2, 3, 4, 6, 8]

export function Lightbox({
  pages,
  index,
  onIndexChange,
  onClose,
}: {
  pages: PageInfo[]
  index: number
  onIndexChange: (index: number) => void
  onClose: () => void
}) {
  const page = pages[index]
  const [zoom, setZoom] = useState<number | 'fit'>('fit')
  const [offset, setOffset] = useState({ x: 0, y: 0 })
  const dragging = useRef<{ x: number; y: number } | null>(null)
  const viewportRef = useRef<HTMLDivElement>(null)
  const [fitScale, setFitScale] = useState(1)

  const recomputeFit = useCallback(() => {
    const viewport = viewportRef.current
    if (!viewport || !page) return
    const scale = Math.min(
      (viewport.clientWidth - 48) / page.width,
      (viewport.clientHeight - 48) / page.height,
      1,
    )
    setFitScale(Math.max(0.05, scale))
  }, [page])

  useEffect(() => {
    recomputeFit()
    window.addEventListener('resize', recomputeFit)
    return () => window.removeEventListener('resize', recomputeFit)
  }, [recomputeFit])

  useEffect(() => {
    setZoom('fit')
    setOffset({ x: 0, y: 0 })
  }, [index])

  const scale = zoom === 'fit' ? fitScale : zoom

  const step = useCallback(
    (direction: 1 | -1) => {
      const current = zoom === 'fit' ? fitScale : zoom
      const candidates = direction > 0 ? ZOOM_STEPS : [...ZOOM_STEPS].reverse()
      const next = candidates.find((value) =>
        direction > 0 ? value > current + 0.001 : value < current - 0.001,
      )
      setZoom(next ?? current)
    },
    [zoom, fitScale],
  )

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
      else if (event.key === 'ArrowRight') onIndexChange(Math.min(pages.length - 1, index + 1))
      else if (event.key === 'ArrowLeft') onIndexChange(Math.max(0, index - 1))
      else if (event.key === '+' || event.key === '=') step(1)
      else if (event.key === '-') step(-1)
      else if (event.key === '0') setZoom('fit')
      else if (event.key === '1') {
        setZoom(1)
        setOffset({ x: 0, y: 0 })
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [index, pages.length, onClose, onIndexChange, step])

  if (!page) return null

  return (
    <div className="fixed inset-0 z-[60] bg-ink-950/97 flex flex-col animate-fade-in">
      <header className="flex items-center gap-4 px-4 py-2.5 border-b border-ink-850 shrink-0">
        <div className="min-w-0">
          <div className="text-sm font-medium text-ink-100 truncate">{page.filename}</div>
          <div className="text-2xs text-ink-500 tabular">
            Seite {page.page_number}/{page.document_pages} · Position #{page.sequence_index} im Präfix
          </div>
        </div>

        <div className="ml-auto flex items-center gap-1.5 shrink-0">
          <button className="btn-ghost !px-2" onClick={() => step(-1)} title="Herauszoomen (−)">
            −
          </button>
          <span className="text-xs tabular text-ink-300 w-16 text-center">
            {(scale * 100).toFixed(0)} %
          </span>
          <button className="btn-ghost !px-2" onClick={() => step(1)} title="Hineinzoomen (+)">
            +
          </button>
          <button
            className={`btn-ghost text-2xs ${zoom === 'fit' ? 'text-accent' : ''}`}
            onClick={() => {
              setZoom('fit')
              setOffset({ x: 0, y: 0 })
            }}
            title="Einpassen (0)"
          >
            Fit
          </button>
          <button
            className={`btn-ghost text-2xs ${zoom === 1 ? 'text-accent' : ''}`}
            onClick={() => {
              setZoom(1)
              setOffset({ x: 0, y: 0 })
            }}
            title="Originalgröße, 1 Bildpixel = 1 Bildschirmpixel (1)"
          >
            1:1
          </button>
          <div className="w-px h-5 bg-ink-800 mx-1" />
          <button className="btn-ghost !px-2" onClick={onClose} title="Schließen (Esc)">
            ✕
          </button>
        </div>
      </header>

      <div
        ref={viewportRef}
        className="flex-1 overflow-hidden relative"
        style={{ cursor: dragging.current ? 'grabbing' : scale > fitScale ? 'grab' : 'default' }}
        onMouseDown={(event) => {
          dragging.current = { x: event.clientX - offset.x, y: event.clientY - offset.y }
        }}
        onMouseMove={(event) => {
          if (!dragging.current) return
          setOffset({
            x: event.clientX - dragging.current.x,
            y: event.clientY - dragging.current.y,
          })
        }}
        onMouseUp={() => {
          dragging.current = null
        }}
        onMouseLeave={() => {
          dragging.current = null
        }}
        onWheel={(event) => {
          if (!event.ctrlKey && !event.metaKey) return
          event.preventDefault()
          step(event.deltaY < 0 ? 1 : -1)
        }}
      >
        <div className="absolute inset-0 flex items-center justify-center">
          <img
            src={page.url}
            alt={`${page.filename} Seite ${page.page_number}`}
            draggable={false}
            className="shadow-2xl bg-white select-none"
            style={{
              width: page.width * scale,
              height: page.height * scale,
              transform: `translate(${offset.x}px, ${offset.y}px)`,
              // Show the real pixels at 1:1 and above instead of smoothing them
              // away — smoothing would hide exactly the artefacts we came to see.
              imageRendering: scale >= 1 ? 'pixelated' : 'auto',
            }}
          />
        </div>

        {index > 0 && (
          <button
            className="absolute left-3 top-1/2 -translate-y-1/2 btn-outline !px-3 !py-6 bg-ink-900/80"
            onClick={() => onIndexChange(index - 1)}
            title="Vorherige Seite (←)"
          >
            ‹
          </button>
        )}
        {index < pages.length - 1 && (
          <button
            className="absolute right-3 top-1/2 -translate-y-1/2 btn-outline !px-3 !py-6 bg-ink-900/80"
            onClick={() => onIndexChange(index + 1)}
            title="Nächste Seite (→)"
          >
            ›
          </button>
        )}
      </div>

      <footer className="shrink-0 border-t border-ink-850">
        <div className="flex items-center gap-6 px-4 py-2 text-2xs tabular text-ink-400">
          <span>
            <span className="text-ink-500">Pixel </span>
            {num(page.width)} × {num(page.height)}
          </span>
          <span>
            <span className="text-ink-500">DPI </span>
            {page.dpi}
          </span>
          <span>
            <span className="text-ink-500">Datei </span>
            {bytes(page.byte_size)} {page.mime_type.replace('image/', '').toUpperCase()}
          </span>
          <span title="Geschätzte Vision-Tokens für dieses Bild">
            <span className="text-ink-500">Tokens ~</span>
            {num(page.estimated_tokens)}
          </span>
          <span className="font-mono text-ink-600" title="SHA-256 der Bilddatei">
            {page.image_hash.slice(0, 12)}
          </span>
          <span className="ml-auto text-ink-600">
            Exakt dieses Bild geht an das Modell — keine separate Vorschau.
          </span>
        </div>

        <div className="flex gap-1.5 px-4 pb-2.5 overflow-x-auto">
          {pages.map((thumb, thumbIndex) => (
            <button
              key={thumb.id}
              onClick={() => onIndexChange(thumbIndex)}
              className={`shrink-0 rounded border-2 overflow-hidden transition-colors ${
                thumbIndex === index ? 'border-accent' : 'border-ink-800 hover:border-ink-600'
              }`}
              title={`${thumb.filename} — Seite ${thumb.page_number}`}
            >
              <img src={thumb.url} alt="" className="h-14 w-auto bg-white" loading="lazy" />
            </button>
          ))}
        </div>
      </footer>
    </div>
  )
}
