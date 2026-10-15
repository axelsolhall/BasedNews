"""5.2 SQLite-in-S3: download → run → upload only on success, never overwrite another writer, always keep the record.
A folder stands in for the bucket (same rules as S3: ETag, If-Match); the S3 adapter is tested with a fake client."""
import io
import json
import sqlite3

import pytest
from botocore.exceptions import ClientError

from pipeline import store
from pipeline.job import runner, storage
from pipeline.job.gates import Gate
from pipeline.job.steps import Step


@pytest.fixture
def state(tmp_path, monkeypatch):
    for k in ("BASEDNEWS_DB", "BASEDNEWS_RECORDS", "BASEDNEWS_LAKE", "BASEDNEWS_SITE_OUT", "BASEDNEWS_SITE_BUCKET",
              "BASEDNEWS_PUBLISH_POSITIONS", "BASEDNEWS_STATE"):
        monkeypatch.setenv(k, "")                        # registered, so the test restores what the runner sets
        monkeypatch.delenv(k)
    bucket_dir = tmp_path / "bucket"
    seed = tmp_path / "seed.sqlite"
    store.connect(seed).close()
    (bucket_dir / "db").mkdir(parents=True)
    (bucket_dir / storage.DB_KEY).write_bytes(seed.read_bytes())
    return bucket_dir


def step(monkeypatch, name, arg, **kw):
    monkeypatch.setitem(runner.STEPS, name, Step(name, "tests.fake_steps", (arg,), **kw))


def headlines(path):
    return sqlite3.connect(path).execute("SELECT COUNT(*) FROM headlines").fetchone()[0]


def test_success_uploads_the_database_and_the_record(state, monkeypatch):
    step(monkeypatch, "s_ok", "ok")
    before = storage.LocalBucket(state).etag(storage.DB_KEY)
    assert storage.run_with_state(f"file://{state}", "s_ok", "r1") == 0
    assert storage.LocalBucket(state).etag(storage.DB_KEY) != before and headlines(state / storage.DB_KEY) == 1
    assert json.loads((state / "runs" / "r1" / "s_ok.json").read_text())["status"] == "ok"


def test_failure_leaves_the_stored_database_alone_but_uploads_the_record(state, monkeypatch):
    step(monkeypatch, "s_gate", "ok", gate=lambda conn, ctx: Gate(False, {}, "bad numbers"))
    before = (state / storage.DB_KEY).read_bytes()
    assert storage.run_with_state(f"file://{state}", "s_gate", "r2") == runner.EXIT_GATE
    assert (state / storage.DB_KEY).read_bytes() == before                # the step's writes were not kept
    rec = json.loads((state / "runs" / "r2" / "s_gate.json").read_text())
    assert rec["status"] == "gate_failed" and rec["error"] == "bad numbers"


def test_a_second_writer_is_never_overwritten(state, monkeypatch):
    other = state.parent / "other.sqlite"
    con = store.connect(other)
    con.execute("INSERT INTO headlines VALUES ('x', 'from another writer', NULL, NULL, 'original')")
    con.commit()
    con.close()

    def sneaky_gate(conn, ctx):                         # someone else uploads while our step runs
        con = sqlite3.connect(other)
        con.execute("INSERT OR IGNORE INTO headlines VALUES ('y', 'other', NULL, NULL, 'original')")
        con.commit()
        con.close()
        storage.LocalBucket(state).put(other, storage.DB_KEY)
        return Gate(True)
    step(monkeypatch, "s_race", "ok", gate=sneaky_gate)
    assert storage.run_with_state(f"file://{state}", "s_race", "r3") == runner.EXIT_CONFLICT
    con = sqlite3.connect(state / storage.DB_KEY)
    assert {r[0] for r in con.execute("SELECT article_id FROM headlines")} == {"x", "y"}  # theirs; our 'a1' is not there


def test_missing_database_is_refused_unless_initialising(state, monkeypatch):
    (state / storage.DB_KEY).unlink()
    step(monkeypatch, "s_ok", "ok")
    assert storage.run_with_state(f"file://{state}", "s_ok", "r4") == runner.EXIT_REFUSED
    assert storage.run_with_state(f"file://{state}", "s_ok", "r5", init=True) == 0
    assert headlines(state / storage.DB_KEY) == 1


def test_site_files_are_published_only_when_allowed(state, monkeypatch, tmp_path):
    site = tmp_path / "site-bucket"
    out = tmp_path / "out"
    out.mkdir()
    (out / "plane-latest.json").write_text("{}")
    monkeypatch.setenv("BASEDNEWS_SITE_OUT", str(out))
    monkeypatch.setenv("BASEDNEWS_SITE_BUCKET", f"file://{site}")
    step(monkeypatch, "s_pub", "args", publishes=True)
    assert storage.run_with_state(f"file://{state}", "s_pub", "r6") == 0 and not site.exists()     # A54: off
    monkeypatch.setenv("BASEDNEWS_PUBLISH_POSITIONS", "1")
    assert storage.run_with_state(f"file://{state}", "s_pub", "r7") == 0
    assert (site / "plane-latest.json").read_text() == "{}"


def test_failed_lake_upload_blocks_the_database_upload(state, monkeypatch):
    def write_lake(conn, ctx):
        import os
        from pathlib import Path
        p = Path(os.environ["BASEDNEWS_LAKE"]) / "judge_calls" / "month=2026-09" / "part.parquet"
        p.parent.mkdir(parents=True)
        p.write_bytes(b"parquet")
        return Gate(True)
    step(monkeypatch, "s_lake", "ok", gate=write_lake)
    real_put = storage.LocalBucket.put

    def put(self, src, key, **kw):
        if key.startswith("lake/"):
            raise OSError("network down")
        return real_put(self, src, key, **kw)
    monkeypatch.setattr(storage.LocalBucket, "put", put)
    before = (state / storage.DB_KEY).read_bytes()
    assert storage.run_with_state(f"file://{state}", "s_lake", "r8") == runner.EXIT_FAILED
    assert (state / storage.DB_KEY).read_bytes() == before


