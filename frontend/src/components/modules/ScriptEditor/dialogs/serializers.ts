import type { JSONContent } from '@tiptap/core'

// ═══════════════════════════════════════════════════════════════
// Tiptap JSON → Fountain format serializer
// ═══════════════════════════════════════════════════════════════

function getNodeText(node: JSONContent): string {
  if (node.text) return node.text
  if (!node.content) return ''
  return node.content.map(getNodeText).join('')
}

export function toFountain(doc: JSONContent): string {
  const lines: string[] = []
  const nodes = doc.content || []

  for (let i = 0; i < nodes.length; i++) {
    const node = nodes[i]
    const text = getNodeText(node)

    switch (node.type) {
      case 'sceneHeading': {
        // Blank line before scene heading
        if (i > 0) lines.push('')
        // Ensure uppercase for Fountain spec
        const heading = text.toUpperCase()
        // If it doesn't start with INT/EXT, prefix with a dot (forced scene heading)
        if (/^(INT|EXT|INT\/EXT|I\/E)[\.\s]/i.test(heading)) {
          lines.push(heading)
        } else {
          lines.push(`.${heading}`)
        }
        break
      }

      case 'action': {
        if (i > 0) lines.push('')
        lines.push(text)
        break
      }

      case 'characterCue': {
        // Blank line before character cue
        lines.push('')
        lines.push(text.toUpperCase())
        break
      }

      case 'dialogue': {
        // Dialogue text indented (Fountain doesn't require indent, just follows character)
        lines.push(text)
        break
      }

      case 'parenthetical': {
        // Parenthetical wrapped in parens
        const pText = text.startsWith('(') ? text : `(${text})`
        lines.push(pText.endsWith(')') ? pText : `${pText})`)
        break
      }

      case 'transition': {
        lines.push('')
        const tText = text.toUpperCase()
        // Fountain transitions end with colon or start with >
        if (tText.endsWith(':')) {
          lines.push(tText)
        } else {
          lines.push(`> ${tText}`)
        }
        break
      }

      case 'paragraph': {
        // Generic paragraph → treat as action
        if (i > 0 && text.trim()) lines.push('')
        lines.push(text)
        break
      }

      default: {
        // Unknown node types fallback to plain text
        if (text.trim()) {
          if (i > 0) lines.push('')
          lines.push(text)
        }
        break
      }
    }
  }

  return lines.join('\n') + '\n'
}

// ═══════════════════════════════════════════════════════════════
// Tiptap JSON → FDX (Final Draft XML) serializer
// ═══════════════════════════════════════════════════════════════

const NODE_TYPE_TO_FDX: Record<string, string> = {
  sceneHeading: 'Scene Heading',
  action: 'Action',
  characterCue: 'Character',
  dialogue: 'Dialogue',
  parenthetical: 'Parenthetical',
  transition: 'Transition',
  paragraph: 'Action',
}

function escapeXml(str: string): string {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;')
}

function renderTextRuns(node: JSONContent): string {
  if (!node.content || node.content.length === 0) {
    return '<Text></Text>'
  }

  return node.content
    .map((child) => {
      const text = escapeXml(child.text || '')
      const marks = child.marks || []
      let style = ''

      for (const mark of marks) {
        if (mark.type === 'bold') style += 'Bold+'
        if (mark.type === 'italic') style += 'Italic+'
        if (mark.type === 'underline') style += 'Underline+'
      }

      if (style) {
        style = style.slice(0, -1) // Remove trailing +
        return `<Text Style="${style}">${text}</Text>`
      }

      return `<Text>${text}</Text>`
    })
    .join('')
}

export function toFDX(doc: JSONContent): string {
  const paragraphs: string[] = []
  const nodes = doc.content || []

  for (const node of nodes) {
    const fdxType = NODE_TYPE_TO_FDX[node.type || 'paragraph'] || 'Action'
    const textRuns = renderTextRuns(node)
    paragraphs.push(`    <Paragraph Type="${fdxType}">\n      ${textRuns}\n    </Paragraph>`)
  }

  const xml = `<?xml version="1.0" encoding="UTF-8"?>
<FinalDraft DocumentType="Script" Template="No" Version="5">
  <Content>
${paragraphs.join('\n')}
  </Content>
</FinalDraft>`

  return xml
}

// ═══════════════════════════════════════════════════════════════
// Tiptap JSON → Plain text serializer
// ═══════════════════════════════════════════════════════════════

export function toPlainText(doc: JSONContent): string {
  const lines: string[] = []
  const nodes = doc.content || []

  for (const node of nodes) {
    const text = getNodeText(node)
    lines.push(text)
  }

  return lines.join('\n') + '\n'
}
