// One real judgment, readable by anyone: two outlets' headlines about the same event, what the judge said on each
// axis, and the judge's own reason. Data: judgments.json (make_judgment_examples.py — real outputs, git-ignored).
const AXES = { econ: { name: "Economic", toward: "further right", none: "No economic angle in this story — the judge abstained" },
               social: { name: "Social", toward: "more conservative", none: "No social angle in this story — the judge abstained" } };

export async function loadJudgments(url = new URL("judgments.json", import.meta.url)) {
  try {
    const r = await fetch(url);
    return r.ok ? (await r.json()).examples : [];
  } catch {
    return [];
  }
}

const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const date = (d) => (d ? new Date(d).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "");

export function judgmentCard(e) {
  const [a, b] = e.articles;
  const side = (x) => `<div class="j-art"><span class="j-who">${esc(x.outlet)}<small>${esc(x.country)}</small></span>
      <a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.title)}</a><small>${date(x.published)}</small></div>`;
  const verdict = (ax) => {
    const v = e.axes[ax], A = AXES[ax];
    if (!v) return "";
    const call = v.same ? "Same framing on this axis"
      : !v.decided ? A.none
      : `<b>${esc(e.articles[v.further].outlet)}</b> frames it ${A.toward}`;
    return `<div class="j-v"><span class="j-axis">${A.name}</span><span class="j-call">${call}</span>
      <p>“${esc(v.reason)}”</p></div>`;
  };
  return `<div class="j-pair">${side(a)}<div class="j-vs">vs</div>${side(b)}</div>
    <div class="j-verdicts">${verdict("econ")}${verdict("social")}</div>`;
}

// a browsable card: ‹ 3 / 10 › ; onShow(example) lets the stage highlight the two outlets
export function judgmentBrowser(el, examples, { onShow = () => {} } = {}) {
  if (!examples.length) {
    el.innerHTML = `<p class="j-empty">No examples on this machine (run make_judgment_examples.py).</p>`;
    return { show: () => {} };
  }
  let k = 0;
  el.innerHTML = `<div class="j-body"></div><div class="j-nav"><button data-d="-1" aria-label="Previous">‹</button>
    <span></span><button data-d="1" aria-label="Next">›</button>
    <small>Real output of the AI judge (gpt-5.4-mini) from a test run in October 2026, with the earlier prompt that also
    wrote reasons. Headlines link to the articles.</small></div>`;
  const show = (i) => {
    k = (i + examples.length) % examples.length;
    el.querySelector(".j-body").innerHTML = judgmentCard(examples[k]);
    el.querySelector(".j-nav span").textContent = `${k + 1} / ${examples.length}`;
    onShow(examples[k]);
  };
  el.querySelector(".j-nav").onclick = (ev) => { const b = ev.target.closest("button"); if (b) show(k + +b.dataset.d); };
  show(0);
  return { show, current: () => examples[k] };
}
