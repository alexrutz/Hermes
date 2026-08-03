import { memo, useMemo, useState, type ReactNode } from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import { stabilizeMarkdown } from '../lib/streamingMarkdown'
import { CodeBlock } from './CodeBlock'

interface Props {
  content: string
  streaming?: boolean
  className?: string
}

function textOf(node: ReactNode): string {
  if (node === null || node === undefined || typeof node === 'boolean') return ''
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(textOf).join('')
  if (typeof node === 'object' && 'props' in (node as any)) {
    return textOf((node as any).props?.children)
  }
  return ''
}

const components: Components = {
  // `node` is react-markdown's mdast node. It must be destructured away in every
  // custom component, otherwise it is spread onto the DOM element and React
  // emits `node="[object Object]"` into the markup.
  code({ node: _node, className, children, ...props }) {
    const language = /language-(\w+)/.exec(className ?? '')?.[1]
    const isBlock = className?.includes('language-') || textOf(children).includes('\n')
    if (!isBlock) {
      return (
        <code className={className} {...props}>
          {children}
        </code>
      )
    }
    return <CodeBlock language={language}>{textOf(children).replace(/\n$/, '')}</CodeBlock>
  },
  // react-markdown would wrap our own <pre> in a second <pre> otherwise.
  pre({ children }) {
    return <>{children}</>
  },
  table({ node: _node, children }) {
    // Wide tables scroll inside their own container; the page never does.
    return (
      <div className="table-scroll">
        <table>{children}</table>
      </div>
    )
  },
  li({ node: _node, children, className, ...props }) {
    const isTask = className?.includes('task-list-item')
    return (
      <li className={isTask ? 'task-item' : className} {...props}>
        {children}
      </li>
    )
  },
  a({ node: _node, children, href, ...props }) {
    return (
      <a href={href} target="_blank" rel="noopener noreferrer" {...props}>
        {children}
      </a>
    )
  },
}

export const Markdown = memo(function Markdown({ content, streaming, className }: Props) {
  const stabilized = useMemo(
    () => stabilizeMarkdown(content, Boolean(streaming)),
    [content, streaming],
  )

  const classes = ['md', streaming ? 'md-streaming' : '', className].filter(Boolean).join(' ')

  return (
    <div className={classes}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[[rehypeKatex, { throwOnError: false, strict: false }]]}
        components={components}
      >
        {stabilized.text}
      </ReactMarkdown>
    </div>
  )
})

export function CopyButton({ value, label = 'Kopieren' }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      type="button"
      className="btn-ghost !px-2 !py-1 text-2xs"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value)
          setCopied(true)
          setTimeout(() => setCopied(false), 1400)
        } catch {
          setCopied(false)
        }
      }}
    >
      {copied ? '✓ Kopiert' : label}
    </button>
  )
}
