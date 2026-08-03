import { useEffect, useMemo, useRef, useState } from 'react'
import * as d3 from 'd3'
import { api, type CacheTree, type MetricsSummary, type TreeNode } from '../lib/api'
import { CACHE_LEVEL_META, num, pct, relativeTime } from '../lib/format'
import { ConfirmDialog, Modal, Spinner, Stat } from './ui'

/**
 * HiRadixTree overview.
 *
 * SGLang has no endpoint that dumps its radix tree, so the structure here is
 * reconstructed from our own ingest metadata — it mirrors the prefix we send,
 * which is what the server must build, but we never observe the server's actual
 * nodes. The UI labels every value as *gemessen* (measured) or *abgeleitet*
 * (derived) rather than implying a precision that does not exist.
 */

interface LayoutNode extends d3.HierarchyNode<TreeNode> {
  x: number
  y: number
}

export function CacheTreeView({
  open,
  onClose,
  onPageOpen,
}: {
  open: boolean
  onClose: () => void
  onPageOpen: (collectionId: string, pageId: string) => void
}) {
  const [tree, setTree] = useState<CacheTree | null>(null)
  const [metrics, setMetrics] = useState<MetricsSummary | null>(null)
  const [metricsAvailable, setMetricsAvailable] = useState(true)
  const [loading, setLoading] = useState(false)
  const [hovered, setHovered] = useState<TreeNode | null>(null)
  const [flushOpen, setFlushOpen] = useState(false)

  const refresh = async () => {
    setLoading(true)
    try {
      const data = await api.cacheDashboard()
      setTree(data.tree)
      setMetrics(data.metrics)
      setMetricsAvailable(data.metrics_available)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (!open) return
    void refresh()
    const timer = setInterval(refresh, 5000)
    return () => clearInterval(timer)
  }, [open])

  return (
    <Modal
      open={open}
      onClose={onClose}
      wide
      title="Cache-Baum & Live-Metriken"
      subtitle="Struktur abgeleitet aus den Ingest-Metadaten · Zustand aus /metrics und gemessenen Trefferquoten"
      footer={
        <>
          <span className="mr-auto text-2xs text-ink-600">
            {loading ? 'Aktualisiere …' : 'Aktualisiert sich alle 5 s'}
          </span>
          <button className="btn-danger" onClick={() => setFlushOpen(true)}>
            Cache leeren
          </button>
          <button className="btn-ghost" onClick={onClose}>
            Schließen
          </button>
        </>
      }
    >
      {!tree ? (
        <div className="flex items-center justify-center py-16 text-ink-500 gap-2">
          <Spinner /> Lade …
        </div>
      ) : (
        <div className="space-y-4">
          <MetricsDashboard metrics={metrics} available={metricsAvailable} tree={tree} />

          <div className="rounded-lg border border-ink-800 bg-ink-950 relative overflow-hidden">
            <TreeCanvas
              tree={tree}
              onHover={setHovered}
              onPageClick={(node, collectionId) => onPageOpen(collectionId, node.id)}
            />
            {hovered && <NodeTooltip node={hovered} />}
          </div>

          <Legend />
          <Provenance provenance={tree.provenance} />
        </div>
      )}

      <ConfirmDialog
        open={flushOpen}
        danger
        title="Server-Cache leeren?"
        body={
          <div className="space-y-2">
            <p>
              <code className="text-ink-100">POST /flush_cache?timeout=30</code> verwirft den
              kompletten Radix-Cache der SGLang-Instanz — L1, L2 und L3.
            </p>
            <p className="text-amber-300">
              Alle Sammlungen werden danach als <span className="font-mono">stale</span> markiert
              und müssen neu ingestiert werden. Das erste Prefill danach dauert wieder Minuten.
            </p>
          </div>
        }
        confirmLabel="Cache leeren"
        onCancel={() => setFlushOpen(false)}
        onConfirm={async () => {
          const result = await api.flushCache()
          setFlushOpen(false)
          if (!result.ok) alert(result.message)
          await refresh()
        }}
      />
    </Modal>
  )
}

