import { describe, expect, it } from 'vitest';
import { resolveTerminalShortcut, type ShortcutGuardContext } from './terminalKeyboard';

function ctx(overrides: Partial<ShortcutGuardContext> = {}): ShortcutGuardContext {
  return {
    defaultPrevented: false,
    repeat: false,
    key: '',
    altKey: false,
    ctrlKey: false,
    metaKey: false,
    shiftKey: false,
    insideModal: false,
    insideEditable: false,
    hasOpenModalDialog: false,
    replayActive: false,
    focusedPaneFrozen: false,
    canDraw: true,
    ...overrides,
  };
}

describe('terminalKeyboard', () => {
  it('resolves F, E, Alt+H, drawing tools, and timeframe shortcuts safely', () => {
    expect(resolveTerminalShortcut(ctx({ key: 'f' }))).toEqual({ type: 'fit' });
    expect(resolveTerminalShortcut(ctx({ key: 'e', replayActive: false }))).toBeUndefined();
    expect(resolveTerminalShortcut(ctx({ key: 'e', replayActive: true }))).toEqual({ type: 'bookmark' });
    expect(resolveTerminalShortcut(ctx({ key: 'h', altKey: true }))).toEqual({ type: 'level' });
    expect(resolveTerminalShortcut(ctx({ key: 'g' }))).toEqual({ type: 'tool', tool: 'fib' });
    expect(resolveTerminalShortcut(ctx({ key: 'r' }))).toEqual({ type: 'tool', tool: 'ray' });
    expect(resolveTerminalShortcut(ctx({ key: '6' }))).toEqual({ type: 'timeframe', timeframe: 'H1' });
  });

  it('blocks held keys, editable fields, dialogs, modifiers, and frozen replay timeframe switches', () => {
    expect(resolveTerminalShortcut(ctx({ key: 'f', repeat: true }))).toBeUndefined();
    expect(resolveTerminalShortcut(ctx({ key: 'f', insideEditable: true }))).toBeUndefined();
    expect(resolveTerminalShortcut(ctx({ key: 'f', insideModal: true }))).toBeUndefined();
    expect(resolveTerminalShortcut(ctx({ key: 'f', hasOpenModalDialog: true }))).toBeUndefined();
    expect(resolveTerminalShortcut(ctx({ key: 'f', ctrlKey: true }))).toBeUndefined();
    expect(resolveTerminalShortcut(ctx({ key: '3', focusedPaneFrozen: true }))).toBeUndefined();
    expect(resolveTerminalShortcut(ctx({ key: 'g', canDraw: false }))).toBeUndefined();
    expect(resolveTerminalShortcut(ctx({ key: 'Escape', hasOpenModalDialog: true }))).toBeUndefined();
    expect(resolveTerminalShortcut(ctx({ key: 'Escape' }))).toEqual({ type: 'escape' });
  });
});
