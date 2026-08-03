/**
 * Make partially-streamed markdown safe to render on every frame.
 *
 * The failure mode this exists to prevent: a half-arrived table renders as a
 * paragraph of pipes, then snaps into a table; a half-arrived code fence renders
 * as an italic mess, then snaps into a code block. Both look like flicker.
 *
 * Two strategies, picked per construct:
 *
 *  - **Close it** when the construct renders correctly as soon as it is closed
 *    and its content is already meaningful — an open code fence just gets a
 *    synthetic closing fence, so code appears line by line inside a stable
 *    block.
 *  - **Hold it back** when a partial version renders as something *different*
 *    from the finished version — a table header without its delimiter row, an
 *    unterminated link, an odd `$$`. A few characters arriving one frame late
 *    is invisible; a paragraph turning into a table is not.
 *
 * Called with `streaming: false` the input is returned untouched, so the final
 * rendered output is never altered.
 */

const FENCE = /^(\s{0,3})(`{3,}|~{3,})(.*)$/
const TABLE_LINE = /^\s{0,3}\|/
/** A GFM delimiter row: `| --- | :--: |`, at least one dash, only cell syntax. */
const DELIMITER_ROW = /^\s{0,3}\|?(?:\s*:?-+:?\s*\|)+\s*:?-*:?\s*\|?\s*$/

export interface StabilizeResult {
  text: string
  /** True when something was withheld — the caller may show a typing cursor. */
  pending: boolean
}

export function stabilizeMarkdown(raw: string, streaming: boolean): StabilizeResult {
  if (!streaming || !raw) return { text: raw, pending: false }

  const fence = openFence(raw)
  if (fence) {
    // Inside a code block: close it rather than hide it. Code is readable while
    // it streams and the block's own frame never changes.
    const needsNewline = raw.endsWith('\n') ? '' : '\n'
    return { text: `${raw}${needsNewline}${fence}`, pending: true }
  }

  let text = raw
  let pending = false

  const withheld = (next: string) => {
    if (next !== text) pending = true
    text = next
  }

  withheld(trimIncompleteTable(text))
  withheld(trimUnbalanced(text, '$$'))
  withheld(trimIncompleteLink(text))
  withheld(trimUnbalancedBackticks(text))
  withheld(trimTrailingEmphasis(text))

  return { text, pending }
}

/** The closing marker for a still-open fence, or null when balanced. */
function openFence(text: string): string | null {
  let marker: string | null = null
  for (const line of text.split('\n')) {
    const match = FENCE.exec(line)
    if (!match) continue
    const token = match[2]
    if (marker === null) {
      marker = token[0]!.repeat(Math.max(3, token.length))
    } else if (token[0] === marker[0] && token.length >= marker.length && !match[3].trim()) {
      marker = null
    }
  }
  return marker
}

/**
 * Withhold a trailing table that cannot render as a table yet.
 *
 * A GFM table needs its delimiter row. Until it arrives, remark renders the
 * header as an ordinary paragraph — which is exactly the flicker we are
 * avoiding.
 */
function trimIncompleteTable(text: string): string {
  const lines = text.split('\n')
  let start = lines.length
  while (start > 0 && TABLE_LINE.test(lines[start - 1]!)) start -= 1
  if (start === lines.length) return text // no trailing table lines at all

  const block = lines.slice(start)
  const hasDelimiter = block.length >= 2 && DELIMITER_ROW.test(block[1]!)
  if (hasDelimiter) return text // renders as a table already; rows stream fine

  // Header alone, or a delimiter row still arriving: hold the whole block.
  return lines.slice(0, start).join('\n')
}

/** Withhold from the last unmatched occurrence of a paired token. */
function trimUnbalanced(text: string, token: string): string {
  const parts = text.split(token)
  if (parts.length % 2 === 1) return text // even number of tokens: balanced
  return parts.slice(0, -1).join(token)
}

function trimUnbalancedBackticks(text: string): string {
  // Only inspect the tail after the last newline: an unmatched backtick earlier
  // in the text has already been resolved by remark one way or another.
  const cut = text.lastIndexOf('\n') + 1
  const head = text.slice(0, cut)
  const tail = text.slice(cut)
  const ticks = tail.split('`')
  if (ticks.length % 2 === 1) return text
  return head + ticks.slice(0, -1).join('`')
}

/** Withhold `[label](partial-url` and `![alt](…` until the closing paren. */
function trimIncompleteLink(text: string): string {
  const open = Math.max(text.lastIndexOf('['), text.lastIndexOf('!['))
  if (open === -1) return text
  const rest = text.slice(open)
  if (rest.includes('\n')) return text // links do not span lines here
  const close = rest.indexOf(']')
  if (close === -1) return text.slice(0, open)
  const after = rest.slice(close + 1)
  if (after.startsWith('(') && !after.includes(')')) return text.slice(0, open)
  return text
}

/**
 * Withhold a dangling emphasis marker at the very end.
 *
 * `Der **` would otherwise render the asterisks literally for one frame before
 * they turn into bold.
 */
function trimTrailingEmphasis(text: string): string {
  const match = /(\*{1,3}|_{1,3}|~{1,2})$/.exec(text)
  if (!match) return text
  const marker = match[1]!
  const body = text.slice(0, text.length - marker.length)
  // An even number of occurrences before the tail means this one opens a new
  // span that has not been closed yet.
  const occurrences = body.split(marker).length - 1
  return occurrences % 2 === 0 ? body : text
}

/** Split assistant output into stable prefix + still-changing tail. */
export function isBalanced(text: string): boolean {
  return stabilizeMarkdown(text, true).text === text
}
