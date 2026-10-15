"""5.2 SQLite-in-S3 (ADR-010): download the database, run the step, upload it only if the step succeeded.

    BASEDNEWS_STATE=s3://<private-bucket>/<prefix>    production (inside the job's container)
    BASEDNEWS_STATE=file:///<folder>                  a local stand-in with the same rules (docker compose, tests)
    unset                                             plain local mode: the database at BASEDNEWS_DB or data/

Single writer (A55), self-healing (ADR-012): the lock object `db/LOCK` is a lease. Its holder renews it every
HEARTBEAT_S from a background thread; a lease not renewed for LEASE_S (the task crashed, ran out of memory or was
killed) is taken over by the next job, so no person ever has to delete a lock. A holder renews for at most MAX_HOLD_S
(a hung task cannot keep it), releases only a lease that is still its own, and never uploads after losing it. A stop
signal (SIGTERM from ECS) ends the step, keeps the stored database unchanged and releases the lease. As a second guard, the upload is conditional on the object still being the version that
was downloaded (S3 If-Match on its ETag): if anything else wrote it meanwhile the upload is refused (exit 4) and nothing
is overwritten. Before the upload the WAL is folded into the file and
`PRAGMA quick_check` must say ok. Step records are uploaded whatever the outcome. The export's site files are uploaded
only when the step succeeded and BASEDNEWS_PUBLISH_POSITIONS=1 (A54). Lake files written by retention are uploaded
before the database, and if that fails the database is not uploaded (A52: nothing is deleted without its archive).
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import shutil
import sqlite3
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from pipeline.job import runner
from pipeline.job.runner import EXIT_CONFLICT, EXIT_OK, EXIT_REFUSED, log

DB_KEY = "db/basednews.sqlite"
LOCK_KEY = "db/LOCK"
LOCK_WAIT_S = 45 * 60              # a judge run takes minutes; an hourly refresh that meets it waits
HEARTBEAT_S = 60                   # the holder renews its lease this often
LEASE_S = 5 * 60                   # not renewed for this long: the holder is gone (crash, OOM, kill) — take over
MAX_HOLD_S = 80 * 60               # no step needs longer (the state machine stops a task at 90 min): stop renewing
EXIT_BUSY = 5
EXIT_STOPPED = 143                 # SIGTERM: the step was stopped; nothing uploaded, lease released
SITE_CACHE_CONTROL = "public, max-age=300"         # the export changes daily; 5 min keeps a fix quick to show


class Conflict(RuntimeError):
    """The stored object is not the version this step started from."""


class Missing(KeyError):
    pass


class LocalBucket:
    """A folder with the bucket's rules (ETag, If-Match, If-None-Match): the fake bucket for compose and tests."""

    def __init__(self, root: Path | str):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        return self.root / key

    def etag(self, key: str) -> str | None:
        p = self._path(key)
        return f'"{hashlib.md5(p.read_bytes()).hexdigest()}"' if p.exists() else None

    def get(self, key: str, dest: Path) -> str:
        p = self._path(key)
        if not p.exists():
            raise Missing(key)
        shutil.copyfile(p, dest)
        return self.etag(key)

    def put(self, src: Path, key: str, if_match: str | None = None, if_none_match: bool = False,
            content_type: str | None = None, cache_control: str | None = None) -> str:
        cur = self.etag(key)
        if (if_match is not None and cur != if_match) or (if_none_match and cur is not None):
            raise Conflict(f"{key}: stored version {cur} is not {if_match or 'absent'}")
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".part")
        shutil.copyfile(src, tmp)
        os.replace(tmp, p)
        return self.etag(key)

    def put_bytes(self, data: bytes, key: str, if_match: str | None = None, if_none_match: bool = False) -> str:
        tmp = Path(tempfile.mkstemp()[1])
        tmp.write_bytes(data)
        try:
            return self.put(tmp, key, if_match=if_match, if_none_match=if_none_match)
        finally:
            tmp.unlink(missing_ok=True)

    def get_bytes(self, key: str) -> tuple[bytes, str]:
        p = self._path(key)
        if not p.exists():
            raise Missing(key)
        return p.read_bytes(), self.etag(key)

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


