import { useEffect, useMemo, useState } from 'react'
import { api, type SettingSpec, type SettingsPayload } from '../lib/api'
import { CopyButton } from './Markdown'
import { Modal, Spinner } from './ui'

const GROUPS: { key: string; label: string; blurb: string }[] = [
  {
    key: 'connection',
    label: 'Verbindung',
    blurb:
      'Die SGLang-Instanz läuft extern auf vast.ai und wird über einen Cloudflare-Tunnel angesprochen.',
  },
  {
    key: 'model',
    label: 'Modell & Sampling',
    blurb: 'Parameter, die pro Request mitgeschickt werden.',
  },
  {
    key: 'vision',
    label: 'Vision-Token-Modell',
    blurb:
      'Aus der preprocessor_config.json des Modells. Qwen3.6-27B nutzt patch_size 16 und merge_size 2 — ein Vision-Token deckt also 32×32 px ab (ältere Qwen-VL-Generationen: 28×28 px).',
  },
  {
    key: 'hicache',
    label: 'HiCache',
    blurb:
      'Das sind Startparameter der vast.ai-Instanz — dieses System kann sie nicht zur Laufzeit ändern. Es erzeugt daraus das Launch-Kommando und meldet Abweichungen der laufenden Instanz.',
  },
  {
    key: 'conversion',
    label: 'Dokumentkonvertierung',
    blurb: 'Globale Defaults. Pro Sammlung und pro Upload überschreibbar.',
  },
  { key: 'query', label: 'Query', blurb: 'Wie eine Frage abgearbeitet wird.' },
  {
    key: 'prompts',
    label: 'Prompts',
    blurb:
      'Der globale System-Prompt und das Seitenlabel sind Teil des gecachten Präfixes. Eine Änderung invalidiert sämtliche Sammlungen.',
  },
]