function MetricsDashboard({
  metrics,
  available,
  tree,
}: {
  metrics: MetricsSummary | null
  available: boolean
  tree: CacheTree
}) {
  if (!metrics) {
    return (
      <div className="rounded-lg border border-amber-500/25 bg-amber-500/[0.06] px-3 py-2 text-xs text-amber-200">
        Keine Metriken erreichbar. Läuft die Instanz mit <code>--enable-metrics</code>, und
        stimmen Base-URL und Passwort?
      </div>
    )
  }

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-4 gap-2">
        <Stat
          label="Sammlungen gecacht"
          value={`${tree.totals.cached_collections}/${tree.totals.collections}`}
          hint="Sammlungen mit gültigem, ingestiertem Präfix (gemessen)"
        />
        <Stat
          label="Seiten im Baum"
          value={num(tree.totals.pages)}
          hint="Summe aller Seiten über alle Sammlungen (gemessen)"
        />
        <Stat
          label="Tokens im Baum"
          value={num(tree.totals.tokens)}
          hint="Geschätzte Präfix-Tokens insgesamt (abgeleitet)"
        />
        <Stat
          label="Hit-Rate (Server, kumulativ)"
          value={pct(metrics.cache_hit_rate, 1)}
          tone={
            (metrics.cache_hit_rate ?? 0) >= 0.7
              ? 'good'
              : (metrics.cache_hit_rate ?? 0) >= 0.3
                ? 'warn'
                : 'bad'
          }
          hint="sglang:cache_hit_rate seit Serverstart (gemessen)"
        />
      </div>

      <div className="grid grid-cols-4 gap-2">
        <Stat
          label="L2 Host-Pool"
          value={
            metrics.hicache_host_total_tokens
              ? pct(metrics.hicache_host_utilization, 1)
              : 'n. v.'
          }
          hint={
            available
              ? `sglang:hicache_host_used_tokens ${num(
                  metrics.hicache_host_used_tokens,
                )} von ${num(metrics.hicache_host_total_tokens)} (gemessen)`
              : 'Die HiCache-Gauges fehlen — läuft der Server mit --enable-hierarchical-cache?'
          }
          tone={available ? 'default' : 'warn'}
        />
        <Stat
          label="KV-Pool Auslastung"
          value={pct(metrics.token_usage, 1)}
          hint={`sglang:num_used_tokens ${num(metrics.num_used_tokens)} von ${num(
            metrics.max_total_num_tokens,
          )} (gemessen)`}
        />
        <Stat
          label="TTFT ⌀"
          value={
            metrics.ttft_seconds_avg !== null
              ? `${metrics.ttft_seconds_avg.toFixed(2).replace('.', ',')} s`
              : '—'
          }
          hint="Mittel aus dem Histogramm sglang:time_to_first_token_seconds (gemessen)"
        />
        <Stat
          label="Requests laufend / Queue"
          value={`${num(metrics.num_running_reqs, '0')} / ${num(metrics.num_queue_reqs, '0')}`}
          hint="gemessen"
        />
      </div>
    </div>
  )
}

