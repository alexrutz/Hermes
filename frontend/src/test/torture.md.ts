/**
 * Markdown torture document.
 *
 * Every construct here broke, or plausibly could break, in the previous
 * version: GFM tables, nested lists mixing ordered and unordered, task lists,
 * strikethrough, autolinks, inline and display math, fenced code with and
 * without a language, blockquotes containing other blocks, and wide tables that
 * must scroll inside their own container.
 */
export const TORTURE = `# Quartalsbericht

Ein Absatz mit **fett**, *kursiv*, ~~durchgestrichen~~, \`inline code\` und einem
Autolink: https://docs.sglang.io/ sowie einem [normalen Link](https://example.com).

## Tabelle mit Ausrichtung

| Sammlung | Seiten | Tokens | Trefferquote |
| :------- | -----: | -----: | :----------: |
| Verträge |     42 | 90.174 |        0,97  |
| Rechnungen |   18 | 38.646 |        0,94  |
| Sonstiges |     7 | 15.029 |        —     |

## Breite Tabelle (muss horizontal scrollen)

| Dokument | Seite | Feld | Wert | Einheit | Quelle | Erfasst | Prüfer | Status | Kommentar |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| mietvertrag_2024_final_unterschrieben.pdf | 3 | Kaltmiete | 1.200,00 | EUR | §4 Abs. 1 | ja | MK | geprüft | keine |
| nebenkostenabrechnung_2023.pdf | 12 | Nachzahlung | 348,20 | EUR | Zeile 44 | ja | MK | offen | Widerspruch läuft |

## Verschachtelte Listen

1. Erste Ebene
   - Zweite Ebene mit \`code\`
     1. Dritte Ebene
        - Vierte Ebene
   - Noch ein Punkt

     Ein Absatz innerhalb des Listenpunkts.
2. Zurück auf erster Ebene
   > Ein Blockquote in einer Liste
   >
   > | a | b |
   > | - | - |
   > | 1 | 2 |

## Aufgabenliste

- [x] Präfix-Determinismus getestet
- [ ] Cache-Baum eingefärbt
  - [ ] Unteraufgabe

## Blockquote

> Der KV-Cache überlebt den Neustart nur mit \`write_through\`.
>
> — Doku

## Mathe

Inline: der Bildpräfix kostet $\\lceil w/32 \\rceil \\cdot \\lceil h/32 \\rceil$ Tokens.

Display:

$$
T_{\\text{Seite}} = \\left\\lceil \\frac{w}{p \\cdot m} \\right\\rceil \\cdot \\left\\lceil \\frac{h}{p \\cdot m} \\right\\rceil + 2
$$

## Code

\`\`\`python
def estimate(width: int, height: int, factor: int = 32) -> int:
    """Vision-Tokens für ein Bild."""
    return (width // factor) * (height // factor) + 2
\`\`\`

\`\`\`
Codeblock ohne Sprachangabe
  mit Einrückung
\`\`\`

---

Letzter Absatz nach einer horizontalen Linie.
`

/** Prefixes that simulate a stream arriving one chunk at a time. */
export function streamPrefixes(text: string, chunk = 37): string[] {
  const out: string[] = []
  for (let i = chunk; i < text.length; i += chunk) out.push(text.slice(0, i))
  out.push(text)
  return out
}
