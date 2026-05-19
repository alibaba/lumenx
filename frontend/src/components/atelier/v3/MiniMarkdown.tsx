"use client";
import * as React from "react";

// MiniMarkdown — a deliberately small inline-markdown renderer for
// short Atelier text fields (project descriptions, idea/comment bodies).
// Supports the four most-used inline marks plus paragraph breaks:
//   **bold**   *italic*   `code`   [text](url)
// Multi-line input is split into <p> blocks; single newlines inside a
// block become <br>.
//
// Why not react-markdown / micromark? They drag in a parser graph (and
// HTML sanitizer) for ~50KB. We render at most a few sentences here —
// the regex pass is O(n) and the no-dep footprint is the right tradeoff.
//
// SAFETY: links use rel="noreferrer noopener" + target="_blank"; we never
// emit raw HTML, so a description containing literal `<script>` shows
// the text "<script>" rather than executing.

interface InlineToken {
  kind: "text" | "bold" | "italic" | "code" | "link";
  body: string;
  href?: string;
}

// Single-pass tokenizer. Order matters: `**bold**` must be tried
// before `*italic*` so a single `*` doesn't eat both `*`s of a bold
// mark. Code spans are tried first so they don't get further parsed.
function tokenizeInline(text: string): InlineToken[] {
  const tokens: InlineToken[] = [];
  let i = 0;
  const len = text.length;
  let buffer = "";
  const flush = () => {
    if (buffer.length > 0) {
      tokens.push({ kind: "text", body: buffer });
      buffer = "";
    }
  };
  while (i < len) {
    const ch = text[i];
    // Inline code: `...`
    if (ch === "`") {
      const end = text.indexOf("`", i + 1);
      if (end > i) {
        flush();
        tokens.push({ kind: "code", body: text.slice(i + 1, end) });
        i = end + 1;
        continue;
      }
    }
    // Bold: **...**
    if (ch === "*" && text[i + 1] === "*") {
      const end = text.indexOf("**", i + 2);
      if (end > i + 1) {
        flush();
        tokens.push({ kind: "bold", body: text.slice(i + 2, end) });
        i = end + 2;
        continue;
      }
    }
    // Italic: *...*
    if (ch === "*") {
      const end = text.indexOf("*", i + 1);
      if (end > i) {
        flush();
        tokens.push({ kind: "italic", body: text.slice(i + 1, end) });
        i = end + 1;
        continue;
      }
    }
    // Link: [text](url)
    if (ch === "[") {
      const closeBracket = text.indexOf("]", i + 1);
      if (closeBracket > i && text[closeBracket + 1] === "(") {
        const closeParen = text.indexOf(")", closeBracket + 2);
        if (closeParen > closeBracket + 1) {
          flush();
          tokens.push({
            kind: "link",
            body: text.slice(i + 1, closeBracket),
            href: text.slice(closeBracket + 2, closeParen),
          });
          i = closeParen + 1;
          continue;
        }
      }
    }
    buffer += ch;
    i += 1;
  }
  flush();
  return tokens;
}

function renderTokens(tokens: InlineToken[]): React.ReactNode[] {
  return tokens.map((t, i) => {
    if (t.kind === "bold") {
      return (
        <strong key={i} className="font-semibold text-foreground">
          {t.body}
        </strong>
      );
    }
    if (t.kind === "italic") {
      return (
        <em key={i} className="italic">
          {t.body}
        </em>
      );
    }
    if (t.kind === "code") {
      return (
        <code
          key={i}
          className="rounded border border-white/8 bg-black/35 px-1 py-[1px] font-mono text-[11px] text-foreground/95"
        >
          {t.body}
        </code>
      );
    }
    if (t.kind === "link" && t.href) {
      // Allow http(s), mailto, and relative links. Reject javascript:
      // and data: schemes — even though we never inject raw HTML, the
      // anchor's href can still navigate.
      const safe = /^(https?:|mailto:|\/|#)/.test(t.href.trim());
      if (!safe) return <span key={i}>{t.body}</span>;
      return (
        <a
          key={i}
          href={t.href}
          target="_blank"
          rel="noreferrer noopener"
          className="text-primary underline decoration-primary/40 underline-offset-2 hover:decoration-primary/80"
        >
          {t.body}
        </a>
      );
    }
    return <React.Fragment key={i}>{t.body}</React.Fragment>;
  });
}

export function MiniMarkdown({
  source,
  className = "",
}: {
  source: string;
  className?: string;
}) {
  // Split into paragraphs on blank lines; preserve single-newlines as
  // <br/> within a paragraph. Empty input returns nothing — caller
  // decides whether to render a placeholder.
  const paragraphs = source.replace(/\r\n/g, "\n").split(/\n{2,}/);
  return (
    <div className={className}>
      {paragraphs.map((para, pi) => {
        const lines = para.split("\n");
        return (
          <p key={pi} className={pi > 0 ? "mt-1.5" : ""}>
            {lines.map((line, li) => (
              <React.Fragment key={li}>
                {li > 0 ? <br /> : null}
                {renderTokens(tokenizeInline(line))}
              </React.Fragment>
            ))}
          </p>
        );
      })}
    </div>
  );
}
