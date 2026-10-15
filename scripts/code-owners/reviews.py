"""Approval-based reviewer weights from PR review dumps."""

import glob
import json
import os
from datetime import datetime, timezone


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _is_bot(login: str) -> bool:
    return login.endswith("[bot]") or login in ("mssonicbld", "mssoniccd", "copilot-pull-request-reviewer", "Copilot")


def compute_review_weights(
    reviews_path: str, half_life_days: float, now: datetime = None
) -> dict:
    """Return {folder: {login: weight}} from approvals.

    reviews_path is a JSON file or a directory of JSON files, each a list of
    {number, author, mergedAt, files, approvals: [{login, at}]}.
    Each reviewer is credited once per PR per folder, using their latest
    approval time, decayed by 0.5 ** (age_days / half_life_days).
    The PR author and bots are ignored.
    """
    now = now or datetime.now(timezone.utc)
    if os.path.isdir(reviews_path):
        paths = sorted(glob.glob(os.path.join(reviews_path, "*.json")))
    else:
        paths = [reviews_path]
    weights = {}
    for path in paths:
        with open(path) as f:
            prs = json.load(f)
        for pr in prs:
            folders = {"/"}
            for file in pr.get("files", []):
                parts = file.split("/")[:-1]
                for i in range(1, len(parts) + 1):
                    folders.add("/" + "/".join(parts[:i]) + "/")
            latest = {}
            for a in pr.get("approvals", []):
                login = a["login"]
                if login == pr.get("author") or _is_bot(login):
                    continue
                if login not in latest or a["at"] > latest[login]:
                    latest[login] = a["at"]
            for login, at in latest.items():
                age = max((now - _parse(at)).total_seconds() / 86400, 0)
                w = 0.5 ** (age / half_life_days) if half_life_days > 0 else 1
                for folder in folders:
                    d = weights.setdefault(folder, {})
                    d[login] = d.get(login, 0) + w
    return weights


def combine_scores(change, review, change_weight: float):
    """Blend max-normalized change scores with max-normalized review scores.

    change is {folder: {login: score}} (already normalized, may hold inf
    for preset owners, kept as is). review is raw {folder: {login: w}}.
    Only folders present in change are kept.
    """
    inf = float("inf")
    out = {}
    for folder, owners in change.items():
        r = review.get(folder, {})
        top = max(r.values(), default=0)
        merged = {}
        for login in set(owners) | set(r):
            c = owners.get(login, 0)
            if c == inf:
                merged[login] = inf
                continue
            rs = r.get(login, 0) / top if top else 0
            merged[login] = round(
                change_weight * c + (1 - change_weight) * rs, 4
            )
        out[folder] = merged
    return out