class FakeS3:
    """Enough of the S3 client for the adapter: ETags and conditional puts."""

    def __init__(self):
        self.objects, self.puts = {}, []

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}, "ResponseMetadata": {"HTTPStatusCode": 404}}, "GetObject")
        body, etag = self.objects[Key]
        return {"Body": io.BytesIO(body), "ETag": etag}

    def put_object(self, Bucket, Key, Body, **kw):
        self.puts.append((Key, kw))
        cur = self.objects.get(Key, (None, None))[1]
        if ("IfMatch" in kw and kw["IfMatch"] != cur) or ("IfNoneMatch" in kw and cur is not None):
            raise ClientError({"Error": {"Code": "PreconditionFailed"}, "ResponseMetadata": {"HTTPStatusCode": 412}},
                              "PutObject")
        etag = f'"{len(self.puts)}"'
        self.objects[Key] = (Body if isinstance(Body, bytes) else Body.read(), etag)
        return {"ETag": etag}

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)


def test_s3_adapter_uses_conditional_writes(tmp_path):
    s3 = FakeS3()
    b = storage.open_bucket("s3://private-bucket/prod", s3)
    f = tmp_path / "f"
    f.write_bytes(b"v1")
    with pytest.raises(storage.Missing):
        b.get("db/basednews.sqlite", tmp_path / "d")
    e1 = b.put(f, "db/basednews.sqlite", if_none_match=True)
    assert s3.puts[-1] == ("prod/db/basednews.sqlite", {"IfNoneMatch": "*"})
    assert b.get("db/basednews.sqlite", tmp_path / "d") == e1 and (tmp_path / "d").read_bytes() == b"v1"
    b.put(f, "db/basednews.sqlite", if_match=e1)
    with pytest.raises(storage.Conflict):
        b.put(f, "db/basednews.sqlite", if_match=e1)                     # the stored version moved on
    b.put(f, "plane-latest.json", content_type="application/json", cache_control=storage.SITE_CACHE_CONTROL)
    assert s3.puts[-1][1] == {"ContentType": "application/json", "CacheControl": storage.SITE_CACHE_CONTROL}


def test_push_db_seeds_once_and_never_overwrites(tmp_path):
    src = tmp_path / "local.sqlite"
    con = store.connect(src)
    con.execute("INSERT INTO headlines VALUES ('z', 'local', NULL, NULL, 'original')")
    con.commit()                                            # left open: WAL content must still be in the copy
    url = f"file://{tmp_path / 'b'}"
    assert storage.push_db(url, src) == 0 and headlines(tmp_path / "b" / storage.DB_KEY) == 1
    assert storage.push_db(url, src) == runner.EXIT_CONFLICT


def test_history_reads_the_stored_database(state, monkeypatch, capsys):
    from pipeline.job.__main__ import main as cli
    step(monkeypatch, "s_ok", "ok")
    storage.run_with_state(f"file://{state}", "s_ok", "r10")
    monkeypatch.setenv("BASEDNEWS_STATE", f"file://{state}")
    monkeypatch.setenv("BASEDNEWS_DB", str(state.parent / "empty.sqlite"))
    capsys.readouterr()
    assert cli(["history"]) == 0 and "s_ok" in capsys.readouterr().out


def test_lock_makes_a_second_job_wait_and_is_released(state, monkeypatch):
    b = storage.LocalBucket(state)
    clock = [1000.0]
    held = storage.acquire_lock(b, "first", wait_s=0, clock=lambda: clock[0])
    assert held and (state / storage.LOCK_KEY).exists()
    waits = []
    assert storage.acquire_lock(b, "second", wait_s=60, poll_s=30, clock=lambda: clock[0],
                                sleep=lambda s: (waits.append(s), clock.__setitem__(0, clock[0] + s))) is None
    assert waits == [30, 30]                                          # waited, then gave up
    clock[0] += storage.LEASE_S + 1                                   # the first job stopped renewing (died)
    assert storage.acquire_lock(b, "third", wait_s=0, clock=lambda: clock[0])
    b.delete(storage.LOCK_KEY)
    assert storage.acquire_lock(b, "fourth", wait_s=0)                # a live holder, on the real clock
    step(monkeypatch, "s_ok", "ok")
    monkeypatch.setenv("BASEDNEWS_LOCK_WAIT_S", "0")
    assert storage.run_with_state(f"file://{state}", "s_ok", "r11") == storage.EXIT_BUSY   # "fourth" still holds it
    b.delete(storage.LOCK_KEY)
    assert storage.run_with_state(f"file://{state}", "s_ok", "r12") == 0
    assert not (state / storage.LOCK_KEY).exists()                    # released after the run


def test_s3_lock_uses_create_if_absent():
    s3 = FakeS3()
    b = storage.open_bucket("s3://private-bucket/prod", s3)
    assert storage.acquire_lock(b, "a", wait_s=0)
    assert s3.puts[-1] == ("prod/db/LOCK", {"IfNoneMatch": "*"})
    assert storage.acquire_lock(b, "b", wait_s=0) is None
    b.delete(storage.LOCK_KEY)
    assert storage.acquire_lock(b, "c", wait_s=0)
