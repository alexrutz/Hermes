import { useEffect, useState } from 'react'
import { CopyButton } from './Markdown'

/**
 * Syntax-highlighted code block.
 *
 * Shiki is loaded lazily and highlighting is applied *after* the plain text is
 * already on screen, so a code block never blocks the stream and never blanks
 * out while a highlighter warms up.
 */

/**
 * Built from `shiki/core` rather than the `shiki` barrel on purpose: the barrel
 * drags in the 600 kB oniguruma wasm engine and every bundled grammar. Here the
 * JavaScript regex engine replaces the wasm, and each grammar is a dynamic
 * import that only loads when a code block of that language actually appears.
 */

let highlighterPromise: Promise<any> | null = null

const GRAMMARS: Record<string, () => Promise<any>> = {
  bash: () => import('shiki/langs/bash.mjs'),
  c: () => import('shiki/langs/c.mjs'),
  cpp: () => import('shiki/langs/cpp.mjs'),
  csharp: () => import('shiki/langs/csharp.mjs'),
  css: () => import('shiki/langs/css.mjs'),
  diff: () => import('shiki/langs/diff.mjs'),
  docker: () => import('shiki/langs/docker.mjs'),
  go: () => import('shiki/langs/go.mjs'),
  html: () => import('shiki/langs/html.mjs'),
  ini: () => import('shiki/langs/ini.mjs'),
  java: () => import('shiki/langs/java.mjs'),
  javascript: () => import('shiki/langs/javascript.mjs'),
  json: () => import('shiki/langs/json.mjs'),
  markdown: () => import('shiki/langs/markdown.mjs'),
  python: () => import('shiki/langs/python.mjs'),
  rust: () => import('shiki/langs/rust.mjs'),
  sql: () => import('shiki/langs/sql.mjs'),
  toml: () => import('shiki/langs/toml.mjs'),
  tsx: () => import('shiki/langs/tsx.mjs'),
  typescript: () => import('shiki/langs/typescript.mjs'),
  xml: () => import('shiki/langs/xml.mjs'),
  yaml: () => import('shiki/langs/yaml.mjs'),
}

const LANGS = Object.keys(GRAMMARS)

async function getHighlighter() {
  if (!highlighterPromise) {
    highlighterPromise = Promise.all([
      import('shiki/core'),
      import('shiki/engine/javascript'),
      import('shiki/themes/github-dark-default.mjs'),
    ]).then(([core, engine, theme]) =>
      core.createHighlighterCore({
        themes: [theme.default],
        langs: [],
        engine: engine.createJavaScriptRegexEngine({ forgiving: true }),
      }),
    )
  }
  return highlighterPromise
}

const loaded = new Set<string>()

async function ensureLanguage(highlighter: any, lang: string) {
  if (loaded.has(lang)) return
  const grammar = await GRAMMARS[lang]!()
  await highlighter.loadLanguage(grammar.default)
  loaded.add(lang)
}

function normalizeLang(language?: string): string | null {
  if (!language) return null
  const alias: Record<string, string> = {
    js: 'javascript', ts: 'typescript', py: 'python', sh: 'bash', shell: 'bash',
    yml: 'yaml', rs: 'rust', kt: 'kotlin', cs: 'csharp', 'c++': 'cpp', md: 'markdown',
  }
  const resolved = alias[language.toLowerCase()] ?? language.toLowerCase()
  return LANGS.includes(resolved) ? resolved : null
}

export function CodeBlock({ language, children }: { language?: string; children: string }) {
  const [html, setHtml] = useState<string | null>(null)
  const lang = normalizeLang(language)

  useEffect(() => {
    let cancelled = false
    if (!lang) {
      setHtml(null)
      return
    }
    getHighlighter()
      .then(async (highlighter) => {
        await ensureLanguage(highlighter, lang)
        if (cancelled) return
        setHtml(
          highlighter.codeToHtml(children, { lang, theme: 'github-dark-default' }) as string,
        )
      })
      // Any failure leaves the plain-text rendering in place — a code block must
      // never disappear because highlighting could not be arranged.
      .catch(() => setHtml(null))
    return () => {
      cancelled = true
    }
  }, [children, lang])

  return (
    <div className="my-4 rounded-lg border border-ink-750 bg-ink-950 overflow-hidden group">
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-ink-800 bg-ink-900">
        <span className="text-2xs font-mono text-ink-400 uppercase tracking-wider">
          {language || 'text'}
        </span>
        <div className="opacity-0 group-hover:opacity-100 transition-opacity">
          <CopyButton value={children} />
        </div>
      </div>
      <div className="overflow-x-auto p-3 text-[0.8125rem] leading-relaxed font-mono">
        {html ? (
          <div className="shiki-host [&_pre]:!bg-transparent [&_pre]:!m-0" dangerouslySetInnerHTML={{ __html: html }} />
        ) : (
          <pre className="!bg-transparent !m-0 !p-0">
            <code>{children}</code>
          </pre>
        )}
      </div>
    </div>
  )
}
