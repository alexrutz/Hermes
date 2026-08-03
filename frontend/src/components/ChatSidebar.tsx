import { useState } from 'react'
import { api, type ChatSummary } from '../lib/api'
import { groupChatsByDate } from '../lib/format'
import { ConfirmDialog } from './ui'

export function ChatSidebar({
  chats,
  activeId,
  onSelect,
  onNew,
  onRefresh,
}: {
  chats: ChatSummary[]
  activeId: string | null
  onSelect: (id: string) => void
  onNew: () => void
  onRefresh: () => void
}) {
  const [search, setSearch] = useState('')
  const [renaming, setRenaming] = useState<string | null>(null)
  const [draftTitle, setDraftTitle] = useState('')
  const [pendingDelete, setPendingDelete] = useState<ChatSummary | null>(null)

  const filtered = search
    ? chats.filter((chat) => chat.title.toLowerCase().includes(search.toLowerCase()))
    : chats
  const groups = groupChatsByDate(filtered)

  const commitRename = async (id: string) => {
    if (draftTitle.trim()) await api.updateChat(id, { title: draftTitle.trim() })
    setRenaming(null)
    onRefresh()
  }

  return (
    <aside className="w-64 shrink-0 border-r border-ink-850 bg-ink-900 flex flex-col">
      <div className="p-3 border-b border-ink-850 space-y-2">
        <button className="btn-primary w-full justify-center" onClick={onNew}>
          Neuer Chat
        </button>
        <input
          className="input !py-1 text-xs"
          placeholder="Chats durchsuchen …"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
      </div>

      <nav className="flex-1 overflow-y-auto p-2">
        {groups.length === 0 && (
          <p className="text-xs text-ink-500 text-center py-8">
            {search ? 'Nichts gefunden.' : 'Noch keine Chats.'}
          </p>
        )}
        {groups.map((group) => (
          <div key={group.label} className="mb-3">
            <div className="px-2 py-1 text-2xs font-medium uppercase tracking-wider text-ink-600">
              {group.label}
            </div>
            <div className="space-y-0.5">
              {group.items.map((chat) => (
                <div
                  key={chat.id}
                  className={`group flex items-center gap-1 rounded-md transition-colors ${
                    chat.id === activeId ? 'bg-ink-800' : 'hover:bg-ink-850'
                  }`}
                >
                  {renaming === chat.id ? (
                    <input
                      className="input !py-1 !px-2 text-xs m-1"
                      value={draftTitle}
                      autoFocus
                      onChange={(event) => setDraftTitle(event.target.value)}
                      onBlur={() => commitRename(chat.id)}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter') commitRename(chat.id)
                        if (event.key === 'Escape') setRenaming(null)
                      }}
                    />
                  ) : (
                    <>
                      <button
                        className="flex-1 min-w-0 text-left px-2.5 py-2"
                        onClick={() => onSelect(chat.id)}
                        onDoubleClick={() => {
                          setRenaming(chat.id)
                          setDraftTitle(chat.title)
                        }}
                      >
                        <div
                          className={`text-xs truncate ${
                            chat.id === activeId ? 'text-ink-100' : 'text-ink-300'
                          }`}
                        >
                          {chat.title}
                        </div>
                        <div className="text-2xs text-ink-600 tabular">
                          {chat.message_count} Nachrichten
                          {chat.selected_collections.length > 0 &&
                            ` · ${chat.selected_collections.length} Sammlungen`}
                        </div>
                      </button>
                      <div className="opacity-0 group-hover:opacity-100 flex pr-1 transition-opacity">
                        <button
                          className="btn-ghost !px-1.5 !py-1 text-2xs"
                          title="Umbenennen"
                          onClick={() => {
                            setRenaming(chat.id)
                            setDraftTitle(chat.title)
                          }}
                        >
                          ✎
                        </button>
                        <button
                          className="btn-ghost !px-1.5 !py-1 text-2xs hover:text-red-300"
                          title="Löschen"
                          onClick={() => setPendingDelete(chat)}
                        >
                          ✕
                        </button>
                      </div>
                    </>
                  )}
                </div>
              ))}
            </div>
          </div>
        ))}
      </nav>

      <ConfirmDialog
        open={pendingDelete !== null}
        danger
        title="Chat löschen"
        body={
          <>
            „{pendingDelete?.title}" wird mit allen Nachrichten, Einzelantworten und
            Thinking-Blöcken gelöscht. Sammlungen und deren Cache bleiben unberührt.
          </>
        }
        confirmLabel="Löschen"
        onCancel={() => setPendingDelete(null)}
        onConfirm={async () => {
          if (!pendingDelete) return
          await api.deleteChat(pendingDelete.id)
          setPendingDelete(null)
          onRefresh()
        }}
      />
    </aside>
  )
}