function TreeCanvas({
  tree,
  onHover,
  onPageClick,
}: {
  tree: CacheTree
  onHover: (node: TreeNode | null) => void
  onPageClick: (node: TreeNode, collectionId: string) => void
}) {
  const svgRef = useRef<SVGSVGElement>(null)
  const width = 900
  const [expanded, setExpanded] = useState<Set<string>>(new Set(['root']))

  // Collapse page-level nodes by default: a 200-page collection would otherwise
  // render 200 leaves and drown everything else.
  const visibleTree = useMemo(() => {
    const prune = (node: TreeNode): TreeNode => {
      const isExpanded = expanded.has(node.id) || node.type === 'root'
      if (!node.children?.length) return node
      if (!isExpanded) return { ...node, children: [] }
      return { ...node, children: node.children.map(prune) }
    }
    return prune(tree.root)
  }, [tree, expanded])

  const { nodes, links, height } = useMemo(() => {
    const root = d3.hierarchy(visibleTree)
    const leafCount = root.leaves().length
    const computedHeight = Math.max(320, leafCount * 24 + 60)
    d3.tree<TreeNode>().size([computedHeight - 40, width - 320])(root)
    return {
      nodes: root.descendants() as LayoutNode[],
      links: root.links(),
      height: computedHeight,
    }
  }, [visibleTree])

  const maxTokens = Math.max(1, ...nodes.map((n) => n.data.tokens))
  const radius = (tokens: number) => 4 + 11 * Math.sqrt(tokens / maxTokens)

  const collectionOf = (node: LayoutNode): string => {
    let current: LayoutNode | null = node
    while (current && current.data.type !== 'collection') current = current.parent as LayoutNode
    return current?.data.id ?? ''
  }

  return (
    <div className="overflow-auto max-h-[26rem]">
      <svg ref={svgRef} width={width} height={height} className="block">
        <g transform="translate(140, 20)">
          {links.map((link, index) => {
            const source = link.source as LayoutNode
            const target = link.target as LayoutNode
            return (
              <path
                key={index}
                d={`M${source.y},${source.x}C${(source.y + target.y) / 2},${source.x} ${
                  (source.y + target.y) / 2
                },${target.x} ${target.y},${target.x}`}
                fill="none"
                stroke={CACHE_LEVEL_META[target.data.cache_level]?.color ?? '#4a5568'}
                strokeOpacity={0.28}
                strokeWidth={1.5}
              />
            )
          })}

          {nodes.map((node) => {
            const meta = CACHE_LEVEL_META[node.data.cache_level] ?? CACHE_LEVEL_META.none!
            const hasHiddenChildren =
              (tree.root === node.data ? false : true) &&
              !expanded.has(node.data.id) &&
              node.data.type !== 'page' &&
              node.data.type !== 'root'
            return (
              <g
                key={node.data.id}
                transform={`translate(${node.y},${node.x})`}
                className="cursor-pointer"
                onMouseEnter={() => onHover(node.data)}
                onMouseLeave={() => onHover(null)}
                onClick={() => {
                  if (node.data.type === 'page') {
                    onPageClick(node.data, collectionOf(node))
                    return
                  }
                  setExpanded((current) => {
                    const next = new Set(current)
                    if (next.has(node.data.id)) next.delete(node.data.id)
                    else next.add(node.data.id)
                    return next
                  })
                }}
              >
                <circle
                  r={radius(node.data.tokens)}
                  fill={meta.color}
                  fillOpacity={node.data.cache_level === 'none' ? 0.25 : 0.75}
                  stroke={meta.color}
                  strokeWidth={1.5}
                />
                {hasHiddenChildren && (
                  <circle
                    r={radius(node.data.tokens) + 3.5}
                    fill="none"
                    stroke={meta.color}
                    strokeOpacity={0.4}
                    strokeDasharray="2 2"
                  />
                )}
                <text
                  x={node.children ? -radius(node.data.tokens) - 6 : radius(node.data.tokens) + 6}
                  dy="0.32em"
                  textAnchor={node.children ? 'end' : 'start'}
                  className="fill-ink-300 text-[10px] font-medium"
                >
                  {node.data.label.length > 30
                    ? `${node.data.label.slice(0, 29)}…`
                    : node.data.label}
                </text>
                <text
                  x={node.children ? -radius(node.data.tokens) - 6 : radius(node.data.tokens) + 6}
                  dy="1.55em"
                  textAnchor={node.children ? 'end' : 'start'}
                  className="fill-ink-600 text-[9px] tabular"
                >
                  {num(node.data.tokens)} Tok
                </text>
              </g>
            )
          })}
        </g>
      </svg>
    </div>
  )
}

