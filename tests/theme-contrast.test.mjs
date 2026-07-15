import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const themes = {
  light: {
    background: '#f9fafb', text: '#111827', muted: '#6b7280',
    success: '#10b981', warning: '#f59e0b', error: '#ef4444', info: '#3b82f6', purple: '#7c3aed',
  },
  dark: {
    background: '#1a1a1a', text: '#ffffff', muted: '#b3b3b3',
    success: '#4ade80', warning: '#fbbf24', error: '#ef4444', info: '#60a5fa', purple: '#a78bfa',
  },
  // Crystal Dark currently exposes the standard dark semantic variables to
  // extension editors while applying its glass treatment in the host shell.
  'crystal-dark': {
    background: '#1a1a1a', text: '#ffffff', muted: '#b3b3b3',
    success: '#4ade80', warning: '#fbbf24', error: '#ef4444', info: '#60a5fa', purple: '#a78bfa',
  },
  'midnight-orchid': {
    background: '#241334', text: '#f4ecff', muted: '#c4b8d8',
    success: '#86efac', warning: '#fde68a', error: '#fca5a5', info: '#a5b4fc', purple: '#c084fc',
  },
};

function channels(hex) {
  return [1, 3, 5].map((start) => Number.parseInt(hex.slice(start, start + 2), 16));
}

function luminance(hex) {
  const values = channels(hex).map((value) => {
    const normalized = value / 255;
    return normalized <= 0.04045 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * values[0] + 0.7152 * values[1] + 0.0722 * values[2];
}

function contrast(left, right) {
  const [bright, dark] = [luminance(left), luminance(right)].sort((a, b) => b - a);
  return (bright + 0.05) / (dark + 0.05);
}

function mix(foreground, background, amount) {
  const foregroundChannels = channels(foreground);
  const backgroundChannels = channels(background);
  const mixed = foregroundChannels.map((value, index) => Math.round(
    value * amount + backgroundChannels[index] * (1 - amount),
  ));
  return `#${mixed.map((value) => value.toString(16).padStart(2, '0')).join('')}`;
}

function lightStroke(semantic) {
  return mix(semantic, '#000000', 0.75);
}

test('semantic tints retain WCAG AA text contrast in every available Nimbalyst theme', () => {
  for (const [themeName, theme] of Object.entries(themes)) {
    assert.ok(contrast(theme.text, theme.background) >= 4.5, `${themeName}: primary text`);
    assert.ok(contrast(theme.muted, theme.background) >= 4.5, `${themeName}: muted text`);
    for (const semantic of ['success', 'warning', 'error', 'info', 'purple']) {
      const tintedBackground = mix(theme[semantic], theme.background, 0.18);
      assert.ok(
        contrast(theme.text, tintedBackground) >= 4.5,
        `${themeName}: text on ${semantic} tint`,
      );
    }
  }
});

test('relationship and state strokes retain non-text contrast', () => {
  for (const [themeName, theme] of Object.entries(themes)) {
    for (const semantic of ['success', 'warning', 'error', 'info', 'purple', 'muted']) {
      const stroke = themeName === 'light' && ['success', 'warning'].includes(semantic)
        ? lightStroke(theme[semantic])
        : theme[semantic];
      assert.ok(
        contrast(stroke, theme.background) >= 3,
        `${themeName}: ${semantic} stroke`,
      );
    }
  }
});

test('timeline styles use host semantic tokens without white-on-status text', async () => {
  const css = await readFile(new URL('../src/timeline/styles.css', import.meta.url), 'utf8');
  for (const token of ['--nim-success', '--nim-warning', '--nim-error', '--nim-info', '--nim-purple']) {
    assert.match(css, new RegExp(token));
  }
  assert.doesNotMatch(css, /color:\s*(?:white|#fff(?:fff)?)(?:\s*[;!])/i);
  assert.match(css, /\.nt-gantt-label[^}]*color:\s*var\(--nim-text\)/);
  assert.match(css, /\.nt-state-stack strong[^}]*color:\s*var\(--nim-text\)/);
  assert.match(css, /\.edge-contributes-to\s*{[^}]*var\(--nt-purple\)/);
  assert.match(css, /\.edge-reviews\s*{[^}]*var\(--nt-info\)/);
  assert.match(css, /\.edge-evidences\s*{[^}]*var\(--nt-neutral\)/);
  assert.match(css, /\.nt-shell\[data-theme="light"\][^{]*{[^}]*--nt-on-track:[^}]*75%/);
});