class S3Bucket:
    def __init__(self, bucket: str, prefix: str = "", client=None):
        self.bucket, self.prefix = bucket, prefix.strip("/")
        if client is None:
            import boto3
            client = boto3.client("s3")
        self.client = client

    def _key(self, key: str) -> str:
        return f"{self.prefix}/{key}" if self.prefix else key

    @staticmethod
    def _code(e) -> tuple[str, int]:
        err = getattr(e, "response", {}) or {}
        return err.get("Error", {}).get("Code", ""), err.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)

    def get(self, key: str, dest: Path) -> str:
        from botocore.exceptions import ClientError
        try:
            resp = self.client.get_object(Bucket=self.bucket, Key=self._key(key))
        except ClientError as e:
            if self._code(e)[0] in ("NoSuchKey", "404") or self._code(e)[1] == 404:
                raise Missing(key) from e
            raise
        tmp = Path(str(dest) + ".part")
        with open(tmp, "wb") as f:
            for chunk in iter(lambda: resp["Body"].read(1 << 20), b""):
                f.write(chunk)
        os.replace(tmp, dest)
        return resp["ETag"]

    def put(self, src: Path, key: str, if_match: str | None = None, if_none_match: bool = False,
            content_type: str | None = None, cache_control: str | None = None) -> str:
        from botocore.exceptions import ClientError
        kw = {}
        if if_match:
            kw["IfMatch"] = if_match
        if if_none_match:
            kw["IfNoneMatch"] = "*"
        if content_type:
            kw["ContentType"] = content_type
        if cache_control:
            kw["CacheControl"] = cache_control
        with open(src, "rb") as f:
            try:
                resp = self.client.put_object(Bucket=self.bucket, Key=self._key(key), Body=f, **kw)
            except ClientError as e:
                code, status = self._code(e)
                if code in ("PreconditionFailed", "ConditionalRequestConflict") or status in (409, 412):
                    raise Conflict(f"{key}: changed since it was downloaded ({code or status})") from e
                raise
        return resp["ETag"]


def _s3_put_bytes(self, data: bytes, key: str, if_match: str | None = None, if_none_match: bool = False) -> str:
    from botocore.exceptions import ClientError
    kw = {"IfMatch": if_match} if if_match else {}
    if if_none_match:
        kw["IfNoneMatch"] = "*"
    try:
        return self.client.put_object(Bucket=self.bucket, Key=self._key(key), Body=data, **kw)["ETag"]
    except ClientError as e:
        code, status = self._code(e)
        if code in ("PreconditionFailed", "ConditionalRequestConflict") or status in (409, 412):
            raise Conflict(f"{key}: {code or status}") from e
        raise


def _s3_get_bytes(self, key: str) -> tuple[bytes, str]:
    from botocore.exceptions import ClientError
    try:
        r = self.client.get_object(Bucket=self.bucket, Key=self._key(key))
    except ClientError as e:
        if self._code(e)[0] in ("NoSuchKey", "404") or self._code(e)[1] == 404:
            raise Missing(key) from e
        raise
    return r["Body"].read(), r["ETag"]


def _s3_delete(self, key: str) -> None:
    self.client.delete_object(Bucket=self.bucket, Key=self._key(key))


S3Bucket.put_bytes, S3Bucket.get_bytes, S3Bucket.delete = _s3_put_bytes, _s3_get_bytes, _s3_delete


class Stopped(KeyboardInterrupt):
    """Raised in the main thread on SIGTERM (ECS StopTask, a state-machine timeout): end the step, upload nothing."""


