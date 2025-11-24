# app/queue.py

import json
from typing import Any, Dict, Optional

import redis.asyncio as redis

from app import settings


_redis = redis.Redis.from_url(
    settings.REDIS_URL,
    encoding="utf-8",
    decode_responses=True,
)

QUEUE_NAME = settings.REDIS_QUEUE_NAME


async def enqueue_ingest_job(payload: Dict[str, Any]) -> None:
    await _redis.lpush(QUEUE_NAME, json.dumps(payload))


async def fetch_ingest_job(timeout: int = 5) -> Optional[Dict[str, Any]]:
    result = await _redis.brpop(QUEUE_NAME, timeout=timeout)
    if not result:
        return None
    _, raw_payload = result
    return json.loads(raw_payload)


async def ping() -> None:
    await _redis.ping()


async def close() -> None:
    await _redis.close()

