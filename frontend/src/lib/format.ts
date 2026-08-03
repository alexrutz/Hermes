export const nf = new Intl.NumberFormat('de-DE')

export function num(value: number | null | undefined, fallback = '—'): string {
  if (value === null || value === undefined || Number.isNaN(value)) return fallback
  return nf.format(Math.round(value))
}

export function pct(value: number | null | undefined, digits = 1, fallback = '—'): string {
  if (value === null || value === undefined || Number.isNaN(value)) return fallback
  return `${(value * 100).toFixed(digits).replace('.', ',')} %`
}

export function ms(value: number | null | undefined, fallback = '—'): string {
  if (value === null || value === undefined || Number.isNaN(value)) return fallback
  if (value < 1000) return `${Math.round(value)} ms`
  return `${(value / 1000).toFixed(value < 10000 ? 2 : 1).replace('.', ',')} s`
}

export function bytes(value: number | null | undefined): string {
  if (!value) return '—'
  const units = ['B', 'KB', 'MB', 'GB']
  let index = 0
  let size = value
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024
    index += 1
  }
  return `${size.toFixed(index === 0 ? 0 : 1).replace('.', ',')} ${units[index]}`
}

/** Colour ramp for context-window pressure. Green → amber → red. */
export function budgetTone(percent: number): { bar: string; text: string } {
  if (percent >= 100) return { bar: 'bg-red-500', text: 'text-red-400' }
  if (percent >= 90) return { bar: 'bg-red-400', text: 'text-red-300' }
  if (percent >= 75) return { bar: 'bg-amber-400', text: 'text-amber-300' }
  return { bar: 'bg-accent', text: 'text-ink-400' }
}

export function groupChatsByDate<T extends { updated_at: string }>(items: T[]) {
  const now = new Date()
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const day = 86_400_000
  const buckets: { label: string; items: T[] }[] = [
    { label: 'Heute', items: [] },
    { label: 'Gestern', items: [] },
    { label: 'Letzte 7 Tage', items: [] },
    { label: 'Letzte 30 Tage', items: [] },
    { label: 'Älter', items: [] },
  ]
  for (const item of items) {
    const time = new Date(item.updated_at).getTime()
    if (time >= startOfToday) buckets[0]!.items.push(item)
    else if (time >= startOfToday - day) buckets[1]!.items.push(item)
    else if (time >= startOfToday - 7 * day) buckets[2]!.items.push(item)
    else if (time >= startOfToday - 30 * day) buckets[3]!.items.push(item)
    else buckets[4]!.items.push(item)
  }
  return buckets.filter((bucket) => bucket.items.length > 0)
}

export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return 'nie'
  const delta = (Date.now() - new Date(iso).getTime()) / 1000
  if (delta < 60) return 'gerade eben'
  if (delta < 3600) return `vor ${Math.floor(delta / 60)} min`
  if (delta < 86400) return `vor ${Math.floor(delta / 3600)} h`
  return `vor ${Math.floor(delta / 86400)} d`
}

export const STATUS_META: Record<
  string,
  { label: string; className: string; dot: string; help: string }
> = {
  cached: {
    label: 'cached',
    className: 'bg-emerald-500/12 text-emerald-300 border border-emerald-500/25',
    dot: 'bg-emerald-400',
    help: 'Der Präfix wurde ingestiert und liegt im HiRadixTree.',
  },
  stale: {
    label: 'stale',
    className: 'bg-amber-500/12 text-amber-300 border border-amber-500/25',
    dot: 'bg-amber-400',
    help: 'Der Präfix hat sich geändert. Neu ingestieren, sonst gibt es keinen Cache-Treffer.',
  },
  ingesting: {
    label: 'ingesting',
    className: 'bg-accent/12 text-accent-soft border border-accent/25',
    dot: 'bg-accent animate-shimmer',
    help: 'Der Präfix wird gerade an SGLang gesendet.',
  },
  pending: {
    label: 'pending',
    className: 'bg-ink-700/40 text-ink-300 border border-ink-700',
    dot: 'bg-ink-400',
    help: 'Noch nicht ingestiert — die erste Anfrage muss den vollen Präfix prefillen.',
  },
  error: {
    label: 'error',
    className: 'bg-red-500/12 text-red-300 border border-red-500/25',
    dot: 'bg-red-400',
    help: 'Der letzte Ingest ist fehlgeschlagen.',
  },
}

export const CACHE_LEVEL_META: Record<
  string,
  { label: string; color: string; description: string }
> = {
  L1: { label: 'L1 (GPU)', color: '#34d399', description: 'Zuletzt getroffen — vermutlich noch im GPU-Pool.' },
  L2: { label: 'L2 (Host-RAM)', color: '#60a5fa', description: 'Länger nicht getroffen — vermutlich in den Host-Pool ausgelagert.' },
  L3: { label: 'L3 (Storage)', color: '#a78bfa', description: 'Ingestiert, aber lange nicht abgefragt — vermutlich nur noch im Storage-Backend.' },
  none: { label: 'nicht gecacht', color: '#4a5568', description: 'Kein gültiger Präfix im Cache.' },
}
