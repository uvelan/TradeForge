import type { DrawingTool } from './Drawings';
import type { Timeframe } from './types';

export const TERMINAL_TIMEFRAME_KEYS: ReadonlyArray<readonly [key: string, timeframe: Timeframe]> = [
  ['1', 'M1'],
  ['2', 'M3'],
  ['3', 'M5'],
  ['4', 'M15'],
  ['5', 'M30'],
  ['6', 'H1'],
  ['7', 'H4'],
  ['8', 'D1'],
];

export const TERMINAL_DRAWING_SHORTCUTS: ReadonlyArray<readonly [key: string, tool: DrawingTool, label: string]> = [
  ['t', 'trend', 'Trendline'],
  ['r', 'ray', 'Ray'],
  ['g', 'fib', 'Fibonacci retracement'],
  ['b', 'rectangle', 'Rectangle'],
  ['p', 'risk-reward', 'Risk/reward'],
];

export const TERMINAL_SHORTCUT_GUIDE: ReadonlyArray<readonly [binding: string, action: string]> = [
  ['F', 'Fit focused chart'],
  ['E', 'Bookmark evidence candle'],
  ['Alt + H', 'Horizontal level'],
  ['T / R / G / B / P', 'Trend / Ray / Fib / Box / R:R'],
  ['1 – 8', 'Timeframes M1 – D1 (unfrozen pane)'],
  ['Space', 'Play / pause replay'],
  ['Shift + ← / →', 'Step replay candle'],
  ['Esc', 'Cancel drawing / crosshair'],
];

export type TerminalShortcutAction =
  | { type: 'escape' }
  | { type: 'level' }
  | { type: 'fit' }
  | { type: 'bookmark' }
  | { type: 'tool'; tool: DrawingTool }
  | { type: 'timeframe'; timeframe: Timeframe };

export interface ShortcutGuardContext {
  defaultPrevented: boolean;
  repeat: boolean;
  key: string;
  altKey: boolean;
  ctrlKey: boolean;
  metaKey: boolean;
  shiftKey: boolean;
  insideModal: boolean;
  insideEditable: boolean;
  hasOpenModalDialog: boolean;
  replayActive: boolean;
  focusedPaneFrozen: boolean;
  canDraw: boolean;
}

export function isEditableOrModalElement(target: EventTarget | null): { insideModal: boolean; insideEditable: boolean; hasOpenModalDialog: boolean } {
  const hasOpenModalDialog = typeof document !== 'undefined' && Boolean(document.querySelector('dialog[open]:not([role="region"]), [role="dialog"]'));
  if (!(target instanceof Element)) {
    return { insideModal: false, insideEditable: false, hasOpenModalDialog };
  }
  const insideModal = Boolean(target.closest('dialog:not([role="region"]), [role="dialog"], .drawer, .small-modal'));
  const insideEditable = Boolean(target.closest('input, textarea, select, [contenteditable], [contenteditable="true"]'));
  return { insideModal, insideEditable, hasOpenModalDialog };
}

export function resolveTerminalShortcut(ctx: ShortcutGuardContext): TerminalShortcutAction | undefined {
  if (ctx.defaultPrevented || ctx.repeat) return undefined;
  if (ctx.key === 'Escape') {
    if (ctx.insideModal || ctx.hasOpenModalDialog) return undefined;
    return { type: 'escape' };
  }
  if (ctx.insideEditable || ctx.insideModal || ctx.hasOpenModalDialog) return undefined;
  if (ctx.ctrlKey || ctx.metaKey) return undefined;

  const lower = ctx.key.toLowerCase();

  if (ctx.altKey && !ctx.shiftKey && lower === 'h') {
    return { type: 'level' };
  }
  if (ctx.altKey || ctx.shiftKey) return undefined;

  if (lower === 'f') return { type: 'fit' };
  if (lower === 'e') return ctx.replayActive ? { type: 'bookmark' } : undefined;

  for (const [key, tool] of TERMINAL_DRAWING_SHORTCUTS) {
    if (lower === key) {
      return ctx.canDraw ? { type: 'tool', tool } : undefined;
    }
  }

  for (const [key, timeframe] of TERMINAL_TIMEFRAME_KEYS) {
    if (lower === key) {
      return ctx.focusedPaneFrozen ? undefined : { type: 'timeframe', timeframe };
    }
  }

  return undefined;
}
