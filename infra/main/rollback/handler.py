"""Auto-rollback (ADR-012): if the hourly refresh has failed twice in a row since the current `prod` image was pushed,
and it has not succeeded once with it, move `prod` back to `prev` (the image that ran before) and email why.

Triggered by EventBridge on every failed or timed-out BasedNews execution. CI tags the outgoing image `prev` before it
moves `prod`, so `prev` is always the last image that was live. It rolls back only to an image that has worked: a
refresh must have succeeded while `prev` was `prod` (2026-10-15: both images failed — one on a token limit, the other
out of memory — and rolling back would have swapped one failure for another). Otherwise it emails once and does nothing. Nothing is deleted: the bad image keeps its commit
tag. After a rollback `prod` == `prev`, so a second failure streak cannot roll back again (no loop); the next push from
CI is the way forward. Daily jobs are not used as evidence: two failures would take two days.
"""
import json
import os

import boto3

FAILED = {"FAILED", "TIMED_OUT"}


def commit_tags(detail: dict) -> list[str]:
    return [t for t in detail.get("imageTags", []) if t not in ("prod", "prev")]


def handler(event, context):
    repo, sm, topic = os.environ["REPOSITORY"], os.environ["REFRESH_ARN"], os.environ["TOPIC"]
    need = int(os.environ.get("MIN_FAILURES", "2"))
    ecr, sfn = boto3.client("ecr"), boto3.client("stepfunctions")

    def image(tag):
        try:
            return ecr.describe_images(repositoryName=repo, imageIds=[{"imageTag": tag}])["imageDetails"][0]
        except ecr.exceptions.ImageNotFoundException:
            return None

    prod, prev = image("prod"), image("prev")
    if not prod or not prev or prod["imageDigest"] == prev["imageDigest"]:
        return _out("no_rollback", reason="no previous image to go back to" if not prev or not prod else "prod is already prev")
    runs = [e for e in sfn.list_executions(stateMachineArn=sm, maxResults=20)["executions"]
            if e["startDate"] >= prod["imagePushedAt"] and e["status"] != "RUNNING"]       # newest first
    if any(e["status"] == "SUCCEEDED" for e in runs):
        return _out("no_rollback", reason="the current image has succeeded at least once: not the image's fault")
    streak = 0
    for e in runs:
        if e["status"] not in FAILED:
            break                                           # an aborted run (a manual stop) ends the count
        streak += 1
    if streak < need:
        return _out("no_rollback", reason=f"{streak} failed refresh run(s) since the deploy; rolling back at {need}")
    was_prod = [e for e in sfn.list_executions(stateMachineArn=sm, maxResults=100)["executions"]
                if prev["imagePushedAt"] <= e["startDate"] < prod["imagePushedAt"]]
    if not any(e["status"] == "SUCCEEDED" for e in was_prod):
        boto3.client("sns").publish(
            TopicArn=topic, Subject="BasedNews: failing, and no image to roll back to",
            Message=(f"The hourly refresh failed {streak} times in a row with the newly deployed image "
                     f"{commit_tags(prod) or prod['imageDigest'][:19]}, but the previous image "
                     f"{commit_tags(prev) or prev['imageDigest'][:19]} never succeeded either, so nothing was rolled "
                     f"back. The cause is probably not the code change (data, memory, an outside service): see the "
                     f"failed step's record in runs/ and CloudWatch /basednews/jobs."))
        return _out("no_rollback", reason="the previous image never succeeded either: rolling back would not help")
    manifest = ecr.batch_get_image(repositoryName=repo, imageIds=[{"imageTag": "prev"}])["images"][0]["imageManifest"]
    ecr.put_image(repositoryName=repo, imageTag="prod", imageManifest=manifest)
    bad, good = commit_tags(prod), commit_tags(prev)
    msg = (f"BasedNews rolled back automatically.\n\nThe hourly refresh failed {streak} times in a row with the newly "
           f"deployed image {bad or prod['imageDigest'][:19]} and never succeeded with it, so `prod` now points to "
           f"the previous image {good or prev['imageDigest'][:19]} again. The next run uses it.\n\n"
           f"Nothing else to do unless you want the new code back: fix it and push (CI deploys and tags it prod).\n"
           f"Why it failed: s3://<private>/prod/runs/<execution>/ and CloudWatch /basednews/jobs.")
    boto3.client("sns").publish(TopicArn=topic, Subject="BasedNews: automatic rollback", Message=msg)
    return _out("rolled_back", bad=bad, good=good, failures=streak)


def _out(event, **fields):
    print(json.dumps({"event": event, **fields}, default=str))
    return {"event": event, **fields}
