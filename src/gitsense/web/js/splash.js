// Takes down the loading splash that index.html shows from its first frame.

const MIN_VISIBLE_MS = 1300; // long enough for the rider to rock once each way
const FONT_WAIT_MS = 1500;
const FADE_MS = 300;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Fade the splash out and remove it.
 * - Waits until the fonts are loaded, so text does not shift after the reveal.
 * - Keeps the splash up for a minimum time so it never just flickers past.
 * - With "reduce motion" set, there is no minimum and no fade.
 */
export async function hideSplash() {
  const el = document.getElementById('splash');
  if (!el) return;
  const calm = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const remaining = calm ? 0 : Math.max(0, MIN_VISIBLE_MS - performance.now());
  await Promise.all([
    sleep(remaining),
    Promise.race([document.fonts ? document.fonts.ready : Promise.resolve(), sleep(FONT_WAIT_MS)]),
  ]);
  el.classList.add('sp-out');
  await sleep(calm ? 0 : FADE_MS);
  el.remove();
}