export function SettingsPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [payload, setPayload] = useState<SettingsPayload | null>(null)
  const [draft, setDraft] = useState<Record<string, any>>({})
  const [group, setGroup] = useState('connection')
  const [advanced, setAdvanced] = useState(false)
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [mismatches, setMismatches] = useState<any[] | null>(null)

  useEffect(() => {
    if (!open) return
    api.getSettings().then((data) => {
      setPayload(data)
      setDraft(data.values)
    })
    api
      .serverInfo()
      .then((info) => setMismatches(info.mismatches))
      .catch(() => setMismatches(null))
  }, [open])

  const dirty = useMemo(() => {
    if (!payload) return []
    return Object.keys(draft).filter(
      (key) => JSON.stringify(draft[key]) !== JSON.stringify(payload.values[key]),
    )
  }, [draft, payload])

  const invalidatingDirty = useMemo(() => {
    if (!payload) return []
    return dirty.filter((key) => payload.schema.find((s) => s.key === key)?.invalidates_cache)
  }, [dirty, payload])

  const save = async () => {
    if (!payload) return
    setSaving(true)
    try {
      const changed = Object.fromEntries(dirty.map((key) => [key, draft[key]]))
      const result = await api.saveSettings(changed)
      setPayload({ ...payload, values: result.values, launch_command: result.launch_command })
      setDraft(result.values)
      setNotice(
        result.invalidated_collections > 0
          ? `Gespeichert. ${result.invalidated_collections} Sammlung(en) auf "stale" gesetzt — der Präfix hat sich geändert.`
          : 'Gespeichert.',
      )
      setTimeout(() => setNotice(null), 6000)
    } catch (error: any) {
      setNotice(error.message)
    } finally {
      setSaving(false)
    }
  }

  const specs = (payload?.schema ?? []).filter(
    (spec) => spec.group === group && (advanced || !spec.advanced),
  )
  const meta = GROUPS.find((entry) => entry.key === group)!

  return (
    <Modal
      open={open}
      onClose={onClose}
      wide
      title="Einstellungen"
      subtitle="Alle Serverparameter, gruppiert. Werte werden persistiert und sind aus .env vorbelegbar."
      footer={
        <>
          {notice && <span className="mr-auto text-2xs text-emerald-300">{notice}</span>}
          {dirty.length > 0 && !notice && (
            <span className="mr-auto text-2xs text-ink-400">
              {dirty.length} Änderung(en) nicht gespeichert
              {invalidatingDirty.length > 0 && (
                <span className="text-amber-300">
                  {' '}
                  · invalidiert alle Caches
                </span>
              )}
            </span>
          )}
          <button className="btn-ghost" onClick={onClose}>
            Schließen
          </button>
          <button className="btn-primary" onClick={save} disabled={!dirty.length || saving}>
            {saving ? <Spinner /> : null} Speichern
          </button>
        </>
      }
    >
      {!payload ? (
        <div className="flex items-center gap-2 justify-center py-16 text-ink-500">
          <Spinner /> Lade …
        </div>
      ) : (
        <div className="flex gap-5 min-h-[26rem]">
          <nav className="w-44 shrink-0 space-y-0.5">
            {GROUPS.map((entry) => (
              <button
                key={entry.key}
                onClick={() => setGroup(entry.key)}
                className={`w-full text-left px-3 py-2 rounded-md text-xs transition-colors ${
                  group === entry.key
                    ? 'bg-ink-800 text-ink-100 font-medium'
                    : 'text-ink-400 hover:bg-ink-850 hover:text-ink-200'
                }`}
              >
                {entry.label}
              </button>
            ))}
            <label className="flex items-center gap-2 px-3 pt-4 text-2xs text-ink-500 cursor-pointer">
              <input
                type="checkbox"
                checked={advanced}
                onChange={(event) => setAdvanced(event.target.checked)}
                className="accent-accent"
              />
              Erweiterte zeigen
            </label>
          </nav>

          <div className="flex-1 min-w-0 space-y-4">
            <p className="text-xs text-ink-400 leading-relaxed border-l-2 border-ink-800 pl-3">
              {meta.blurb}
            </p>

            {group === 'prompts' && invalidatingDirty.length > 0 && (
              <div className="rounded-lg border border-amber-500/30 bg-amber-500/[0.07] px-3 py-2.5 text-xs text-amber-200 leading-relaxed">
                <strong>Achtung:</strong> {invalidatingDirty.join(', ')} ist Teil des gecachten
                Präfixes. Nach dem Speichern werden alle Sammlungen auf{' '}
                <span className="font-mono">stale</span> gesetzt und müssen neu ingestiert werden
                — das erste Prefill danach kostet wieder die volle Zeit.
              </div>
            )}

            {group === 'hicache' && (
              <LaunchCommand command={payload.launch_command} mismatches={mismatches} />
            )}

            <div className="space-y-3">
              {specs.map((spec) => (
                <SettingField
                  key={spec.key}
                  spec={spec}
                  value={draft[spec.key]}
                  changed={dirty.includes(spec.key)}
                  onChange={(value) => setDraft((current) => ({ ...current, [spec.key]: value }))}
                  onReset={async () => {
                    const result = await api.resetSettings([spec.key])
                    setPayload({ ...payload, values: result.values, launch_command: result.launch_command })
                    setDraft(result.values)
                  }}
                />
              ))}
            </div>
          </div>
        </div>
      )}
    </Modal>
  )
}

function LaunchCommand({
  command,
  mismatches,
}: {
  command: string
  mismatches: any[] | null
}) {
  return (
    <div className="space-y-2">
      {mismatches === null ? (
        <div className="rounded-lg border border-ink-800 bg-ink-950 px-3 py-2 text-2xs text-ink-500">
          Die laufende Instanz konnte nicht abgefragt werden (<code>/get_server_info</code>) —
          kein Abgleich möglich.
        </div>
      ) : mismatches.length === 0 ? (
        <div className="rounded-lg border border-emerald-500/25 bg-emerald-500/[0.06] px-3 py-2 text-2xs text-emerald-300">
          Die laufende Instanz stimmt mit diesen Einstellungen überein.
        </div>
      ) : (
        <div className="rounded-lg border border-amber-500/25 bg-amber-500/[0.06] px-3 py-2 text-2xs text-amber-200 space-y-1">
          <div className="font-medium">
            Die laufende Instanz weicht ab — diese Werte sind nur Dokumentation, bis die Instanz
            mit ihnen neu gestartet wird:
          </div>
          {mismatches.map((mismatch) => (
            <div key={mismatch.key} className="font-mono">
              {mismatch.label}: läuft mit{' '}
              <span className="text-red-300">{String(mismatch.actual)}</span>, eingestellt{' '}
              <span className="text-emerald-300">{String(mismatch.expected)}</span>
            </div>
          ))}
        </div>
      )}

      <div className="rounded-lg border border-ink-800 bg-ink-950 overflow-hidden">
        <div className="flex items-center justify-between px-3 py-1.5 border-b border-ink-800">
          <span className="text-2xs uppercase tracking-wider text-ink-500">
            Launch-Kommando für die vast.ai-Instanz
          </span>
          <CopyButton value={command} />
        </div>
        <pre className="p-3 text-2xs font-mono text-ink-300 overflow-x-auto leading-relaxed">
          {command}
        </pre>
      </div>
    </div>
  )
}

