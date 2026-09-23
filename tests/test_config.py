from pathlib import Path

import pytest
from pydantic import ValidationError

from tovitunes.config import load_config


def test_paths_are_relative_to_config_and_publishing_is_closed(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "schema_version: 1\n"
        "database_path: data/tovitunes.db\n"
        "data_root: data\n"
        "brand_root: brands/tovitunes\n"
        "publication_enabled: false\n",
        encoding="utf-8",
    )
    config = load_config(config_path)
    assert config.database_path == tmp_path / "data" / "tovitunes.db"
    assert config.expected_youtube_channel_id is None
    assert config.publication_enabled is False


def test_unknown_keys_and_missing_channel_fail(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    common = "database_path: data/x.db\ndata_root: data\nbrand_root: brands\n"
    config_path.write_text(common + "invented: true\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_config(config_path)
    config_path.write_text(common + "publication_enabled: true\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="expected YouTube channel ID"):
        load_config(config_path)

