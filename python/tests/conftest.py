from __future__ import annotations

import os
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _default_test_temp_base() -> Path:
    override = os.environ.get("FLOW_APP_TEST_TEMP_ROOT")
    if override:
        return Path(override)
    return Path(tempfile.gettempdir()) / "equity-option-tools-pytest"


def _configure_test_tempdir() -> Path:
    root = _default_test_temp_base() / f"run-{os.getpid()}-{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    return root


_TEST_TEMP_ROOT = _configure_test_tempdir()


def _set_temp_env(tempdir: Path) -> None:
    tempdir_str = str(tempdir)
    os.environ["TMP"] = tempdir_str
    os.environ["TEMP"] = tempdir_str
    os.environ["TMPDIR"] = tempdir_str
    tempfile.tempdir = tempdir_str


_set_temp_env(_TEST_TEMP_ROOT)


@pytest.fixture
def tmp_path() -> Path:
    path = _TEST_TEMP_ROOT / f"path-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path
