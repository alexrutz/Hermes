import type { ChatMessage, CollectionAnswer, SynthesisInfo } from '../lib/api'
import { ms, num, pct } from '../lib/format'
import { Markdown } from './Markdown'
import { Disclosure, HitRatePill, Spinner } from './ui'

/**
 * Assistant messages show the synthesised answer only. Everything underneath —
 * the per-collection answers and every thinking block — is collapsed by
 * default, with the numbers that matter in each header.
 */

function AnswerBlock({ answer }: { answer: CollectionAnswer }) {
  const noHit = answer.answer_text.trim().toUpperCase().startsWith('KEIN TREFFER')
  return (
    <Disclosure
      title={
        <span className="flex items-center gap-2">
          <span className={noHit ? 'text-ink-400' : 'text-ink-100'}>{answer.collection_name}</span>
          {noHit && <span className="badge bg-ink-800 text-ink-400">kein Treffer</span>}
          {answer.error && <span className="badge bg-red-500/12 text-red-300">Fehler</span>}
          {answer.prefix_matched === false && (
            <span
              className="badge bg-amber-500/12 text-amber-300"
              title="Der gesendete Präfix wich vom ingestierten ab — kein Cache-Treffer möglich."
            >
              Präfix abweichend
            </span>
          )}
          {answer.streaming && <Spinner className="text-accent" />}
        </span>
      }
      meta={
        <>
          <span title="Zeit bis zum ersten Token">TTFT {ms(answer.ttft_ms)}</span>
          <span title="Gesamtdauer">{ms(answer.latency_ms)}</span>
          <span title="Prompt-Tokens, davon aus dem Cache">
            {num(answer.prompt_tokens)} Tok · {num(answer.cached_tokens)} gecacht
          </span>
          <HitRatePill rate={answer.cache_hit_rate} matched={answer.prefix_matched} />
        </>
      }
    >
      {answer.error ? (
        <p className="text-xs text-red-300 font-mono whitespace-pre-wrap">{answer.error}</p>
      ) : (
        <div className="space-y-2">
          {answer.reasoning_text && (
            <Disclosure
              tone="thinking"
              title={<span className="text-ink-400">Thinking</span>}
              meta={<span>{num(answer.reasoning_text.length)} Zeichen</span>}
            >
              <div className="text-xs text-ink-400 whitespace-pre-wrap leading-relaxed font-mono max-h-96 overflow-y-auto">
                {answer.reasoning_text}
              </div>
            </Disclosure>
          )}
          <Markdown content={answer.answer_text} streaming={answer.streaming} />
        </div>
      )}
    </Disclosure>
  )
}

function SynthesisBlock({ synthesis }: { synthesis: SynthesisInfo }) {
  if (synthesis.skipped) {
    return (
      <div className="text-2xs text-ink-500 px-3 py-2 rounded-lg border border-ink-800 bg-ink-900/50">
        Synthese übersprungen — die Einzelantwort wurde direkt ausgegeben.
      </div>
    )
  }
  if (!synthesis.reasoning_text) return null
  return (
    <Disclosure
      tone="thinking"
      title={<span className="text-ink-300">Thinking der Syntheseantwort</span>}
      meta={
        <>
          <span>TTFT {ms(synthesis.ttft_ms)}</span>
          <span>{ms(synthesis.latency_ms)}</span>
          <span>{num(synthesis.prompt_tokens)} Tok</span>
        </>
      }
    >
      <div className="text-xs text-ink-400 whitespace-pre-wrap leading-relaxed font-mono max-h-96 overflow-y-auto">
        {synthesis.reasoning_text}
      </div>
    </Disclosure>
  )
}

export function MessageView({
  message,
  streaming = false,
}: {
  message: ChatMessage
  streaming?: boolean
}) {
  if (message.role === 'user') {
    return (
      <div className="flex justify-end animate-slide-up">
        <div className="max-w-[75%] rounded-2xl rounded-br-md bg-ink-800 border border-ink-750 px-4 py-2.5">
          <p className="text-[0.9375rem] leading-relaxed text-ink-100 whitespace-pre-wrap">
            {message.content}
          </p>
        </div>
      </div>
    )
  }

  const answers = message.collection_answers
  const totalPrompt = answers.reduce((sum, a) => sum + a.prompt_tokens, 0)
  const totalCached = answers.reduce((sum, a) => sum + a.cached_tokens, 0)
  const overallRate = totalPrompt ? totalCached / totalPrompt : 0

  return (
    <div className="animate-slide-up space-y-3">
      {message.content || streaming ? (
        <Markdown content={message.content} streaming={streaming} />
      ) : null}

      {(answers.length > 0 || message.synthesis) && (
        <div className="space-y-1.5 pt-1">
          <div className="flex items-center gap-2 text-2xs text-ink-500">
            <span className="uppercase tracking-wider">Details</span>
            <span className="h-px flex-1 bg-ink-800" />
            {totalPrompt > 0 && (
              <span className="tabular" title="Summe über alle Sammlungen dieser Antwort">
                {num(totalPrompt)} Prompt-Tokens · {pct(overallRate, 0)} aus dem Cache
              </span>
            )}
          </div>
          {answers.map((answer) => (
            <AnswerBlock key={answer.collection_id} answer={answer} />
          ))}
          {message.synthesis && <SynthesisBlock synthesis={message.synthesis} />}
        </div>
      )}
    </div>
  )
}
