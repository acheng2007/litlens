import json
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import uuid4

trace_id = ContextVar("trace_id", default="")
logger = logging.getLogger("litlens")
logging.basicConfig(level=logging.INFO, format="%(message)s")


def event(name, **fields):
    logger.info(json.dumps({"event": name, "trace_id": trace_id.get(), **fields}, default=str))


@contextmanager
def span(name, **fields):
    started = time.perf_counter()
    span_id = uuid4().hex[:16]
    event(name + ".start", span_id=span_id, **fields)
    try:
        yield
    except Exception as exc:
        event(name + ".error", span_id=span_id, error_type=type(exc).__name__)
        raise
    finally:
        event(name + ".end", span_id=span_id,
              latency_ms=round((time.perf_counter() - started) * 1000, 2), **fields)
