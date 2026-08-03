import { useCallback, useEffect, useRef, useState } from 'react'
import {
  api,
  streamQuery,
  type ChatMessage,
  type ChatSummary,
  type Collection,
  type CollectionAnswer,
  type PageInfo,
} from './lib/api'
import { ms, num, pct } from './lib/format'
import { CacheTreeView } from './components/CacheTreeView'
import { ChatSidebar } from './components/ChatSidebar'
import { CollectionsPanel } from './components/CollectionsPanel'
import { Lightbox } from './components/Lightbox'
import { MessageView } from './components/MessageView'
import { SettingsPanel } from './components/SettingsPanel'
import { EmptyState, Spinner, useAutoScroll } from './components/ui'

interface LiveState {
  answer: string
  collections: Record<string, CollectionAnswer>
  order: string[]
  synthesis: { reasoning_text: string; started: boolean }
  phase: 'idle' | 'collections' | 'synthesis' | 'done'
  error: string | null
  totalMs: number | null
}

const EMPTY_LIVE: LiveState = {
  answer: '',
  collections: {},
  order: [],
  synthesis: { reasoning_text: '', started: false },
  phase: 'idle',
  error: null,
  totalMs: null,
}

export default function App() {
  const [collections, setCollections] = useState<Collection[]>([])
  const [chats, setChats] = useState<ChatSummary[]>([])
  const [activeChat, setActiveChat] = useState<string | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [selected, setSelected] = useState<string[]>([])
  const [question, setQuestion] = useState('')
  const [live, setLive] = useState<LiveState>(EMPTY_LIVE)
  const [busy, setBusy] = useState(false)
  const [health, setHealth] = useState<{ ok: boolean; latency_ms: number; error: string } | null>(
    null,
  )
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [treeOpen, setTreeOpen] = useState(false)
  const [lightbox, setLightbox] = useState<{ pages: PageInfo[]; index: number } | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const scrollRef = useAutoScroll<HTMLDivElement>(
    messages.length + live.answer.length + live.order.length,
  )

  const refreshCollections = useCallback(() => {
    api.listCollections().then(setCollections).catch(() => {})
  }, [])
  const refreshChats = useCallback(() => {
    api.listChats().then(setChats).catch(() => {})
  }, [])

  useEffect(() => {
    refreshCollections()
    refreshChats()
  }, [refreshCollections, refreshChats])

  // Poll connection health and collection status; ingest jobs finish in the
  // background and the badges have to catch up on their own.
  useEffect(() => {
    const tick = () => {
      api.health().then(setHealth).catch(() => setHealth({ ok: false, latency_ms: 0, error: 'nicht erreichbar' }))
      refreshCollections()
    }
    tick()
    const timer = setInterval(tick, 10000)
    return () => clearInterval(timer)
  }, [refreshCollections])

  const openChat = async (id: string) => {
    const chat = await api.getChat(id)
    setActiveChat(id)
    setMessages(chat.messages)
    // Restore the collections that were ticked when this chat was last used.
    setSelected(chat.selected_collections)
    setLive(EMPTY_LIVE)
  }

  const newChat = () => {
    setActiveChat(null)
    setMessages([])
    setLive(EMPTY_LIVE)
  }

  const toggleCollection = (id: string) => {
    setSelected((current) => {
      const next = current.includes(id) ? current.filter((x) => x !== id) : [...current, id]
      if (activeChat) void api.updateChat(activeChat, { selected_collections: next })
      return next
    })
  }

  const send = async () => {
    const text = question.trim()
    if (!text || !selected.length || busy) return

    setQuestion('')
    setBusy(true)
    setLive({ ...EMPTY_LIVE, phase: 'collections' })
    setMessages((current) => [
      ...current,
      {
        id: `local-${Date.now()}`,
        role: 'user',
        content: text,
        created_at: new Date().toISOString(),
        collection_answers: [],
        synthesis: null,
      },
    ])

    const controller = new AbortController()
    abortRef.current = controller
    let chatId = activeChat

    try {
      await streamQuery(
        { chat_id: activeChat, question: text, collection_ids: selected },
        (event, data) => {
          setLive((current) => {
            const next = { ...current, collections: { ...current.collections } }
            const patch = (id: string, changes: Partial<CollectionAnswer>) => {
              next.collections[id] = { ...(next.collections[id] as CollectionAnswer), ...changes }
            }

            switch (event) {
              case 'chat':
                chatId = data.chat_id
                break
              case 'collection_start':
                next.order = [...current.order, data.collection_id]
                next.collections[data.collection_id] = {
                  collection_id: data.collection_id,
                  collection_name: data.collection_name,
                  answer_text: '',
                  reasoning_text: '',
                  latency_ms: 0,
                  ttft_ms: 0,
                  prompt_tokens: 0,
                  cached_tokens: 0,
                  completion_tokens: 0,
                  cache_hit_rate: 0,
                  error: '',
                  prefix_matched: data.prefix_matched,
                  streaming: true,
                }
                break
              case 'collection_reasoning':
                patch(data.collection_id, {
                  reasoning_text:
                    (next.collections[data.collection_id]?.reasoning_text ?? '') + data.text,
                })
                break
              case 'collection_delta':
                patch(data.collection_id, {
                  answer_text:
                    (next.collections[data.collection_id]?.answer_text ?? '') + data.text,
                })
                break
              case 'collection_done':
                patch(data.collection_id, { ...data, streaming: false })
                break
              case 'collection_error':
                patch(data.collection_id, { error: data.error, streaming: false })
                break
              case 'synthesis_start':
                next.phase = 'synthesis'
                next.synthesis = { ...next.synthesis, started: true }
                break
              case 'synthesis_reasoning':
                next.synthesis = {
                  ...next.synthesis,
                  reasoning_text: next.synthesis.reasoning_text + data.text,
                }
                break
              case 'synthesis_skipped':
                next.phase = 'synthesis'
                break
              case 'answer_delta':
                next.answer = current.answer + data.text
                break
              case 'done':
                next.phase = 'done'
                next.totalMs = data.total_ms
                break
              case 'error':
                next.error = data.error
                break
            }
            return next
          })
        },
        controller.signal,
      )

      if (chatId) {
        const chat = await api.getChat(chatId)
        setActiveChat(chatId)
        setMessages(chat.messages)
        setLive(EMPTY_LIVE)
        refreshChats()
      }
    } catch (error: any) {
      if (error.name !== 'AbortError') {
        setLive((current) => ({ ...current, error: error.message, phase: 'done' }))
      }
    } finally {
      setBusy(false)
      abortRef.current = null
      refreshCollections()
    }
  }

  const openPage = async (collectionId: string, pageId: string) => {
    const pages = await api.listPages(collectionId)
    const index = Math.max(0, pages.findIndex((page) => page.id === pageId))
    setTreeOpen(false)
    setLightbox({ pages, index })
  }

  const selectedCollections = collections.filter((c) => selected.includes(c.id))
  const notCached = selectedCollections.filter((c) => c.status !== 'cached')
  const liveAnswers = live.order.map((id) => live.collections[id]!).filter(Boolean)

  return (
    <div className="h-full flex bg-ink-950">
      <ChatSidebar
        chats={chats}
        activeId={activeChat}
        onSelect={openChat}
        onNew={newChat}
        onRefresh={refreshChats}
      />

      <main className="flex-1 flex flex-col min-w-0">
        <header className="h-12 shrink-0 border-b border-ink-850 bg-ink-900 flex items-center gap-3 px-4">
          <h1 className="text-sm font-semibold tracking-tight text-ink-100">Hermes</h1>
          <span className="text-2xs text-ink-600">Visuelles CAG · SGLang HiCache</span>

          <div className="ml-auto flex items-center gap-2">
            <button className="btn-ghost text-xs" onClick={() => setTreeOpen(true)}>
              Cache-Baum
            </button>
            <button className="btn-ghost text-xs" onClick={() => setSettingsOpen(true)}>
              Einstellungen
            </button>
            <span
              className={`badge ${
                health?.ok
                  ? 'bg-emerald-500/12 text-emerald-300 border border-emerald-500/25'
                  : 'bg-red-500/12 text-red-300 border border-red-500/25'
              }`}
              title={
                health?.ok
                  ? `SGLang erreichbar · ${health.latency_ms} ms`
                  : `SGLang nicht erreichbar. ${health?.error ?? ''}`
              }
            >
              <span
                className={`w-1.5 h-1.5 rounded-full ${
                  health?.ok ? 'bg-emerald-400' : 'bg-red-400 animate-shimmer'
                }`}
              />
              {health?.ok ? `SGLang ${health.latency_ms} ms` : 'offline'}
            </span>
          </div>
        </header>

        <div ref={scrollRef} className="flex-1 overflow-y-auto">
          <div className="max-w-3xl mx-auto px-6 py-8 space-y-7">
            {messages.length === 0 && !busy && (
              <EmptyState
                title="Frag deine Dokumente"
                body="Hake rechts eine oder mehrere Sammlungen an und stelle eine Frage. Die Seitenbilder liegen bereits im KV-Cache — es muss nur noch die Frage geprefillt werden."
              />
            )}

            {messages.map((message) => (
              <MessageView key={message.id} message={message} />
            ))}

            {busy && (
              <MessageView
                streaming
                message={{
                  id: 'live',
                  role: 'assistant',
                  content: live.answer,
                  created_at: new Date().toISOString(),
                  collection_answers: liveAnswers,
                  synthesis: live.synthesis.started
                    ? {
                        reasoning_text: live.synthesis.reasoning_text,
                        latency_ms: 0,
                        ttft_ms: 0,
                        prompt_tokens: 0,
                        completion_tokens: 0,
                        skipped: false,
                      }
                    : null,
                }}
              />
            )}

            {live.error && (
              <div className="rounded-lg border border-red-500/25 bg-red-500/[0.06] px-3 py-2 text-xs text-red-300">
                {live.error}
              </div>
            )}
          </div>
        </div>

        <footer className="shrink-0 border-t border-ink-850 bg-ink-900">
          <div className="max-w-3xl mx-auto px-6 py-3">
            {selectedCollections.length > 0 && (
              <div className="flex items-center gap-2 mb-2 flex-wrap text-2xs">
                <span className="text-ink-500">Durchsucht:</span>
                {selectedCollections.map((collection) => (
                  <span
                    key={collection.id}
                    className="badge bg-ink-800 text-ink-300 border border-ink-700"
                    title={`${collection.pages} Seiten · ~${num(collection.estimated_tokens)} Tokens`}
                  >
                    {collection.name}
                  </span>
                ))}
                {notCached.length > 0 && (
                  <span
                    className="badge bg-amber-500/12 text-amber-300 border border-amber-500/25"
                    title="Diese Sammlungen sind nicht ingestiert — die erste Anfrage muss den vollen Präfix prefillen und dauert entsprechend lange."
                  >
                    {notCached.length} nicht gecacht
                  </span>
                )}
              </div>
            )}

            <div className="relative">
              <textarea
                className="input resize-none pr-24 min-h-[3.25rem] max-h-40 leading-relaxed"
                rows={1}
                placeholder={
                  selected.length
                    ? 'Frage stellen … (Enter senden, Shift+Enter neue Zeile)'
                    : 'Zuerst rechts mindestens eine Sammlung anhaken'
                }
                value={question}
                disabled={busy}
                onChange={(event) => {
                  setQuestion(event.target.value)
                  event.target.style.height = 'auto'
                  event.target.style.height = `${Math.min(160, event.target.scrollHeight)}px`
                }}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && !event.shiftKey) {
                    event.preventDefault()
                    void send()
                  }
                }}
              />
              <div className="absolute right-2 bottom-2 flex items-center gap-1.5">
                {busy ? (
                  <button
                    className="btn-outline text-xs"
                    onClick={() => abortRef.current?.abort()}
                  >
                    <Spinner /> Abbrechen
                  </button>
                ) : (
                  <button
                    className="btn-primary text-xs"
                    onClick={send}
                    disabled={!question.trim() || !selected.length}
                  >
                    Senden
                  </button>
                )}
              </div>
            </div>

            <div className="flex items-center gap-3 mt-1.5 text-2xs text-ink-600 tabular">
              {live.phase !== 'idle' && busy && (
                <span className="text-accent-soft">
                  {live.phase === 'collections'
                    ? `Sammlungen werden abgefragt (${liveAnswers.length}/${selected.length})`
                    : 'Antworten werden zusammengeführt'}
                </span>
              )}
              {live.totalMs && <span>Gesamt {ms(live.totalMs)}</span>}
              {liveAnswers.length > 0 && !busy && (
                <span>
                  Cache{' '}
                  {pct(
                    liveAnswers.reduce((sum, a) => sum + a.cached_tokens, 0) /
                      Math.max(1, liveAnswers.reduce((sum, a) => sum + a.prompt_tokens, 0)),
                    0,
                  )}
                </span>
              )}
              <span className="ml-auto">
                {selectedCollections.reduce((sum, c) => sum + c.pages, 0)} Seiten ·{' '}
                {num(selectedCollections.reduce((sum, c) => sum + c.estimated_tokens, 0))} Tokens
                im Präfix
              </span>
            </div>
          </div>
        </footer>
      </main>

      <CollectionsPanel
        collections={collections}
        selected={selected}
        onToggle={toggleCollection}
        onRefresh={refreshCollections}
      />

      <SettingsPanel open={settingsOpen} onClose={() => setSettingsOpen(false)} />
      <CacheTreeView
        open={treeOpen}
        onClose={() => setTreeOpen(false)}
        onPageOpen={openPage}
      />
      {lightbox && (
        <Lightbox
          pages={lightbox.pages}
          index={lightbox.index}
          onIndexChange={(index) => setLightbox({ ...lightbox, index })}
          onClose={() => setLightbox(null)}
        />
      )}
    </div>
  )
}
