"""The history's positions, day by day (A74): yesterday's posterior is today's prior, and only today's verdicts move it.

Per axis the state is one Gaussian over [θ_outlets (n), β, ε_articles (k)] — every outlet's position, the first-position
bias, and the articles still inside the 7-day window (the only ones a later day can compare again). A day's verdicts
touch a few of these; their Laplace update (the hierarchical model's likelihood, pipeline.rate.hierarchical) is made on
those alone and carried to the rest through the prior's correlations — exact for a Gaussian prior, and its cost depends
on the day's verdicts, not on how many came before. Articles that leave the window are marginalised out (their rows
dropped), so the state stays the same size year after year. Every outlet has a position on every day
(history_positions), and so does every country (history_country_positions, with its sd from the day's joint
uncertainty); the planner picks the next day's pairs from the latest one.

More verdicts only ever sharpen a position: nothing is added back between days (A74).
"""
from __future__ import annotations

import datetime as dt
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from pipeline.judge import prompt as P
from pipeline.rate.hierarchical import HierPosterior
from pipeline.rate.model import BTConfig, sigmoid

POSITIONS = """CREATE TABLE IF NOT EXISTS history_positions (
    day TEXT NOT NULL, axis TEXT NOT NULL, outlet TEXT NOT NULL, mean REAL NOT NULL, sd REAL NOT NULL,
    PRIMARY KEY (day, axis, outlet))"""
COUNTRIES = """CREATE TABLE IF NOT EXISTS history_country_positions (
    day TEXT NOT NULL, axis TEXT NOT NULL, country TEXT NOT NULL, mean REAL NOT NULL, sd REAL NOT NULL,
    PRIMARY KEY (day, axis, country))"""


@dataclass
class AxisState:
    axis: str
    outlets: list[str]
    mean: np.ndarray                    # [θ_outlets (n), β, ε_articles (k)]
    cov: np.ndarray                     # dense, same order
    sigma_a: float                      # a new article's prior sd around its outlet
    tau: float                          # the outlets' prior sd before any verdict
    articles: list[str] = field(default_factory=list)
    born: list[str] = field(default_factory=list)      # each article's publication day
    n_obs: int = 0

    @property
    def n(self) -> int:
        return len(self.outlets)

    def posterior(self) -> HierPosterior:
        """The state as the planner reads a fit (plan_joint_hier)."""
        return HierPosterior(self.axis, self.outlets, list(self.articles), self.mean, self.cov, self.sigma_a,
                             self.n_obs, {}, self.tau)


def start(outlets: list[str], axis: str, tau: float, sigma_a: float, cfg: BTConfig | None = None) -> AxisState:
    """Before any verdict: every outlet at 0 with sd τ, the bias at 0 with its prior sd."""
    cfg = cfg or BTConfig()
    n = len(outlets)
    return AxisState(axis, list(outlets), np.zeros(n + 1), np.diag([tau ** 2] * n + [cfg.beta_prior_sd ** 2]),
                     sigma_a, tau)


def update(s: AxisState, rows: list[tuple], cfg: BTConfig | None = None) -> None:
    """Today's verdicts on this axis: rows of (first_outlet, second_outlet, first_article, second_article,
    first_published_day, second_published_day, discrimination, first_wins)."""
    if not rows:
        return
    cfg = cfg or BTConfig()
    n = s.n
    oidx = {o: k for k, o in enumerate(s.outlets)}
    rows = [r for r in rows if r[0] in oidx and r[1] in oidx]
    if not rows:
        return
    aidx = {a: n + 1 + k for k, a in enumerate(s.articles)}
    new = {}
    for r in rows:
        for a, d in ((r[2], r[4]), (r[3], r[5])):
            if a not in aidx and a not in new:
                new[a] = d
    if new:                                         # a new article enters at its prior, uncorrelated with the rest
        k0, k = len(s.mean), len(new)
        s.mean = np.concatenate([s.mean, np.zeros(k)])
        cov = np.zeros((k0 + k, k0 + k))
        cov[:k0, :k0] = s.cov
        cov[k0:, k0:] = np.eye(k) * s.sigma_a ** 2
        s.cov = cov
        for j, (a, d) in enumerate(new.items()):
            aidx[a] = k0 + j
            s.articles.append(a)
            s.born.append(d)
    J = sorted({oidx[r[0]] for r in rows} | {oidx[r[1]] for r in rows} | {n}
               | {aidx[r[2]] for r in rows} | {aidx[r[3]] for r in rows})
    loc = {g: j for j, g in enumerate(J)}
    X = np.zeros((len(rows), len(J)))
    for i, (oa, ob, fa, fb, _, _, disc, _) in enumerate(rows):
        for g, v in ((oidx[oa], disc), (oidx[ob], -disc), (n, disc), (aidx[fa], disc), (aidx[fb], -disc)):
            X[i, loc[g]] += v
    y = np.array([float(r[7]) for r in rows])
    m_J, P_JJ = s.mean[J], s.cov[np.ix_(J, J)]
    L = np.linalg.inv(P_JJ)                         # the prior's precision on the touched parameters
    phi = m_J.copy()
    for _ in range(cfg.max_iter):                   # Newton on log prior + log likelihood (log-concave: one mode)
        p = sigmoid(X @ phi)
        H = (X.T * (p * (1 - p))) @ X + L
        step = np.linalg.solve(H, X.T @ (y - p) - L @ (phi - m_J))
        phi += step
        if np.max(np.abs(step)) < cfg.tol:
            break
    p = sigmoid(X @ phi)
    C_J = np.linalg.inv((X.T * (p * (1 - p))) @ X + L)
    K = s.cov[:, J] @ L                             # how every parameter follows the touched ones in the prior
    s.mean = s.mean + K @ (phi - m_J)
    s.cov = s.cov - K @ (P_JJ - C_J) @ K.T
    s.cov = (s.cov + s.cov.T) / 2
    s.n_obs += len(rows)


