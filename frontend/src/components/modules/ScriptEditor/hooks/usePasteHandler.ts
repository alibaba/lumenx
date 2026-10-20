'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import { Editor } from '@tiptap/react';

// ─── Types ───────────────────────────────────────────────────────────────────

export interface PasteSuggestion {
  lineIndex: number;
  originalText: string;
  suggestedType: string;
  ruleId: string;
  /** For character_dialogue rule: extracted character name */
  characterName?: string;
  /** For character_dialogue rule: extracted dialogue text */
  dialogueText?: string;
}

export interface PasteAnalysis {
  hasFormattableContent: boolean;
  /** 0-1, matched lines / total non-empty lines */
  matchRate: number;
  suggestions: PasteSuggestion[];
}

// ─── L2 Heuristic Rules ─────────────────────────────────────────────────────

interface HeuristicRule {
  id: string;
  pattern: RegExp;
  suggest: string;
  /** Optional extractor for structured data from matches */
  extract?: (match: RegExpMatchArray) => Partial<PasteSuggestion>;
}

const HEURISTIC_RULES: HeuristicRule[] = [
  {
    id: 'scene_heading_western',
    // Match: INT/EXT at start (supports Japanese/CJK descriptions after prefix)
    pattern: /^(INT|EXT|INT\/EXT|内|外|内\/外)[.\s．]/i,
    suggest: 'SceneHeading',
  },
  {
    id: 'scene_heading_japanese',
    // Match: ○ at start (Japanese scene heading)
    pattern: /^○\s*.+/,
    suggest: 'SceneHeading',
  },
  {
    id: 'section_heading_japanese_bracket',
    // Match: 【…】 at start (Japanese section/chapter title, e.g. 【第一話】、【シーン1】)
    pattern: /^【.+】/,
    suggest: 'SceneHeading',
  },
  {
    id: 'transition_japanese',
    // Match: ◆ or ◇ at start (Japanese scene separator/transition)
    pattern: /^[◆◇]\s*.*/,
    suggest: 'Transition',
  },
  {
    id: 'transition',
    // Match: common transition words
    pattern: /^(切至|淡入|淡出|CUT TO|FADE IN|FADE OUT|DISSOLVE|SMASH CUT|叠化)[：:.\s]*$/i,
    suggest: 'Transition',
  },
  {
    id: 'parenthetical',
    // Match: action in parentheses
    pattern: /^[（(].+[）)]\s*$/,
    suggest: 'Parenthetical',
  },
  {
    id: 'character_dialogue',
    // Match: Name：Dialogue or Name:Dialogue (fullwidth/halfwidth colon)
    pattern: /^([^\s:：]{1,20})[：:]\s*(.+)$/,
    suggest: 'CharacterCue + Dialogue',
    extract: (match) => ({
      characterName: match[1],
      dialogueText: match[2],
    }),
  },
];

// ─── Analysis Logic ──────────────────────────────────────────────────────────

function analyzeText(text: string): PasteAnalysis {
  const lines = text.split('\n');
  const nonEmptyLines = lines.filter((l) => l.trim().length > 0);

  if (nonEmptyLines.length === 0) {
    return { hasFormattableContent: false, matchRate: 0, suggestions: [] };
  }

  const suggestions: PasteSuggestion[] = [];

  lines.forEach((line, lineIndex) => {
    const trimmed = line.trim();
    if (!trimmed) return;

    for (const rule of HEURISTIC_RULES) {
      const match = trimmed.match(rule.pattern);
      if (match) {
        const suggestion: PasteSuggestion = {
          lineIndex,
          originalText: trimmed,
          suggestedType: rule.suggest,
          ruleId: rule.id,
          ...(rule.extract ? rule.extract(match) : {}),
        };
        suggestions.push(suggestion);
        break; // First matching rule wins
      }
    }
  });

  const matchRate = nonEmptyLines.length > 0 ? suggestions.length / nonEmptyLines.length : 0;

  return {
    hasFormattableContent: matchRate > 0.3,
    matchRate,
    suggestions,
  };
}

// ─── Apply Formatting ────────────────────────────────────────────────────────

