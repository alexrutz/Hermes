import { useEffect, useRef, useState, type ReactNode } from 'react'
import { STATUS_META, budgetTone, pct } from '../lib/format'

export function StatusBadge({ status, className = '' }: { status: string; className?: string }) {
  const meta = STATUS_META[status] ?? STATUS_META.pending!
  return (
    <span className={`badge ${meta.className} ${className}`} title={meta.help}>
      <span className={`w-1.5 h-1.5 rounded-full ${meta.dot}`} />
      {meta.label}
    </span>
  )
}

export function TokenBar({
  percent,
  className = '',
  showLabel = false,
}: {
  percent: number
  className?: string
  showLabel?: boolean
}) {
  const tone = budgetTone(percent)
  return (
    <div className={`flex items-center gap-2 ${className}`}>
      <div className="flex-1 h-1.5 rounded-full bg-ink-800 overflow-hidden">
        <div
          className={`h-full rounded-full transition-[width] duration-500 ${tone.bar}`}
          style={{ width: `${Math.min(100, Math.max(2, percent))}%` }}
        />
      </div>
      {showLabel && (
        <span className={`text-2xs tabular ${tone.text}`}>{percent.toFixed(0)} %</span>
      )}
    </div>
  )
}

export function Disclosure({
  title,
  meta,
  children,
  defaultOpen = false,
  tone = 'default',
}: {
  title: ReactNode
  meta?: ReactNode
  children: ReactNode
  defaultOpen?: boolean
  tone?: 'default' | 'thinking'
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div
      className={`rounded-lg border ${
        tone === 'thinking' ? 'border-ink-800 bg-ink-900/50' : 'border-ink-800 bg-ink-900'
      }`}
    >
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-ink-850/60 rounded-lg transition-colors"
      >
        <svg
          className={`w-3 h-3 shrink-0 text-ink-500 transition-transform ${open ? 'rotate-90' : ''}`}
          viewBox="0 0 12 12"
          fill="currentColor"
        >
          <path d="M4 2l4 4-4 4z" />
        </svg>
        <span className="text-xs font-medium text-ink-200 truncate">{title}</span>
        <span className="ml-auto flex items-center gap-2 text-2xs text-ink-500 tabular shrink-0">
          {meta}
        </span>
      </button>
      {open && <div className="px-3 pb-3 pt-1 animate-fade-in">{children}</div>}
    </div>
  )
}

export function Modal({
  open,
  onClose,
  title,
  subtitle,
  children,
  wide = false,
  footer,
}: {
  open: boolean
  onClose: () => void
  title: string
  subtitle?: string
  children: ReactNode
  wide?: boolean
  footer?: ReactNode
}) {
  useEffect(() => {
    if (!open) return
    const handler = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [open, onClose])

  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-ink-950/80 backdrop-blur-sm animate-fade-in" onClick={onClose} />
      <div
        className={`relative panel shadow-2xl w-full ${wide ? 'max-w-5xl' : 'max-w-lg'} max-h-[88vh] flex flex-col animate-slide-up`}
      >
        <header className="flex items-start justify-between gap-4 px-5 py-4 border-b border-ink-800">
          <div className="min-w-0">
            <h2 className="text-base font-semibold text-ink-100 tracking-tight">{title}</h2>
            {subtitle && <p className="text-xs text-ink-400 mt-0.5">{subtitle}</p>}
          </div>
          <button type="button" className="btn-ghost !px-2" onClick={onClose} aria-label="Schließen">
            ✕
          </button>
        </header>
        <div className="overflow-y-auto px-5 py-4 flex-1">{children}</div>
        {footer && (
          <footer className="flex items-center justify-end gap-2 px-5 py-3 border-t border-ink-800">
            {footer}
          </footer>
        )}
      </div>
    </div>
  )
}

export function ConfirmDialog({
  open,
  title,
  body,
  confirmLabel = 'Bestätigen',
  danger = false,
  onConfirm,
  onCancel,
}: {
  open: boolean
  title: string
  body: ReactNode
  confirmLabel?: string
  danger?: boolean
  onConfirm: () => void
  onCancel: () => void
}) {
  return (
    <Modal
      open={open}
      onClose={onCancel}
      title={title}
      footer={
        <>
          <button type="button" className="btn-ghost" onClick={onCancel}>
            Abbrechen
          </button>
          <button
            type="button"
            className={danger ? 'btn-danger' : 'btn-primary'}
            onClick={onConfirm}
          >
            {confirmLabel}
          </button>
        </>
      }
    >
      <div className="text-sm text-ink-300 leading-relaxed">{body}</div>
    </Modal>
  )
}

export function Stat({
  label,
  value,
  hint,
  tone = 'default',
}: {
  label: string
  value: ReactNode
  hint?: string
  tone?: 'default' | 'good' | 'warn' | 'bad'
}) {
  const toneClass = {
    default: 'text-ink-100',
    good: 'text-emerald-300',
    warn: 'text-amber-300',
    bad: 'text-red-300',
  }[tone]
  return (
    <div className="panel px-3 py-2.5" title={hint}>
      <div className="text-2xs uppercase tracking-wider text-ink-500">{label}</div>
      <div className={`mt-1 text-lg font-semibold tabular ${toneClass}`}>{value}</div>
    </div>
  )
}

export function HitRatePill({ rate, matched }: { rate: number; matched?: boolean }) {
  const tone =
    rate >= 0.85
      ? 'bg-emerald-500/12 text-emerald-300 border-emerald-500/25'
      : rate >= 0.4
        ? 'bg-amber-500/12 text-amber-300 border-amber-500/25'
        : 'bg-red-500/12 text-red-300 border-red-500/25'
  return (
    <span
      className={`badge border ${tone}`}
      title={
        matched === false
          ? 'Der Präfix wich beim Absenden von dem ab, was ingestiert wurde — dieser Treffer ist deshalb niedrig.'
          : 'cached_tokens / prompt_tokens, gemeldet vom Server. Gemessen, nicht geschätzt.'
      }
    >
      {pct(rate, 0)} Cache
    </span>
  )
}

export function useAutoScroll<T extends HTMLElement>(dependency: unknown) {
  const ref = useRef<T | null>(null)
  const pinned = useRef(true)

  useEffect(() => {
    const element = ref.current
    if (!element) return
    const onScroll = () => {
      const distance = element.scrollHeight - element.scrollTop - element.clientHeight
      pinned.current = distance < 120
    }
    element.addEventListener('scroll', onScroll, { passive: true })
    return () => element.removeEventListener('scroll', onScroll)
  }, [])

  useEffect(() => {
    const element = ref.current
    if (element && pinned.current) element.scrollTop = element.scrollHeight
  }, [dependency])

  return ref
}

export function Spinner({ className = '' }: { className?: string }) {
  return (
    <svg className={`animate-spin w-3.5 h-3.5 ${className}`} viewBox="0 0 24 24" fill="none">
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" opacity="0.2" />
      <path d="M22 12a10 10 0 0 1-10 10" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  )
}

export function EmptyState({ title, body, action }: { title: string; body: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center text-center py-16 px-6">
      <h3 className="text-sm font-medium text-ink-200">{title}</h3>
      <p className="mt-1.5 text-xs text-ink-500 max-w-sm leading-relaxed">{body}</p>
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}