class Lease:
    """`db/LOCK` as a lease: {run_id, at, heartbeat, host}. Acquire it, keep it alive from a thread, release it only
    if it is still ours. `lost` turns true when someone else's write replaced ours (we were taken over)."""

    def __init__(self, bucket, run_id: str, clock=time.time, heartbeat_s: float = HEARTBEAT_S,
                 lease_s: float = LEASE_S, max_hold_s: float = MAX_HOLD_S):
        self.bucket, self.run_id, self.clock = bucket, run_id, clock
        self.heartbeat_s, self.lease_s, self.max_hold_s = heartbeat_s, lease_s, max_hold_s
        self.etag, self.acquired_at, self.lost = None, None, False
        self._stop = threading.Event()
        self._thread = None
        self._mu = threading.Lock()

    def _body(self) -> bytes:
        now = self.clock()
        return json.dumps({"run_id": self.run_id, "at": self.acquired_at or now, "heartbeat": now,
                           "host": socket.gethostname()}).encode()

    def _expired(self, holder: dict) -> bool:
        return self.clock() - holder.get("heartbeat", holder.get("at", 0)) > self.lease_s   # old locks have only "at"

    def acquire(self, wait_s: float = LOCK_WAIT_S, poll_s: float = 30.0, sleep=time.sleep, beat: bool = True) -> bool:
        deadline = self.clock() + wait_s
        while True:
            try:
                self.acquired_at = self.clock()
                self.etag = self.bucket.put_bytes(self._body(), LOCK_KEY, if_none_match=True)
                break
            except Conflict:
                try:
                    raw, etag = self.bucket.get_bytes(LOCK_KEY)
                    holder = json.loads(raw)
                except Missing:
                    continue                                   # released between the two calls: try again
                if self._expired(holder):
                    try:
                        self.acquired_at = self.clock()
                        self.etag = self.bucket.put_bytes(self._body(), LOCK_KEY, if_match=etag)
                        log("lock_taken_over", run_id=self.run_id, stale_holder=holder.get("run_id"),
                            silent_s=round(self.clock() - holder.get("heartbeat", holder.get("at", 0))))
                        break
                    except Conflict:
                        continue                               # someone else took it first
                if self.clock() >= deadline:
                    log("lock_busy", run_id=self.run_id, holder=holder.get("run_id"), waited_s=wait_s)
                    return False
                log("lock_wait", run_id=self.run_id, holder=holder.get("run_id"))
                sleep(poll_s)
        if beat:
            self._thread = threading.Thread(target=self._beat, name="lease-heartbeat", daemon=True)
            self._thread.start()
        return True

    def renew(self) -> bool:
        """One heartbeat: rewrite the lease only if it is still the version we wrote. False = lost or too old."""
        with self._mu:
            if self.lost:
                return False
            if self.clock() - self.acquired_at > self.max_hold_s:
                log("lock_hold_limit", run_id=self.run_id, held_s=round(self.clock() - self.acquired_at))
                return False                                   # stop renewing: others may take over after LEASE_S
            try:
                self.etag = self.bucket.put_bytes(self._body(), LOCK_KEY, if_match=self.etag)
                return True
            except (Conflict, Missing):
                self.lost = True
                log("lock_lost", run_id=self.run_id)
                return False
            except Exception as e:                             # a network blip: try again at the next beat
                log("lock_renew_error", run_id=self.run_id, error=str(e))
                return True

    def _beat(self):
        while not self._stop.wait(self.heartbeat_s):
            if not self.renew():
                return

    def still_ours(self) -> bool:
        """Fencing check before any upload: the stored lease is the one we last wrote."""
        if self.lost:
            return False
        try:
            raw, etag = self.bucket.get_bytes(LOCK_KEY)
        except Missing:
            return False
        return etag == self.etag and json.loads(raw).get("run_id") == self.run_id

    def release(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)
        with self._mu:
            if self.still_ours():
                self.bucket.delete(LOCK_KEY)
            else:
                log("lock_not_released", run_id=self.run_id, reason="the lease is no longer ours: left to its holder")


def acquire_lock(bucket, run_id: str, wait_s: float = LOCK_WAIT_S, poll_s: float = 30.0,
                 clock=time.time, sleep=time.sleep) -> str | None:
    """Kept for callers and tests of the first version: acquire a lease without a heartbeat. ETag or None."""
    lease = Lease(bucket, run_id, clock=clock)
    return lease.etag if lease.acquire(wait_s, poll_s, sleep, beat=False) else None


def open_bucket(url: str, client=None):
    u = urlparse(url)
    if u.scheme == "s3":
        return S3Bucket(u.netloc, u.path, client)
    if u.scheme in ("file", ""):
        return LocalBucket(u.path if u.scheme else url)
    raise ValueError(f"unsupported state location {url!r}: use s3://bucket/prefix or file:///folder")


def finalize(db: Path) -> str:
    """Fold the WAL into the file (only the main file is uploaded) and check it. Returns quick_check's answer."""
    conn = sqlite3.connect(db)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return conn.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        conn.close()


def upload_lake(bucket, lake: Path, run_id: str) -> str:
    """Upload the Parquet files retention wrote (before the database, which may no longer hold their rows)."""
    files = sorted(lake.rglob("*.parquet")) if lake.exists() else []
    try:
        for f in files:
            bucket.put(f, f"lake/{f.relative_to(lake).as_posix()}")
    except Exception as e:
        return f"{type(e).__name__}: {e}"
    if files:
        log("lake_uploaded", run_id=run_id, files=len(files))
    return ""


def publish_site(out_dir: Path, site_url: str, client=None) -> list[str]:
    """Upload the export's JSON files to the site bucket (A54 decides whether this is called)."""
    bucket = open_bucket(site_url, client)
    done = []
    for f in sorted(out_dir.glob("*.json")):
        bucket.put(f, f.name, content_type="application/json", cache_control=SITE_CACHE_CONTROL)
        done.append(f.name)
    return done


def push_db(state_url: str, src: Path, client=None) -> int:
    """Seed the stored database from a local one (first deploy, or the compose stand-in). Never overwrites."""
    bucket = open_bucket(state_url, client)
    work = Path(tempfile.mkdtemp(prefix="basednews-"))
    snap = work / "basednews.sqlite"
    try:
        s_, d_ = sqlite3.connect(src), sqlite3.connect(snap)
        s_.backup(d_)                                       # a consistent copy, WAL included
        s_.close(), d_.close()
        check = finalize(snap)
        if check != "ok":
            log("push_refused", reason=f"quick_check: {check}")
            return runner.EXIT_FAILED
        try:
            etag = bucket.put(snap, DB_KEY, if_none_match=True)
        except Conflict:
            log("push_refused", reason=f"{DB_KEY} already exists; delete it deliberately (S3 keeps old versions) first")
            return EXIT_CONFLICT
        log("state_seeded", key=DB_KEY, etag=etag, bytes=snap.stat().st_size, source=str(src))
        return EXIT_OK
    finally:
        shutil.rmtree(work, ignore_errors=True)


