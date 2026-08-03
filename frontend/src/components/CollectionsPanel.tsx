import { useCallback, useEffect, useRef, useState } from 'react'
import {
  api,
  subscribeProgress,
  type Collection,
  type DocumentInfo,
  type PageInfo,
} from '../lib/api'
import { num, relativeTime } from '../lib/format'
import { Lightbox } from './Lightbox'
import { ConfirmDialog, Modal, Spinner, StatusBadge, TokenBar } from './ui'

interface Props {
  collections: Collection[]
  selected: string[]
  onToggle: (id: string) => void
  onRefresh: () => void
}

export function CollectionsPanel({ collections, selected, onToggle, onRefresh }: Props) {
  const [creating, setCreating] = useState(false)
  const [detailId, setDetailId] = useState<string | null>(null)

  return (
    <aside className="w-[21rem] shrink-0 border-l border-ink-850 bg-ink-900 flex flex-col">
      <header className="px-4 py-3 border-b border-ink-850 flex items-center gap-2">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-ink-400">
          Sammlungen
        </h2>
        <span className="text-2xs text-ink-600 tabular">{collections.length}</span>
        <button className="btn-ghost !px-2 ml-auto text-xs" onClick={() => setCreating(true)}>
          + Neu
        </button>
      </header>

      <div className="flex-1 overflow-y-auto p-2 space-y-1.5">
        {collections.length === 0 && (
          <p className="text-xs text-ink-500 px-2 py-6 text-center leading-relaxed">
            Noch keine Sammlung.
            <br />
            Eine Sammlung ist ein Ast im Cache-Baum.
          </p>
        )}
        {collections.map((collection) => (
          <CollectionCard
            key={collection.id}
            collection={collection}
            checked={selected.includes(collection.id)}
            onToggle={() => onToggle(collection.id)}
            onOpen={() => setDetailId(collection.id)}
            onRefresh={onRefresh}
          />
        ))}
      </div>

      <CreateDialog open={creating} onClose={() => setCreating(false)} onCreated={onRefresh} />
      {detailId && (
        <CollectionDetail
          collectionId={detailId}
          onClose={() => setDetailId(null)}
          onRefresh={onRefresh}
        />
      )}
    </aside>
  )
}

function CollectionCard({
  collection,
  checked,
  onToggle,
  onOpen,
  onRefresh,
}: {
  collection: Collection
  checked: boolean
  onToggle: () => void
  onOpen: () => void
  onRefresh: () => void
}) {
  const [job, setJob] = useState<any>(null)
  const busy = collection.status === 'ingesting' || (job && !job.finished)

  useEffect(() => {
    if (collection.status !== 'ingesting') return
    return subscribeProgress(collection.id, (data) => {
      setJob(data)
      if (data.finished) {
        setJob(null)
        onRefresh()
      }
    })
  }, [collection.id, collection.status, onRefresh])

  const startIngest = async () => {
    try {
      await api.startIngest(collection.id)
      onRefresh()
      subscribeProgress(collection.id, (data) => {
        setJob(data)
        if (data.finished) {
          setJob(null)
          onRefresh()
        }
      })
    } catch (error: any) {
      alert(error.message)
    }
  }

  return (
    <div
      className={`rounded-lg border transition-colors ${
        checked ? 'border-accent-dim bg-accent/[0.04]' : 'border-ink-800 bg-ink-850/40 hover:border-ink-700'
      }`}
    >
      <div className="flex items-start gap-2.5 p-2.5">
        <input
          type="checkbox"
          checked={checked}
          onChange={onToggle}
          className="mt-0.5 w-4 h-4 rounded accent-accent shrink-0 cursor-pointer"
          title="Für die nächste Frage berücksichtigen"
        />
        <button className="min-w-0 flex-1 text-left" onClick={onOpen}>
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-ink-100 truncate">{collection.name}</span>
            <StatusBadge status={collection.status} className="shrink-0" />
          </div>
          <div className="mt-1 flex items-center gap-2 text-2xs text-ink-500 tabular">
            <span>{collection.pages} Seiten</span>
            <span>·</span>
            <span>~{num(collection.estimated_tokens)} Tok</span>
            {collection.last_measured_hit_rate !== null && (
              <>
                <span>·</span>
                <span
                  className={
                    collection.last_measured_hit_rate >= 0.85
                      ? 'text-emerald-400'
                      : 'text-amber-400'
                  }
                  title={`Zuletzt gemessen ${relativeTime(collection.last_measured_at)}`}
                >
                  {(collection.last_measured_hit_rate * 100).toFixed(0)} % Treffer
                </span>
              </>
            )}
          </div>
          <TokenBar percent={collection.budget_used_percent} className="mt-2" showLabel />
        </button>
      </div>

      {job && !job.finished && (
        <div className="px-2.5 pb-2.5">
          <div className="flex items-center gap-2 text-2xs text-accent-soft">
            <Spinner />
            <span className="truncate">{job.message || job.phase}</span>
          </div>
          {job.total > 1 && (
            <div className="mt-1.5 h-1 rounded-full bg-ink-800 overflow-hidden">
              <div
                className="h-full bg-accent transition-[width]"
                style={{ width: `${job.percent}%` }}
              />
            </div>
          )}
        </div>
      )}

      {(collection.status === 'stale' ||
        collection.status === 'pending' ||
        collection.status === 'error') &&
        collection.pages > 0 &&
        !busy && (
          <div className="px-2.5 pb-2.5">
            {collection.status_detail && (
              <p className="text-2xs text-ink-500 mb-1.5 leading-snug">
                {collection.status_detail}
              </p>
            )}
            <button
              className="btn-outline w-full !py-1 text-2xs"
              onClick={startIngest}
              disabled={collection.over_budget}
              title={
                collection.over_budget
                  ? 'Die Sammlung überschreitet das Kontextfenster.'
                  : 'Präfix einmalig an SGLang senden und im Cache ablegen'
              }
            >
              {collection.status === 'pending' ? 'Ingestieren' : 'Neu ingestieren'}
            </button>
          </div>
        )}
    </div>
  )
}

function CreateDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean
  onClose: () => void
  onCreated: () => void
}) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [dpi, setDpi] = useState<number | ''>('')

  const submit = async () => {
    if (!name.trim()) return
    await api.createCollection({
      name: name.trim(),
      description,
      ...(dpi ? { dpi: Number(dpi) } : {}),
    } as any)
    setName('')
    setDescription('')
    setDpi('')
    onCreated()
    onClose()
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Neue Sammlung"
      subtitle="Eine Sammlung ist ein Ast im HiRadixTree. Dokumente werden ausschließlich hinten angehängt."
      footer={
        <>
          <button className="btn-ghost" onClick={onClose}>
            Abbrechen
          </button>
          <button className="btn-primary" onClick={submit} disabled={!name.trim()}>
            Anlegen
          </button>
        </>
      }
    >
      <div className="space-y-3">
        <Field label="Name">
          <input
            className="input"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="z. B. Mietverträge 2024"
            autoFocus
          />
        </Field>
        <Field label="Beschreibung" hint="Nur für dich — geht nicht in den Prompt.">
          <input
            className="input"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
        </Field>
        <Field label="DPI-Override" hint="Leer lassen, um den globalen Default zu verwenden.">
          <input
            className="input"
            type="number"
            min={72}
            max={300}
            value={dpi}
            onChange={(event) => setDpi(event.target.value ? Number(event.target.value) : '')}
          />
        </Field>
      </div>
    </Modal>
  )
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string
  hint?: string
  children: React.ReactNode
}) {
  return (
    <label className="block">
      <span className="block text-xs font-medium text-ink-300 mb-1">{label}</span>
      {children}
      {hint && <span className="block text-2xs text-ink-500 mt-1 leading-snug">{hint}</span>}
    </label>
  )
}