function NodeTooltip({ node }: { node: TreeNode }) {
  const meta = CACHE_LEVEL_META[node.cache_level] ?? CACHE_LEVEL_META.none!
  return (
    <div className="absolute bottom-2 right-2 w-72 panel !bg-ink-900/97 p-3 text-xs pointer-events-none shadow-xl">
      <div className="font-medium text-ink-100 truncate">{node.label}</div>
      <div className="mt-0.5 text-2xs text-ink-500 uppercase tracking-wide">{node.type}</div>
      <dl className="mt-2 space-y-1 text-2xs">
        <Row label="Tokens" value={num(node.tokens)} basis="abgeleitet" />
        <Row label="Offset im Präfix" value={num(node.token_offset)} basis="abgeleitet" />
        <Row
          label="Cache-Level"
          value={<span style={{ color: meta.color }}>{meta.label}</span>}
          basis={node.cache_level_basis}
        />
        {node.measured_hit_rate !== undefined && node.measured_hit_rate !== null && (
          <Row
            label="Trefferquote"
            value={pct(node.measured_hit_rate, 1)}
            basis="gemessen"
            extra={relativeTime(node.measured_at)}
          />
        )}
        {node.status && <Row label="Status" value={node.status} basis="gemessen" />}
        {node.width ? (
          <Row label="Pixel" value={`${node.width} × ${node.height}`} basis="gemessen" />
        ) : null}
        {node.pages ? <Row label="Seiten" value={num(node.pages)} basis="gemessen" /> : null}
      </dl>
      {node.type === 'page' && (
        <p className="mt-2 text-2xs text-accent-soft">Klicken öffnet die Bildvorschau.</p>
      )}
      {node.status_detail && (
        <p className="mt-2 text-2xs text-amber-300 leading-snug">{node.status_detail}</p>
      )}
    </div>
  )
}

function Row({
  label,
  value,
  basis,
  extra,
}: {
  label: string
  value: React.ReactNode
  basis: string
  extra?: string
}) {
  return (
    <div className="flex items-baseline gap-2">
      <dt className="text-ink-500 w-28 shrink-0">{label}</dt>
      <dd className="text-ink-200 tabular flex-1">{value}</dd>
      <dd
        className={`text-[9px] uppercase tracking-wide shrink-0 ${
          basis === 'gemessen' ? 'text-emerald-500/70' : 'text-ink-600'
        }`}
        title={
          basis === 'gemessen'
            ? 'Vom Server gemeldet oder direkt gezählt.'
            : 'Aus eigenen Metadaten geschätzt, nicht beobachtet.'
        }
      >
        {basis}
        {extra ? ` · ${extra}` : ''}
      </dd>
    </div>
  )
}

function Legend() {
  return (
    <div className="flex flex-wrap items-center gap-4 text-2xs text-ink-400">
      <span className="text-ink-500 uppercase tracking-wider">Einfärbung</span>
      {Object.entries(CACHE_LEVEL_META).map(([key, meta]) => (
        <span key={key} className="flex items-center gap-1.5" title={meta.description}>
          <span
            className="w-2.5 h-2.5 rounded-full"
            style={{ backgroundColor: meta.color, opacity: key === 'none' ? 0.35 : 0.8 }}
          />
          {meta.label}
        </span>
      ))}
      <span className="ml-auto text-ink-600">
        Knotengröße ∝ √Tokenzahl · gestrichelter Ring = eingeklappte Kinder
      </span>
    </div>
  )
}

function Provenance({ provenance }: { provenance: Record<string, string> }) {
  return (
    <div className="rounded-lg border border-ink-800 bg-ink-950 p-3 space-y-1.5">
      <h4 className="text-2xs font-semibold uppercase tracking-wider text-ink-400">
        Was gemessen ist und was abgeleitet
      </h4>
      {[
        ['Struktur', provenance.structure, provenance.structure_note],
        ['Cache-Level', provenance.cache_level, provenance.cache_level_note],
        ['Trefferquote', provenance.hit_rate, provenance.hit_rate_note],
      ].map(([label, basis, note]) => (
        <p key={label} className="text-2xs text-ink-500 leading-relaxed">
          <span className="text-ink-300">{label}: </span>
          <span
            className={basis === 'gemessen' ? 'text-emerald-400' : 'text-amber-400'}
          >
            {basis}
          </span>
          {' — '}
          {note}
        </p>
      ))}
    </div>
  )
}
