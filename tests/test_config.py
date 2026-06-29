from pathlib import Path

import pytest

from bdx_slow_control.config import ConfigurationError, load_json, normalized_prefix


def test_normalized_prefix_adds_separator():
    assert normalized_prefix("BDX:TEST") == "BDX:TEST:"


def test_normalized_prefix_preserves_separator():
    assert normalized_prefix("BDX:TEST:") == "BDX:TEST:"


def test_load_json_rejects_missing_file(tmp_path: Path):
    with pytest.raises(ConfigurationError):
        load_json(tmp_path / "missing.json")
