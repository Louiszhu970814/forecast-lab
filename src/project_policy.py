"""Fail-closed trial membership policy, project_policy_v1 (D79).

API project buckets identify types; default_project is a reference, not a type.
Unknown buckets/types must be verified against the API before adding them here.
"""

VERSION = "project_policy_v1"
PROJECT_TYPES = frozenset({"tournament", "site_main", "question_series", "category"})
MEMBERSHIP_UNKNOWN = "post project membership unavailable or unrecognized; cannot establish which projects it belongs to"


def trial_membership_error(post: dict, bot_testing_id: int) -> str | None:
    projects = post.get("projects")
    uncertain = MEMBERSHIP_UNKNOWN
    if not isinstance(projects, dict) or not projects:
        return uncertain
    typed, defaults = {}, []
    for bucket, values in projects.items():
        if bucket not in PROJECT_TYPES | {"default_project"}:
            return uncertain
        if values is None:
            if bucket == "default_project":
                continue
            return uncertain
        values = values if isinstance(values, list) else [values]
        for value in values:
            if not isinstance(value, dict) or type(value.get("id")) is not int or value["id"] <= 0:
                return uncertain
            pid, kind = value["id"], value.get("type")
            if kind is not None and kind not in PROJECT_TYPES:
                return uncertain
            if bucket == "default_project":
                defaults.append((pid, kind))
                continue
            if kind is not None and kind != bucket:
                return uncertain
            if pid in typed and typed[pid] != bucket:
                return uncertain
            typed[pid] = bucket
    for pid, kind in defaults:
        if kind is None:
            kind = typed.get(pid)
        if kind is None or (pid in typed and typed[pid] != kind):
            return uncertain
        typed[pid] = kind
    if not typed:
        return uncertain
    if any(kind == "tournament" and pid != bot_testing_id for pid, kind in typed.items()):
        return "post belongs to a tournament outside bot testing area"
    return None
