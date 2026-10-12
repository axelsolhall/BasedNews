"""5.1 job runner: every step leaves a record that says what ran, what changed, what the gate measured and what failed.
Stand-in modules (tests/fake_steps.py) and a temporary database; no network, no key, no spend."""
import json

import pytest

from pipeline import store
from pipeline.job import runner
from pipeline.job.__main__ import main as cli
from pipeline.job.gates import Gate
from pipeline.job.steps import Step


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("BASEDNEWS_DB", str(tmp_path / "db.sqlite"))
    monkeypatch.setenv("BASEDNEWS_RECORDS", str(tmp_path / "records"))
    monkeypatch.delenv("BASEDNEWS_RUN_BUDGET_USD", raising=False)
    monkeypatch.delenv("BASEDNEWS_STATE", raising=False)
    return tmp_path


def add(monkeypatch, name, arg, gate=None, **kw):
    monkeypatch.setitem(runner.STEPS, name, Step(name, "tests.fake_steps", (arg,), gate=gate, **kw))


def lines(capsys):
    return [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.strip()]


def test_ok_step_is_recorded_with_counts_and_json_logs(env, monkeypatch, capsys):
    add(monkeypatch, "t_ok", "ok", gate=lambda conn, ctx: Gate(True, {"new": ctx["delta"].get("headlines")}))
    code, (rec,) = runner.run("t_ok", "run1")
    assert code == 0 and rec["status"] == "ok" and rec["gate"]["metrics"] == {"new": 1}
    assert rec["counts_after"]["headlines"] == rec["counts_before"]["headlines"] + 1 and rec["commit_sha"]
    row = store.recent_job_runs(store.connect())[0]
    assert row["run_id"] == "run1" and row["status"] == "ok" and row["gate"]["ok"] is True
    assert json.loads((env / "records" / "run1" / "t_ok.json").read_text())["status"] == "ok"
    out = lines(capsys)                                     # every stdout line is JSON with the run and step
    assert [e["event"] for e in out] == ["step_started", "output", "output", "step_finished"]
    assert out[1] == {**out[1], "line": "wrote one headline", "run_id": "run1", "step": "t_ok"}
    assert "sk-abcdef" not in out[2]["line"]                # keys are redacted in module output too


def test_failure_keeps_traceback_without_the_key_and_stops_the_job(env, monkeypatch, capsys):
    add(monkeypatch, "t_boom", "boom")
    add(monkeypatch, "t_after", "ok")
    monkeypatch.setitem(runner.JOBS, "t_job", ("t_boom", "t_after"))
    code, recs = runner.run("t_job", "run2")
    assert code == runner.EXIT_FAILED and [r["step"] for r in recs] == ["t_boom"]       # the next step never ran
    rec = recs[0]
    assert rec["status"] == "failed" and rec["error"].startswith("RuntimeError") and "fake_steps.py" in rec["traceback"]
    assert "sk-abcdef" not in rec["error"] + rec["traceback"] and "sk-***" in rec["error"]
    events = lines(capsys)
    assert events[-1]["event"] == "job_stopped" and events[-1]["skipped"] == ["t_after"]
    assert store.connect().execute("SELECT COUNT(*) FROM headlines").fetchone()[0] == 0


def test_nonzero_exit_and_failed_gate_have_their_own_codes(env, monkeypatch):
    add(monkeypatch, "t_exit", "exit3")
    assert runner.run("t_exit")[0] == runner.EXIT_FAILED
    add(monkeypatch, "t_gate", "ok", gate=lambda conn, ctx: Gate(False, {"x": 1}, "x too small"))
    code, (rec,) = runner.run("t_gate")
    assert code == runner.EXIT_GATE and rec["status"] == "gate_failed" and rec["error"] == "x too small"
    add(monkeypatch, "t_badgate", "args", gate=lambda conn, ctx: 1 / 0)    # a broken gate never passes silently
    assert runner.run("t_badgate")[1][0]["status"] == "gate_failed"


def test_crash_inside_a_write_transaction_does_not_lock_out_the_record(env, monkeypatch):
    add(monkeypatch, "t_tx", "crash_in_tx")
    monkeypatch.setattr(store, "connect", lambda path=None, _c=store.connect: _timeout(_c(path)))
    code, (rec,) = runner.run("t_tx", "run3")
    assert code == runner.EXIT_FAILED and "mid-transaction" in rec["error"]
    row = store.recent_job_runs(store.connect())[0]
    assert row["run_id"] == "run3" and row["status"] == "failed"          # saved, not "database is locked"
    assert store.connect().execute("SELECT COUNT(*) FROM headlines").fetchone()[0] == 0   # the half write is gone


def _timeout(conn):
    conn.execute("PRAGMA busy_timeout = 2000")          # fail in 2 s instead of 120 s if the lock is still held
    return conn


def test_paid_step_needs_an_explicit_budget(env, monkeypatch):
    add(monkeypatch, "t_paid", "args", paid=True)
    code, (rec,) = runner.run("t_paid")
    assert code == runner.EXIT_REFUSED and "budget" in rec["error"]
    monkeypatch.setenv("BASEDNEWS_RUN_BUDGET_USD", "0.5")
    code, (rec,) = runner.run("t_paid")
    assert code == 0 and rec["args"][-2:] == ["--budget-usd", "0.5"]
    assert runner.run("t_paid", extra=["--dry-run"])[1][0]["args"] == ["args", "--dry-run"]


def test_ml_step_refuses_clearly_without_the_model(env, monkeypatch):
    add(monkeypatch, "t_ml", "ok", needs_ml=True)
    monkeypatch.setattr(runner.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setenv("BASEDNEWS_EMBED_MODEL", "sentence-transformers/x")
    code, (rec,) = runner.run("t_ml")
    assert code == runner.EXIT_REFUSED and "WITH_ML=1" in rec["error"]


def test_cli_list_history_and_unknown(env, monkeypatch, capsys):
    assert cli(["list"]) == 0 and "job  refresh" in capsys.readouterr().out
    add(monkeypatch, "t_ok", "ok")
    assert cli(["t_ok", "--run-id", "r9"]) == 0
    capsys.readouterr()
    assert cli(["history"]) == 0 and "t_ok" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cli(["nope"])
