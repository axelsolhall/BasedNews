"""Kill switch (A48, infra/main/killswitch): when the cost budget fires, every enabled schedule is switched off and
nothing else about it changes. Fake scheduler client; no AWS."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("killswitch", Path("infra/main/killswitch/handler.py"))
killswitch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(killswitch)


class FakeScheduler:
    def __init__(self, schedules):
        self.s, self.updates = schedules, []

    def get_paginator(self, name):
        assert name == "list_schedules"
        return type("P", (), {"paginate": lambda _, GroupName: [{"Schedules": [{"Name": n} for n in self.s]}]})()

    def get_schedule(self, Name, GroupName):
        return {"Name": Name, "GroupName": GroupName, "Arn": "x", "CreationDate": "y", **self.s[Name]}

    def update_schedule(self, **kw):
        self.updates.append(kw)


def test_killswitch_disables_enabled_schedules_and_keeps_their_settings(monkeypatch):
    target = {"Arn": "arn:sfn", "RoleArn": "arn:role", "Input": "{}"}
    fake = FakeScheduler({
        "refresh": {"State": "ENABLED", "ScheduleExpression": "cron(5 * * * ? *)", "ScheduleExpressionTimezone": "UTC",
                    "FlexibleTimeWindow": {"Mode": "OFF"}, "Target": target},
        "weekly": {"State": "DISABLED", "ScheduleExpression": "cron(30 2 ? * MON *)", "FlexibleTimeWindow": {"Mode": "OFF"},
                   "Target": target},
    })
    monkeypatch.setattr(killswitch.boto3, "client", lambda name: fake)
    monkeypatch.setenv("GROUP", "basednews")
    assert killswitch.handler({}, None) == {"disabled": ["refresh"]}
    (u,) = fake.updates
    assert u["State"] == "DISABLED" and u["Name"] == "refresh" and u["GroupName"] == "basednews"
    assert u["Target"] == target and u["ScheduleExpression"] == "cron(5 * * * ? *)" and "Arn" not in u
