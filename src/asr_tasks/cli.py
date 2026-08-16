from __future__ import annotations

import argparse
import logging
import multiprocessing
import sys
from pathlib import Path

import uvicorn
from alembic import command
from alembic.config import Config

from .api import create_app
from .config import Settings
from .worker import Worker


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def migrate(settings: Settings) -> None:
    settings.ensure_directories()
    root = Path(__file__).resolve().parents[2]
    config_path = root / "alembic.ini"
    if not config_path.is_file():
        config_path = Path.cwd() / "alembic.ini"
    config = Config(str(config_path))
    config.set_main_option("script_location", str(config_path.parent / "migrations"))
    config.set_main_option("sqlalchemy.url", settings.db_url)
    command.upgrade(config, "head")


def worker_entry() -> None:
    settings = Settings()
    configure_logging(settings.log_level)
    Worker(settings).run()


def run_api(settings: Settings) -> None:
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


def run_all(settings: Settings) -> None:
    context = multiprocessing.get_context("spawn")
    workers = [
        context.Process(target=worker_entry, daemon=False) for _ in range(settings.worker_count)
    ]
    for process in workers:
        process.start()
    try:
        run_api(settings)
    finally:
        for process in workers:
            if process.is_alive():
                process.terminate()
        for process in workers:
            process.join(timeout=10)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="asr-tasks service")
    parser.add_argument(
        "command", choices=["api", "worker", "all", "migrate"], nargs="?", default="all"
    )
    args = parser.parse_args(argv)
    settings = Settings()
    configure_logging(settings.log_level)
    migrate(settings)
    if args.command == "migrate":
        return
    if args.command == "api":
        run_api(settings)
    elif args.command == "worker":
        Worker(settings).run()
    else:
        run_all(settings)


if __name__ == "__main__":
    main(sys.argv[1:])
