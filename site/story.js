// Scroll-driven chapters. The chapter under the reading line (60 % down the screen) drives the stage: its handler gets
// (u, entering) — u = how far the line is through the chapter (0..1), entering = true on the first frame it is active.
// Above the first chapter the `before` handler runs, below the last one `after`. Handlers must be idempotent: scrolling
// back up re-enters a chapter and must restore its state.
export function runStory(chapters, handlers, { line = 0.6, before = "start", after = "free", progress = null } = {}) {
  let active = null;
  const step = () => {
    const y = innerHeight * line;
    let ch = null, u = 0;
    for (const c of chapters) {
      const r = c.getBoundingClientRect();
      if (r.top <= y && r.bottom > y) { ch = c.dataset.ch; u = (y - r.top) / r.height; }
    }
    if (!ch && chapters[0].getBoundingClientRect().top > y) ch = before;
    if (!ch && chapters[chapters.length - 1].getBoundingClientRect().bottom <= y) ch = after;
    if (ch && handlers[ch]) handlers[ch](u, ch !== active);
    if (ch !== active) for (const c of chapters) c.classList.toggle("active", c.dataset.ch === ch);
    active = ch;
    if (progress) {
      const box = progress.container.getBoundingClientRect();
      progress.bar.style.width = `${100 * Math.max(0, Math.min(1, -box.top / Math.max(1, box.height - innerHeight)))}%`;
    }
  };
  addEventListener("scroll", step, { passive: true });
  addEventListener("resize", step);
  step();
  return { refresh: () => { active = null; step(); }, active: () => active };
}
