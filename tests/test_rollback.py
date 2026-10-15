"""Auto-rollback (ADR-012, infra/main/rollback): `prod` goes back to `prev` only after the refresh failed twice in a row
with the new image and never succeeded with it — and never loops. Fake AWS clients; no AWS."""
import datetime as dt
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("rollback", Path("infra/main/rollback/handler.py"))
rollback = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rollback)

T0 = dt.datetime(2026, 10, 15, 8, 0, tzinfo=dt.timezone.utc)


class NotFound(Exception):
    pass


class FakeECR:
    exceptions = type("E", (), {"ImageNotFoundException": NotFound})

    def __init__(self, tags):
        self.tags, self.puts = tags, []                     # tag -> (digest, pushed_at, [tags])

    def describe_images(self, repositoryName, imageIds):
        t = imageIds[0]["imageTag"]
        if t not in self.tags:
            raise NotFound(t)
        d, at, tags = self.tags[t]
        return {"imageDetails": [{"imageDigest": d, "imagePushedAt": at, "imageTags": tags}]}

    def batch_get_image(self, repositoryName, imageIds):
        return {"images": [{"imageManifest": f"manifest-of-{self.tags[imageIds[0]['imageTag']][0]}"}]}

    def put_image(self, **kw):
        self.puts.append(kw)


class FakeSFN:
    def __init__(self, runs):
        self.runs = runs

    def list_executions(self, stateMachineArn, maxResults):
        return {"executions": [{"status": s, "startDate": T0 + dt.timedelta(hours=h)} for s, h in self.runs]}


@pytest.fixture
def env(monkeypatch):
    for k, v in {"REPOSITORY": "basednews-job", "REFRESH_ARN": "arn:sm", "TOPIC": "arn:topic"}.items():
        monkeypatch.setenv(k, v)
    sent = []

    def install(ecr, sfn):
        clients = {"ecr": ecr, "stepfunctions": sfn, "sns": type("S", (), {"publish": lambda _, **kw: sent.append(kw)})()}
        monkeypatch.setattr(rollback.boto3, "client", lambda name: clients[name])
    return install, sent


def images():
    return FakeECR({"prod": ("sha256:new", T0, ["prod", "950d304785ca"]),
                    "prev": ("sha256:old", T0 - dt.timedelta(days=1), ["prev", "8fad1c1ce40b"])})


def test_two_failures_with_a_new_image_roll_back_and_email(env):
    install, sent = env
    ecr = images()
    install(ecr, FakeSFN([("FAILED", 2), ("FAILED", 1), ("SUCCEEDED", -1)]))   # newest first; the success was before
    out = rollback.handler({}, None)
    assert out["event"] == "rolled_back" and out["bad"] == ["950d304785ca"] and out["good"] == ["8fad1c1ce40b"]
    assert ecr.puts == [{"repositoryName": "basednews-job", "imageTag": "prod", "imageManifest": "manifest-of-sha256:old"}]
    assert "rolled back" in sent[0]["Message"] and "8fad1c1ce40b" in sent[0]["Message"]


def test_one_failure_is_not_enough(env):
    install, _ = env
    ecr = images()
    install(ecr, FakeSFN([("FAILED", 1)]))
    assert rollback.handler({}, None)["event"] == "no_rollback" and not ecr.puts


def test_an_image_that_has_worked_is_not_blamed(env):
    install, _ = env
    ecr = images()
    install(ecr, FakeSFN([("FAILED", 3), ("FAILED", 2), ("SUCCEEDED", 1)]))      # e.g. a source outage, not the code
    assert rollback.handler({}, None)["event"] == "no_rollback" and not ecr.puts


def test_no_loop_after_a_rollback(env):
    install, _ = env
    ecr = FakeECR({"prod": ("sha256:old", T0, ["prod", "prev"]), "prev": ("sha256:old", T0, ["prod", "prev"])})
    install(ecr, FakeSFN([("FAILED", 2), ("FAILED", 1)]))
    assert rollback.handler({}, None)["reason"] == "prod is already prev" and not ecr.puts


def test_manual_stops_do_not_count(env):
    install, _ = env
    ecr = images()
    install(ecr, FakeSFN([("FAILED", 3), ("ABORTED", 2), ("FAILED", 1)]))
    assert rollback.handler({}, None)["event"] == "no_rollback" and not ecr.puts