def drop_before(s: AxisState, day: str) -> None:
    """Marginalise out the articles published before `day` (they can no longer be compared)."""
    keep = [k for k, d in enumerate(s.born) if d >= day]
    if len(keep) == len(s.articles):
        return
    idx = list(range(s.n + 1)) + [s.n + 1 + k for k in keep]
    s.mean, s.cov = s.mean[idx], s.cov[np.ix_(idx, idx)]
    s.articles = [s.articles[k] for k in keep]
    s.born = [s.born[k] for k in keep]


def new_verdicts(conn, purpose: str, after_rowid: int, a_full: float, a_summary: float) -> tuple[dict, int]:
    """The verdicts recorded since `after_rowid` ({axis: rows for update}), and the last rowid read."""
    rows = conn.execute("""SELECT c.rowid AS rid, c.first_outlet, c.second_outlet, c.first_article, c.second_article,
                                  substr(a.published, 1, 10) AS pa, substr(b.published, 1, 10) AS pb, c.text_kind,
                                  j.axis, j.first_wins
                           FROM judge_calls c JOIN judgments j ON j.call_id = c.call_id
                           JOIN articles a ON a.id = c.first_article JOIN articles b ON b.id = c.second_article
                           WHERE c.purpose = ? AND c.prompt_version = ? AND c.rowid > ? AND j.first_wins IS NOT NULL
                           ORDER BY c.rowid""", (purpose, P.PROMPT_VERSION, after_rowid)).fetchall()
    last = conn.execute("SELECT max(rowid) FROM judge_calls").fetchone()[0] or after_rowid
    out: dict[str, list] = {ax: [] for ax in P.AXES}
    for r in rows:
        out[r["axis"]].append((r["first_outlet"], r["second_outlet"], r["first_article"], r["second_article"], r["pa"],
                               r["pb"], a_full if r["text_kind"] == "full" else a_summary, bool(r["first_wins"])))
    return out, max(last, after_rowid)


def record_day(conn, day: str, states: dict[str, AxisState], groups: dict[str, dict[str, float]] | None = None) -> None:
    """Every outlet's position on `day`, per axis, and every country's (A08: the weighted mean of its reference
    outlets, `groups`), its sd from the day's joint uncertainty — which only the state holds, so it is written now."""
    conn.execute(POSITIONS)
    conn.execute(COUNTRIES)
    conn.executemany("INSERT OR REPLACE INTO history_positions VALUES (?,?,?,?,?)",
                     [(day, ax, o, float(s.mean[k]), float(np.sqrt(max(s.cov[k, k], 0.0))))
                      for ax, s in states.items() for k, o in enumerate(s.outlets)])
    rows = []
    for ax, s in states.items():
        idx = {o: k for k, o in enumerate(s.outlets)}
        for c, g in (groups or {}).items():
            ks = [idx[o] for o in g if o in idx]
            if not ks:
                continue
            w = np.array([g[s.outlets[k]] for k in ks], dtype=float)
            w /= w.sum()
            var = float(w @ s.cov[np.ix_(ks, ks)] @ w)
            rows.append((day, ax, c, float(w @ s.mean[ks]), float(np.sqrt(max(var, 0.0)))))
    conn.executemany("INSERT OR REPLACE INTO history_country_positions VALUES (?,?,?,?,?)", rows)


def save(path: Path, day: str, last_rowid: int, states: dict[str, AxisState]) -> None:
    """The state after `day` (written to a temporary file, then moved: a crash leaves the previous day's)."""
    meta = {"day": day, "last_rowid": last_rowid,
            "axes": {ax: {"outlets": s.outlets, "articles": s.articles, "born": s.born, "sigma_a": s.sigma_a,
                          "tau": s.tau, "n_obs": s.n_obs} for ax, s in states.items()}}
    arrays = {f"{ax}_{k}": getattr(s, k) for ax, s in states.items() for k in ("mean", "cov")}
    tmp = path.with_suffix(".tmp.npz")
    np.savez(tmp, meta=np.array(json.dumps(meta)), **arrays)
    os.replace(tmp, path)


def load(path: Path) -> tuple[str, int, dict[str, AxisState]] | None:
    if not path.exists():
        return None
    z = np.load(path)
    meta = json.loads(str(z["meta"]))
    states = {ax: AxisState(ax, m["outlets"], z[f"{ax}_mean"], z[f"{ax}_cov"], m["sigma_a"], m["tau"], m["articles"],
                            m["born"], m["n_obs"]) for ax, m in meta["axes"].items()}
    return meta["day"], meta["last_rowid"], states


def window_start(day: dt.date) -> str:
    """The first publication day the next day's window still holds (its window is [day − 5, day + 1])."""
    return (day - dt.timedelta(days=5)).isoformat()