def run_with_state(state_url: str, target: str, run_id: str | None = None, extra=(), gate: bool = True,
                   init: bool = False, client=None, site_client=None) -> int:
    bucket = open_bucket(state_url, client)
    work = Path(tempfile.mkdtemp(prefix="basednews-"))
    db = work / "basednews.sqlite"
    os.environ["BASEDNEWS_DB"] = str(db)
    os.environ["BASEDNEWS_RECORDS"] = str(work / "records")
    os.environ["BASEDNEWS_LAKE"] = str(work / "lake")
    os.environ.setdefault("BASEDNEWS_SITE_OUT", str(work / "site"))
    run_id = run_id or runner.new_run_id()
    lease = Lease(bucket, run_id)
    if not lease.acquire(wait_s=float(os.environ.get("BASEDNEWS_LOCK_WAIT_S", LOCK_WAIT_S))):
        shutil.rmtree(work, ignore_errors=True)
        return EXIT_BUSY
    t0 = time.monotonic()
    try:
        etag = bucket.get(DB_KEY, db)
    except Missing:
        if not init:
            log("state_missing", run_id=run_id, key=DB_KEY, hint="first deploy: upload the database, or pass --init-state")
            lease.release()
            shutil.rmtree(work, ignore_errors=True)
            return EXIT_REFUSED
        etag = None
    log("state_downloaded", run_id=run_id, etag=etag, bytes=db.stat().st_size if db.exists() else 0,
        seconds=round(time.monotonic() - t0, 1))

    code, recs = runner.EXIT_FAILED, []
    try:
        try:
            code, recs = runner.run(target, run_id, extra, gate)
        except Stopped:
            code = EXIT_STOPPED
            recs = runner.records_of(run_id)                  # what the stopped step managed to write
            log("step_stopped", run_id=run_id, hint="SIGTERM: nothing uploaded, the lease is released")
        if code == EXIT_OK and not lease.still_ours():         # fencing: taken over meanwhile (we hung or lost it)
            log("state_not_saved", run_id=run_id, reason="the lease is no longer ours: another job may be writing")
            code = EXIT_CONFLICT
        if code == EXIT_OK:
            check = finalize(db)
            lake_error = upload_lake(bucket, Path(os.environ["BASEDNEWS_LAKE"]), run_id)
            if lake_error:                               # A52: never upload deletions whose archive is not stored
                log("state_not_saved", run_id=run_id, reason=f"lake upload failed: {lake_error}")
                code = runner.EXIT_FAILED
            elif check != "ok":
                log("state_not_saved", run_id=run_id, reason=f"quick_check: {check}")
                code = runner.EXIT_FAILED
            else:
                try:
                    new = bucket.put(db, DB_KEY, if_match=etag, if_none_match=etag is None)
                    log("state_saved", run_id=run_id, etag=new, bytes=db.stat().st_size)
                except Conflict as e:
                    log("state_conflict", run_id=run_id, error=str(e), hint="another writer ran; nothing overwritten")
                    code = EXIT_CONFLICT
            site = os.environ.get("BASEDNEWS_SITE_BUCKET")
            if code == EXIT_OK and site and any(runner.STEPS[r["step"]].publishes for r in recs):
                if os.environ.get("BASEDNEWS_PUBLISH_POSITIONS") == "1":
                    files = publish_site(Path(os.environ["BASEDNEWS_SITE_OUT"]), site, site_client)
                    log("site_published", run_id=run_id, files=files)
                else:
                    log("site_not_published", run_id=run_id, reason="BASEDNEWS_PUBLISH_POSITIONS is not 1 (A54)")
        else:
            log("state_not_saved", run_id=run_id, reason=f"step status exit {code}: the stored database is unchanged")
    finally:
        for rec in recs:                                    # the record of a failed step is the most useful one
            path = runner.records_dir() / rec["run_id"] / f"{rec['step']}.json"
            try:
                bucket.put(path, f"runs/{rec['run_id']}/{rec['step']}.json", content_type="application/json")
            except Exception as e:                          # CloudWatch still has the step's log lines
                log("record_not_uploaded", run_id=run_id, step=rec["step"], error=str(e))
        lease.release()
        shutil.rmtree(work, ignore_errors=True)
    return code
