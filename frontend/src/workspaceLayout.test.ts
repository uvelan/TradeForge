import { describe, expect, it } from 'vitest';
import { clampDockWidth, getDockBounds, normalizeDockSide, stepDockWidthForKey } from './workspaceLayout';

describe('workspaceLayout', () => {
  it('clamps saved desktop inspector widths to the current viewport', () => {
    expect(clampDockWidth(580, 1400)).toBe(580);
    // At 900px container, chart core >= 360 and splitter = 10 -> max dock is 530px
    expect(clampDockWidth(580, 900)).toBe(530);
    // Invalid or auto values recover cleanly
    expect(clampDockWidth('auto', 1200)).toBe(320);
    expect(clampDockWidth('broken', 1200)).toBe(320);
    expect(clampDockWidth(50, 1200)).toBe(220);
  });

  it('computes keyboard resize steps respecting dock side and bounds', () => {
    const bounds = getDockBounds(1280);
    expect(bounds).toEqual({ min: 220, max: 640, defaultWidth: 320 });
    expect(stepDockWidthForKey('ArrowLeft', false, 320, 'right', 1280)).toBe(336);
    expect(stepDockWidthForKey('ArrowRight', true, 320, 'right', 1280)).toBe(272);
    expect(stepDockWidthForKey('ArrowLeft', false, 320, 'left', 1280)).toBe(304);
    expect(stepDockWidthForKey('ArrowRight', true, 320, 'left', 1280)).toBe(368);
    expect(stepDockWidthForKey('Home', false, 400, 'right', 1280)).toBe(220);
    expect(stepDockWidthForKey('End', false, 400, 'right', 1280)).toBe(640);
    expect(stepDockWidthForKey('Enter', false, 400, 'right', 1280)).toBeUndefined();
  });

  it('normalizes dock side preferences safely', () => {
    expect(normalizeDockSide('left')).toBe('left');
    expect(normalizeDockSide('right')).toBe('right');
    expect(normalizeDockSide('broken')).toBe('right');
    expect(normalizeDockSide(null)).toBe('right');
  });
});