function buildFormattedContent(text: string, suggestions: PasteSuggestion[]) {
  const lines = text.split('\n');
  const suggestionByLine = new Map<number, PasteSuggestion>();
  suggestions.forEach((s) => suggestionByLine.set(s.lineIndex, s));

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const nodes: any[] = [];

  lines.forEach((line, idx) => {
    const trimmed = line.trim();
    if (!trimmed) return;

    const suggestion = suggestionByLine.get(idx);

    if (!suggestion) {
      // Unmatched lines → Action node
      nodes.push({
        type: 'action',
        content: [{ type: 'text', text: trimmed }],
      });
      return;
    }

    switch (suggestion.ruleId) {
      case 'scene_heading_western':
      case 'scene_heading_japanese':
      case 'section_heading_japanese_bracket':
        nodes.push({
          type: 'sceneHeading',
          attrs: { id: crypto.randomUUID() },
          content: [{ type: 'text', text: trimmed }],
        });
        break;

      case 'transition':
      case 'transition_japanese':
        nodes.push({
          type: 'transition',
          attrs: { type: 'custom' },
          content: [{ type: 'text', text: trimmed }],
        });
        break;

      case 'parenthetical':
        // Parenthetical → Action with centered attribute
        nodes.push({
          type: 'action',
          attrs: { centered: true },
          content: [{ type: 'text', text: trimmed }],
        });
        break;

      case 'character_dialogue':
        // Split into CharacterCue + Dialogue nodes
        if (suggestion.characterName) {
          nodes.push({
            type: 'characterCue',
            content: [{ type: 'text', text: suggestion.characterName }],
          });
        }
        if (suggestion.dialogueText) {
          nodes.push({
            type: 'dialogue',
            content: [{ type: 'text', text: suggestion.dialogueText }],
          });
        }
        break;

      default:
        nodes.push({
          type: 'action',
          content: [{ type: 'text', text: trimmed }],
        });
    }
  });

  return nodes;
}

// ─── Hook ────────────────────────────────────────────────────────────────────

const HINT_AUTO_DISMISS_MS = 5000;

export function usePasteHandler(editor: Editor | null) {
  const [showHint, setShowHint] = useState(false);
  const [analysis, setAnalysis] = useState<PasteAnalysis | null>(null);
  const [pastedContent, setPastedContent] = useState<string>('');
  const dismissTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Auto-dismiss the hint bar
  const startDismissTimer = useCallback(() => {
    if (dismissTimerRef.current) {
      clearTimeout(dismissTimerRef.current);
    }
    dismissTimerRef.current = setTimeout(() => {
      setShowHint(false);
    }, HINT_AUTO_DISMISS_MS);
  }, []);

  // Analyze pasted text and show hint if applicable
  const handlePaste = useCallback(
    (text: string) => {
      const result = analyzeText(text);
      setAnalysis(result);
      setPastedContent(text);

      if (result.hasFormattableContent) {
        setShowHint(true);
        startDismissTimer();
      }
    },
    [startDismissTimer]
  );

  // Apply formatting to editor
  const applyFormatting = useCallback(() => {
    if (!editor || !analysis || !pastedContent) return;

    const nodes = buildFormattedContent(pastedContent, analysis.suggestions);
    if (nodes.length === 0) return;

    // Replace editor content with structured nodes
    editor
      .chain()
      .focus()
      .insertContent(nodes)
      .run();

    setShowHint(false);
    if (dismissTimerRef.current) {
      clearTimeout(dismissTimerRef.current);
    }
  }, [editor, analysis, pastedContent]);

  // Dismiss hint bar
  const dismissHint = useCallback(() => {
    setShowHint(false);
    if (dismissTimerRef.current) {
      clearTimeout(dismissTimerRef.current);
    }
  }, []);

  // Register paste event handler on editor
  useEffect(() => {
    if (!editor) return;

    const handleEditorPaste = (_event: ClipboardEvent, _slice: unknown) => {
      // Get plain text from clipboard event
      const text = _event.clipboardData?.getData('text/plain');
      if (text && text.trim().length > 0) {
        // Only analyze multi-line content (single lines are unlikely scripts)
        const lineCount = text.split('\n').filter((l) => l.trim().length > 0).length;
        if (lineCount >= 2) {
          handlePaste(text);
        }
      }
      // Return false to let default paste behavior proceed
      return false;
    };

    // Use Tiptap's paste handler via the ProseMirror plugin interface
    // We hook into the 'paste' DOM event on the editor element
    const editorElement = editor.view.dom;
    const onPaste = (event: Event) => {
      const clipboardEvent = event as ClipboardEvent;
      const text = clipboardEvent.clipboardData?.getData('text/plain');
      if (text && text.trim().length > 0) {
        const lineCount = text.split('\n').filter((l) => l.trim().length > 0).length;
        if (lineCount >= 2) {
          handlePaste(text);
        }
      }
      // Don't prevent default - let Tiptap handle the actual paste
    };

    editorElement.addEventListener('paste', onPaste);

    return () => {
      editorElement.removeEventListener('paste', onPaste);
      if (dismissTimerRef.current) {
        clearTimeout(dismissTimerRef.current);
      }
    };
  }, [editor, handlePaste]);

  return { showHint, analysis, applyFormatting, dismissHint };
}
