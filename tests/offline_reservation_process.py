"""Real hard-exit injection at the run/intent/Studio identity transaction boundaries."""

import os
import sys
import time
from pathlib import Path

from tovitunes.config import RuntimeConfig
from tovitunes.creative.fake import FakeNIMTransport
from tovitunes.creative.provider import DurableStructuredGenerator
from tovitunes.creative.service import CreativeService
from tovitunes.orchestrator import build_orchestrator
from tovitunes.persistence.db import Database
from tovitunes.pipeline.targets import ProductionTarget
from tovitunes.web.jobs import JobManager


def main() -> None:
    config = RuntimeConfig(
        database_path=Path(sys.argv[1]), data_root=Path(sys.argv[2]), brand_root=Path(sys.argv[3])
    )
    boundary = sys.argv[4]
    database = Database(config.database_path)
    database.migrate()
    jobs = JobManager(database)
    reserve = CreativeService.reserve_next_run

    def reserve_then_exit(self, **kwargs):
        run_id = reserve(self, **kwargs)
        if boundary == "reserved":
            os._exit(73)
        return run_id

    CreativeService.reserve_next_run = reserve_then_exit

    def retain(db, reference):
        if boundary == "intent":
            os._exit(73)
        jobs.retain_reservation(db, reference)
        if boundary == "identity":
            os._exit(73)

    def prepare_then_exit(self, **kwargs):
        os._exit(73)

    CreativeService.prepare = prepare_then_exit
    flow = build_orchestrator(
        config, creative_provider=DurableStructuredGenerator(database, FakeNIMTransport())
    )
    jobs.submit(
        "studio",
        None,
        lambda: flow.generate(ProductionTarget.DRAFT, retain_reservation=retain),
        target=ProductionTarget.DRAFT,
    )
    time.sleep(30)
    raise RuntimeError("crash boundary was not reached")


if __name__ == "__main__":
    main()