function CollectionDetail({
  collectionId,
  onClose,
  onRefresh,
}: {
  collectionId: string
  onClose: () => void
  onRefresh: () => void
}) {
  const [detail, setDetail] = useState<(Collection & { documents: DocumentInfo[] }) | null>(null)
  const [pages, setPages] = useState<PageInfo[]>([])
  const [lightbox, setLightbox] = useState<number | null>(null)
  const [pendingDelete, setPendingDelete] = useState<{ doc: DocumentInfo; impact: any } | null>(
    null,
  )
  const [uploading, setUploading] = useState(false)
  const [uploadDpi, setUploadDpi] = useState<number | null>(null)
  const [preview, setPreview] = useState<any>(null)
  const [pendingFile, setPendingFile] = useState<File | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)

  const load = useCallback(async () => {
    const [d, p] = await Promise.all([api.getCollection(collectionId), api.listPages(collectionId)])
    setDetail(d)
    setPages(p)
    setUploadDpi((current) => current ?? d.effective_dpi)
  }, [collectionId])

  useEffect(() => {
    void load()
  }, [load])

  // Re-estimate whenever the slider moves, debounced so dragging does not
  // hammer the backend with rasterisation probes.
  useEffect(() => {
    if (!pendingFile || !uploadDpi) return
    const timer = setTimeout(() => {
      api
        .dpiPreview(pendingFile, uploadDpi, detail?.max_edge ?? 0)
        .then(setPreview)
        .catch(() => setPreview(null))
    }, 250)
    return () => clearTimeout(timer)
  }, [pendingFile, uploadDpi, detail?.max_edge])

  const doUpload = async () => {
    if (!pendingFile || !uploadDpi) return
    setUploading(true)
    try {
      await api.uploadDocument(collectionId, pendingFile, uploadDpi)
      setPendingFile(null)
      setPreview(null)
      await load()
      onRefresh()
    } catch (error: any) {
      const detailPayload = error.detail
      alert(
        detailPayload?.overflow_tokens
          ? `${error.message}\n\nBudget: ${num(detailPayload.budget_tokens)} Tokens\n` +
            `Überschuss: ${num(detailPayload.overflow_tokens)} Tokens\n${detailPayload.hint ?? ''}`
          : error.message,
      )
    } finally {
      setUploading(false)
    }
  }

  const confirmDelete = async (doc: DocumentInfo) => {
    const impact = await api.removalImpact(collectionId, doc.id)
    setPendingDelete({ doc, impact })
  }

  if (!detail) return null

  return (
    <>
      <Modal
        open
        onClose={onClose}
        wide
        title={detail.name}
        subtitle={`${detail.documents.length} Dokumente · ${detail.pages} Seiten · ~${num(
          detail.estimated_tokens,
        )} von ${num(detail.budget_tokens)} verfügbaren Tokens`}
      >
        <div className="space-y-5">
          {detail.status_detail && (
            <div className="rounded-lg border border-amber-500/25 bg-amber-500/[0.06] px-3 py-2 text-xs text-amber-200 leading-relaxed">
              {detail.status_detail}
            </div>
          )}

          <section>
            <SectionTitle>Dokumente</SectionTitle>
            <p className="text-2xs text-ink-500 mb-2 leading-relaxed">
              Die Reihenfolge ergibt sich aus der Position im Präfix, nicht aus Dateinamen.
              Anhängen ist billig, Entfernen aus der Mitte erzwingt einen kompletten Re-Ingest.
            </p>
            <div className="space-y-1">
              {detail.documents.map((doc) => (
                <div
                  key={doc.id}
                  className="flex items-center gap-3 px-3 py-2 rounded-lg border border-ink-800 bg-ink-850/40"
                >
                  <span className="text-2xs font-mono text-ink-600 w-10 shrink-0">
                    #{doc.sequence_start}
                  </span>
                  <span className="text-sm text-ink-200 truncate flex-1">{doc.filename}</span>
                  <span className="text-2xs text-ink-500 tabular shrink-0">
                    {doc.page_count} S. · {doc.dpi} DPI
                  </span>
                  {doc.is_last && (
                    <span
                      className="badge bg-ink-800 text-ink-400 shrink-0"
                      title="Entfernen verkürzt den Präfix nur — der Cache bleibt gültig."
                    >
                      letztes
                    </span>
                  )}
                  <button
                    className="btn-ghost !px-2 text-2xs text-red-300 shrink-0"
                    onClick={() => confirmDelete(doc)}
                  >
                    Entfernen
                  </button>
                </div>
              ))}
              {detail.documents.length === 0 && (
                <p className="text-xs text-ink-500 py-3">Noch keine Dokumente.</p>
              )}
            </div>
          </section>

          <section>
            <SectionTitle>Dokument hinzufügen</SectionTitle>
            <input
              ref={fileInput}
              type="file"
              accept=".pdf,.png,.jpg,.jpeg,.webp,.tif,.tiff"
              className="hidden"
              onChange={(event) => {
                const file = event.target.files?.[0]
                if (file) setPendingFile(file)
                event.target.value = ''
              }}
            />
            {!pendingFile ? (
              <button className="btn-outline w-full" onClick={() => fileInput.current?.click()}>
                PDF oder Bild auswählen
              </button>
            ) : (
              <div className="rounded-lg border border-ink-800 bg-ink-850/40 p-3 space-y-3">
                <div className="flex items-center gap-2">
                  <span className="text-sm text-ink-100 truncate flex-1">{pendingFile.name}</span>
                  <button
                    className="btn-ghost !px-2 text-2xs"
                    onClick={() => {
                      setPendingFile(null)
                      setPreview(null)
                    }}
                  >
                    Verwerfen
                  </button>
                </div>

                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="text-xs font-medium text-ink-300">
                      DPI für diesen Upload
                    </span>
                    <span className="text-sm font-semibold tabular text-accent-soft">
                      {uploadDpi}
                    </span>
                  </div>
                  <input
                    type="range"
                    min={72}
                    max={300}
                    step={1}
                    value={uploadDpi ?? 150}
                    onChange={(event) => setUploadDpi(Number(event.target.value))}
                    className="w-full accent-accent"
                  />
                  <div className="flex justify-between text-2xs text-ink-600 mt-0.5">
                    <span>72 — klein</span>
                    <span>150 — Standard</span>
                    <span>300 — maximal</span>
                  </div>
                </div>

                {preview && (
                  <div className="grid grid-cols-3 gap-2 text-center">
                    <MiniStat
                      label="Seiten"
                      value={num(preview.pages)}
                      hint="Seitenzahl des Dokuments"
                    />
                    <MiniStat
                      label="Pixel/Seite"
                      value={
                        preview.per_page[0]
                          ? `${preview.per_page[0].width}×${preview.per_page[0].height}`
                          : '—'
                      }
                      hint="Resultierende Bildgröße bei dieser DPI"
                    />
                    <MiniStat
                      label="Tokens"
                      value={`~${num(preview.total_tokens)}`}
                      hint={`${preview.context_percent} % des Kontextfensters, ${preview.pixels_per_token_edge}×${preview.pixels_per_token_edge} px je Vision-Token`}
                      tone={
                        preview.context_percent > 90
                          ? 'bad'
                          : preview.context_percent > 70
                            ? 'warn'
                            : 'default'
                      }
                    />
                  </div>
                )}

                <button
                  className="btn-primary w-full"
                  onClick={doUpload}
                  disabled={uploading}
                >
                  {uploading ? (
                    <>
                      <Spinner /> Konvertiere …
                    </>
                  ) : (
                    'Hochladen und konvertieren'
                  )}
                </button>
              </div>
            )}
          </section>

          <section>
            <SectionTitle>
              Seiten
              <span className="ml-2 font-normal text-ink-500 normal-case tracking-normal">
                exakt so, wie sie an das Modell gehen
              </span>
            </SectionTitle>
            <div className="grid grid-cols-[repeat(auto-fill,minmax(88px,1fr))] gap-2">
              {pages.map((page, index) => (
                <button
                  key={page.id}
                  onClick={() => setLightbox(index)}
                  className="group relative rounded border border-ink-800 hover:border-accent-dim overflow-hidden transition-colors"
                  title={`${page.filename} S. ${page.page_number} — ${page.width}×${page.height}px, ~${page.estimated_tokens} Tokens`}
                >
                  <img
                    src={page.url}
                    alt=""
                    className="w-full aspect-[1/1.414] object-cover object-top bg-white"
                    loading="lazy"
                  />
                  <span className="absolute bottom-0 inset-x-0 bg-ink-950/85 text-2xs tabular text-ink-300 px-1 py-0.5 flex justify-between">
                    <span>#{page.sequence_index}</span>
                    <span>{Math.round(page.estimated_tokens / 100) / 10}k</span>
                  </span>
                </button>
              ))}
            </div>
            {pages.length === 0 && <p className="text-xs text-ink-500">Noch keine Seiten.</p>}
          </section>
        </div>
      </Modal>

      {lightbox !== null && (
        <Lightbox
          pages={pages}
          index={lightbox}
          onIndexChange={setLightbox}
          onClose={() => setLightbox(null)}
        />
      )}

      <ConfirmDialog
        open={pendingDelete !== null}
        danger={pendingDelete?.impact.invalidates}
        title={
          pendingDelete?.impact.invalidates
            ? 'Sammlung wird invalidiert'
            : 'Dokument entfernen'
        }
        body={
          <div className="space-y-2">
            <p>
              <span className="font-mono text-ink-100">{pendingDelete?.doc.filename}</span> wird
              entfernt.
            </p>
            <p className={pendingDelete?.impact.invalidates ? 'text-amber-300' : 'text-ink-400'}>
              {pendingDelete?.impact.message}
            </p>
          </div>
        }
        confirmLabel={pendingDelete?.impact.invalidates ? 'Invalidieren und entfernen' : 'Entfernen'}
        onCancel={() => setPendingDelete(null)}
        onConfirm={async () => {
          if (!pendingDelete) return
          await api.deleteDocument(collectionId, pendingDelete.doc.id)
          setPendingDelete(null)
          await load()
          onRefresh()
        }}
      />
    </>
  )
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="text-xs font-semibold uppercase tracking-wider text-ink-400 mb-2">
      {children}
    </h3>
  )
}

function MiniStat({
  label,
  value,
  hint,
  tone = 'default',
}: {
  label: string
  value: string
  hint?: string
  tone?: 'default' | 'warn' | 'bad'
}) {
  const toneClass = { default: 'text-ink-100', warn: 'text-amber-300', bad: 'text-red-300' }[tone]
  return (
    <div className="rounded border border-ink-800 bg-ink-900 px-2 py-1.5" title={hint}>
      <div className="text-2xs text-ink-500 uppercase tracking-wide">{label}</div>
      <div className={`text-sm font-semibold tabular ${toneClass}`}>{value}</div>
    </div>
  )
}
