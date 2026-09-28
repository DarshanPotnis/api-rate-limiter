"""Tests for sending the app's own log records to the console."""

import logging
from collections.abc import Iterator

import pytest

from app.logging_setup import configure_logging


@pytest.fixture
def app_logger() -> Iterator[logging.Logger]:
    logger = logging.getLogger("app")
    level, handlers = logger.level, list(logger.handlers)
    yield logger
    logger.setLevel(level)
    logger.handlers[:] = handlers


def test_app_logs_go_to_one_handler_at_the_configured_level(app_logger: logging.Logger) -> None:
    configure_logging("INFO")
    configure_logging("INFO")  # the lifespan can run more than once in one process

    assert app_logger.level == logging.INFO
    assert len(app_logger.handlers) == 1
    assert logging.getLogger("app.providers.ollama").isEnabledFor(logging.INFO)


def test_other_libraries_keep_their_own_levels(app_logger: logging.Logger) -> None:
    before = logging.getLogger("httpx").getEffectiveLevel()

    configure_logging("DEBUG")

    assert logging.getLogger("httpx").getEffectiveLevel() == before
