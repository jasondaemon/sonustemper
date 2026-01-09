import os
import sys
import json
from typing import Any

LEVELS = {"error": 0, "summary": 1, "debug": 2}
LOG_LEVEL = LEVELS.get(os.getenv("LOG_LEVEL", "error").lower(), 0)
REDACT_KEYS = {
    "api_key",
    "proxy_shared_secret",
    "authorization",
    "cookie",
    "x-api-key",
    "x-sonustemper-proxy",
    "proxy-authorization",
}


def fmt_kv(**kv: Any) -> str:
    parts = []
    for k, v in kv.items():
        if k is None:
            continue
        key = str(k)
        val = "<redacted>" if key.lower() in REDACT_KEYS else v
        sval = json.dumps(val, ensure_ascii=False) if isinstance(val, (dict, list)) else str(val)
        if len(sval) > 500:
            sval = sval[:500] + "…"
        parts.append(f"{key}={sval}")
    return " ".join(parts)


def _log(level: str, tag: str, msg: str, **kv: Any) -> None:
    lvl_val = LEVELS.get(level, 0)
    if lvl_val > LOG_LEVEL:
        return
    line = f"[{level}][{tag}] {msg}"
    kvs = fmt_kv(**kv)
    if kvs:
        line = f"{line} | {kvs}"
    stream = sys.stderr if level == "error" else sys.stdout
    print(line, file=stream, flush=True)


def log_error(tag: str, msg: str, **kv: Any) -> None:
    _log("error", tag, msg, **kv)


def log_summary(tag: str, msg: str, **kv: Any) -> None:
    _log("summary", tag, msg, **kv)


def log_debug(tag: str, msg: str, **kv: Any) -> None:
    _log("debug", tag, msg, **kv)
