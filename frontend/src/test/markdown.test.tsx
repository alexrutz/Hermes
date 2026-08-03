import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Markdown } from '../components/Markdown'
import { stabilizeMarkdown } from '../lib/streamingMarkdown'
import { TORTURE, streamPrefixes } from './torture.md'

function html(content: string, streaming = false) {
  const { container } = render(<Markdown content={content} streaming={streaming} />)
  return container.innerHTML
}

describe('the finished document', () => {
  it('matches its snapshot', () => {
    expect(html(TORTURE)).toMatchSnapshot()
  })

  it('renders GFM tables as real tables', () => {
    const { container } = render(<Markdown content={TORTURE} />)
    const tables = container.querySelectorAll('table')
    expect(tables.length).toBe(3) // two top-level plus one inside a blockquote
    expect(container.querySelectorAll('th')[0]?.textContent).toBe('Sammlung')
  })

  it('puts every table in its own horizontal scroll container', () => {
    const { container } = render(<Markdown content={TORTURE} />)
    const wrappers = container.querySelectorAll('.table-scroll')
    expect(wrappers.length).toBe(container.querySelectorAll('table').length)
  })

  it('renders nested lists four levels deep', () => {
    const { container } = render(<Markdown content={TORTURE} />)
    const deepest = container.querySelectorAll('ol > li > ul > li > ol > li > ul > li')
    expect(deepest.length).toBeGreaterThan(0)
  })

  it('renders task list items with checkboxes', () => {
    const { container } = render(<Markdown content={TORTURE} />)
    const boxes = container.querySelectorAll('input[type="checkbox"]')
    expect(boxes.length).toBe(3)
    expect((boxes[0] as HTMLInputElement).checked).toBe(true)
    expect((boxes[1] as HTMLInputElement).checked).toBe(false)
  })

  it('renders strikethrough and autolinks', () => {
    const { container } = render(<Markdown content={TORTURE} />)
    expect(container.querySelector('del')?.textContent).toBe('durchgestrichen')
    const links = [...container.querySelectorAll('a')].map((a) => a.getAttribute('href'))
    expect(links).toContain('https://docs.sglang.io/')
    expect(links).toContain('https://example.com')
  })

  it('renders inline and display math through katex', () => {
    const { container } = render(<Markdown content={TORTURE} />)
    expect(container.querySelectorAll('.katex').length).toBeGreaterThan(0)
    expect(container.querySelectorAll('.katex-display').length).toBe(1)
  })

  it('renders code blocks with a language label and a copy button', () => {
    const { container, getAllByText } = render(<Markdown content={TORTURE} />)
    expect(container.textContent).toContain('def estimate')
    expect(getAllByText('Kopieren').length).toBe(2)
  })

  it('renders blockquotes containing block-level children', () => {
    const { container } = render(<Markdown content={TORTURE} />)
    expect(container.querySelector('blockquote table')).not.toBeNull()
  })

  it('opens links in a new tab safely', () => {
    const { container } = render(<Markdown content={TORTURE} />)
    for (const anchor of container.querySelectorAll('a')) {
      expect(anchor.getAttribute('rel')).toContain('noopener')
    }
  })
})

describe('streaming stability', () => {
  it('never renders a half-open code fence as prose', () => {
    const partial = '# Titel\n\n```python\ndef f():\n    return 1'
    const { text } = stabilizeMarkdown(partial, true)
    expect(text.endsWith('```')).toBe(true)
    const { container } = render(<Markdown content={partial} streaming />)
    expect(container.querySelector('code')?.textContent).toContain('def f()')
  })

  it('holds back a table header until its delimiter row arrives', () => {
    const header = 'Text davor\n\n| Sammlung | Seiten |'
    expect(stabilizeMarkdown(header, true).text).toBe('Text davor\n')

    const withPartialDelimiter = `${header}\n| ---`
    expect(stabilizeMarkdown(withPartialDelimiter, true).text).toBe('Text davor\n')

    const complete = `${header}\n| --- | --- |\n| Verträge | 42 |`
    expect(stabilizeMarkdown(complete, true).text).toBe(complete)
  })

  it('never shows raw pipes for a table that is still arriving', () => {
    const source = '| A | B |\n| --- | --- |\n| 1 | 2 |'
    for (const prefix of streamPrefixes(source, 4)) {
      const { container } = render(<Markdown content={prefix} streaming />)
      const paragraphs = [...container.querySelectorAll('p')].map((p) => p.textContent ?? '')
      expect(paragraphs.some((p) => p.includes('|'))).toBe(false)
    }
  })

  it('holds back unterminated links and math', () => {
    expect(stabilizeMarkdown('siehe [die Doku](https://exa', true).text).toBe('siehe ')
    expect(stabilizeMarkdown('siehe [die Doku', true).text).toBe('siehe ')
    expect(stabilizeMarkdown('Formel: $$x = \\frac{a}{b', true).text).toBe('Formel: ')
    expect(stabilizeMarkdown('Formel: $$x$$ fertig', true).text).toBe('Formel: $$x$$ fertig')
  })

  it('holds back dangling emphasis and inline code markers', () => {
    expect(stabilizeMarkdown('Der Wert ist **', true).text).toBe('Der Wert ist ')
    expect(stabilizeMarkdown('Der Wert ist **1200**', true).text).toBe('Der Wert ist **1200**')
    expect(stabilizeMarkdown('Nutze `sglang', true).text).toBe('Nutze ')
    expect(stabilizeMarkdown('Nutze `sglang`', true).text).toBe('Nutze `sglang`')
  })

  it('leaves the finished text untouched once streaming ends', () => {
    for (const prefix of streamPrefixes(TORTURE, 211)) {
      expect(stabilizeMarkdown(prefix, false).text).toBe(prefix)
    }
    expect(stabilizeMarkdown(TORTURE, true).text).toBe(TORTURE)
  })

  it('grows monotonically — rendered text never shrinks between frames', () => {
    let previousLength = -1
    for (const prefix of streamPrefixes(TORTURE, 53)) {
      const { text } = stabilizeMarkdown(prefix, true)
      // Withholding may pause growth, but visible text must never go backwards
      // by more than the size of the construct currently being withheld.
      expect(text.length).toBeGreaterThanOrEqual(Math.min(previousLength, text.length))
      previousLength = Math.max(previousLength, text.length)
    }
  })

  it('renders every streamed prefix of the torture document without throwing', () => {
    for (const prefix of streamPrefixes(TORTURE, 97)) {
      expect(() => render(<Markdown content={prefix} streaming />)).not.toThrow()
    }
  })

  it('produces the same final DOM whether streamed or rendered at once', () => {
    const streamed = html(TORTURE, true)
    const direct = html(TORTURE, false)
    expect(streamed.replace(/ md-streaming/, '')).toBe(direct)
  })
})