function SettingField({
  spec,
  value,
  changed,
  onChange,
  onReset,
}: {
  spec: SettingSpec
  value: any
  changed: boolean
  onChange: (value: any) => void
  onReset: () => void
}) {
  const [revealed, setRevealed] = useState(false)

  return (
    <div
      className={`rounded-lg border px-3 py-2.5 transition-colors ${
        changed ? 'border-accent-dim bg-accent/[0.04]' : 'border-ink-800 bg-ink-850/30'
      }`}
    >
      <div className="flex items-start gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs font-medium text-ink-200">{spec.label}</span>
            <code className="text-2xs text-ink-600">{spec.key}</code>
            {spec.launch_param && (
              <span
                className="badge bg-ink-800 text-ink-400"
                title="Startparameter der externen Instanz — wirkt erst nach deren Neustart."
              >
                Startparameter
              </span>
            )}
            {spec.invalidates_cache && (
              <span className="badge bg-amber-500/12 text-amber-300">invalidiert Cache</span>
            )}
          </div>
          {spec.description && (
            <p className="text-2xs text-ink-500 mt-1 leading-relaxed">{spec.description}</p>
          )}
        </div>
        <button className="btn-ghost !px-1.5 !py-0.5 text-2xs shrink-0" onClick={onReset}>
          Zurücksetzen
        </button>
      </div>

      <div className="mt-2">
        {spec.type === 'bool' ? (
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={Boolean(value)}
              onChange={(event) => onChange(event.target.checked)}
              className="w-4 h-4 accent-accent"
            />
            <span className="text-xs text-ink-300">{value ? 'aktiv' : 'inaktiv'}</span>
          </label>
        ) : spec.type === 'select' ? (
          <select
            className="input"
            value={String(value ?? '')}
            onChange={(event) => onChange(event.target.value)}
          >
            {spec.choices.map((choice) => (
              <option key={choice} value={choice}>
                {choice}
              </option>
            ))}
          </select>
        ) : spec.type === 'text' ? (
          <textarea
            className="input font-mono text-2xs leading-relaxed"
            rows={Math.min(18, String(value ?? '').split('\n').length + 2)}
            value={String(value ?? '')}
            onChange={(event) => onChange(event.target.value)}
          />
        ) : spec.secret ? (
          <div className="flex gap-2">
            <input
              className="input font-mono"
              type={revealed ? 'text' : 'password'}
              value={String(value ?? '')}
              onChange={(event) => onChange(event.target.value)}
            />
            <button className="btn-ghost text-2xs shrink-0" onClick={() => setRevealed((v) => !v)}>
              {revealed ? 'Verbergen' : 'Zeigen'}
            </button>
          </div>
        ) : (
          <input
            className="input"
            type={spec.type === 'int' || spec.type === 'float' ? 'number' : 'text'}
            step={spec.type === 'float' ? 0.01 : 1}
            min={spec.minimum ?? undefined}
            max={spec.maximum ?? undefined}
            value={String(value ?? '')}
            onChange={(event) =>
              onChange(
                spec.type === 'int'
                  ? Number.parseInt(event.target.value || '0', 10)
                  : spec.type === 'float'
                    ? Number.parseFloat(event.target.value || '0')
                    : event.target.value,
              )
            }
          />
        )}
      </div>
    </div>
  )
}
