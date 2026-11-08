"""Regression tests for installed llama.cpp build identity parsing."""
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from portable.update_llamacpp import installed_build, parse_llamacpp_build


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("version: 0.9.0-dev (build 12177, commit 6c01da627)\n"
         "built with Clang 20.1.8 for Windows x86_64", 12177),
        ("version: 0.9.0-dev (build 12202, commit d6be06557)", 12202),
        ("build: 12202", 12202),
        ("version: b12202", 12202),
        ("llama.cpp b12202", 12202),
        ("version: 0.9.0-dev", None),
        ("version: 0.9.0-dev (build 0, commit 6c01da627)", None),
        ("version: 0.9.0-dev (build 12177, commit abc)\nbuild 12202", None),
    ],
)
def test_parse_llamacpp_build(output, expected):
    assert parse_llamacpp_build(output) == expected


def test_installed_build_uses_actual_build_number(tmp_path: Path):
    text = b"version: 0.9.0-dev (build 12202, commit d6be06557)\n"
    with patch(
        "portable.update_llamacpp.subprocess.run",
        return_value=CompletedProcess(args=[], returncode=0, stdout=text, stderr=b""),
    ):
        assert installed_build(tmp_path, "llama-server.exe") == 12202


def test_installed_build_unrecognized_output_is_not_assumed_zero(tmp_path: Path):
    with patch(
        "portable.update_llamacpp.subprocess.run",
        return_value=CompletedProcess(
            args=[], returncode=0, stdout=b"version: 0.9.0-dev\n", stderr=b"",
        ),
    ):
        assert installed_build(tmp_path, "llama-server.exe") is None
