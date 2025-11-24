import asyncio
import logging

from app import queue as job_queue, tasks, database


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ingestion-worker")


async def _worker_loop():
    logger.info("Ingestion worker is running.")
    while True:
        payload = await job_queue.fetch_ingest_job(timeout=5)
        if payload is None:
            continue

        job_id = payload.get("job_id", "unknown")
        logger.info("Dequeued ingestion job %s", job_id)
        try:
            await tasks.process_ingestion_job(**payload)
        except Exception:
            logger.exception("Ingestion job %s crashed unexpectedly", job_id)


async def main():
    logger.info("Booting ingestion worker...")
    try:
        await job_queue.ping()
        logger.info("Connected to Redis queue.")
    except Exception:
        logger.exception("Unable to connect to Redis.")
        raise

    try:
        await _worker_loop()
    finally:
        await job_queue.close()
        await database.close_pool()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Worker interrupted, shutting down.")

