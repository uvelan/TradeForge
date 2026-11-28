export type DockSide = 'right' | 'left';

export const DEFAULT_DOCK_WIDTH = 320;
export const MIN_DOCK_WIDTH = 220;
export const MAX_DOCK_WIDTH = 640;
export const MIN_CORE_WIDTH = 360;
export const SPLITTER_WIDTH = 10;

export interface DockBounds {
  min: number;
  max: number;
  defaultWidth: number;
}

export function getDockBounds(containerWidth: number): DockBounds {
  const usable = Number.isFinite(containerWidth) && containerWidth > 0 ? containerWidth : 1280;
  const maxByViewport = Math.floor(usable - MIN_CORE_WIDTH - SPLITTER_WIDTH);
  const max = Math.max(MIN_DOCK_WIDTH, Math.min(MAX_DOCK_WIDTH, maxByViewport));
  const min = Math.min(MIN_DOCK_WIDTH, max);
  const defaultWidth = Math.min(max, Math.max(min, DEFAULT_DOCK_WIDTH));
  return { min, max, defaultWidth };
}

export function clampDockWidth(raw: number | string | null | undefined, containerWidth: number): number {
  const bounds = getDockBounds(containerWidth);
  if (raw === null || raw === undefined || raw === '' || raw === 'auto') {
    return bounds.defaultWidth;
  }
  const numeric = typeof raw === 'number' ? raw : Number(raw);
  if (!Number.isFinite(numeric) || numeric <= 0) {
    return bounds.defaultWidth;
  }
  return Math.round(Math.min(bounds.max, Math.max(bounds.min, numeric)));
}

export function normalizeDockSide(raw: string | null | undefined): DockSide {
  return raw === 'left' ? 'left' : 'right';
}

export function stepDockWidthForKey(
  key: string,
  shiftKey: boolean,
  currentWidth: number,
  side: DockSide,
  containerWidth: number,
): number | undefined {
  const bounds = getDockBounds(containerWidth);
  const delta = shiftKey ? 48 : 16;
  if (key === 'Home') return bounds.min;
  if (key === 'End') return bounds.max;
  if (key !== 'ArrowLeft' && key !== 'ArrowRight') return undefined;
  // Moving the divider physically left increases a right-docked inspector and decreases a left-docked one.
  const direction = key === 'ArrowLeft' ? (side === 'right' ? 1 : -1) : (side === 'right' ? -1 : 1);
  return clampDockWidth(currentWidth + direction * delta, containerWidth);
}
