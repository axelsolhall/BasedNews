"""Kill switch (A48): the cost budget passed its limit, so switch off every BasedNews schedule.

Triggered by SNS from the AWS budget. Re-enable deliberately: `terraform apply` with the *_enabled variables.
"""
import json
import os

import boto3

KEEP = ("Name", "GroupName", "ScheduleExpression", "ScheduleExpressionTimezone", "FlexibleTimeWindow", "Target",
        "Description", "StartDate", "EndDate", "KmsKeyArn")


def handler(event, context):
    s = boto3.client("scheduler")
    group = os.environ["GROUP"]
    names = [x["Name"] for page in s.get_paginator("list_schedules").paginate(GroupName=group) for x in page["Schedules"]]
    off = []
    for name in names:
        d = s.get_schedule(Name=name, GroupName=group)
        if d["State"] == "DISABLED":
            continue
        s.update_schedule(State="DISABLED", **{k: d[k] for k in KEEP if k in d})   # update replaces: send it all back
        off.append(name)
    print(json.dumps({"event": "killswitch", "disabled": off, "already_off": sorted(set(names) - set(off))}))
    return {"disabled": off}
