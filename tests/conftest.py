import shutil
from pathlib import Path

import pytest

from tovitunes.catalog import BrandCatalog, load_brand


@pytest.fixture
def brand_root() -> Path:
    return Path(__file__).resolve().parents[1] / "brands" / "tovitunes"


@pytest.fixture
def catalog(brand_root: Path) -> BrandCatalog:
    return load_brand(brand_root)


@pytest.fixture
def draft_catalog(tmp_path: Path, brand_root: Path) -> BrandCatalog:
    root = tmp_path / "draft-brand"
    shutil.copytree(brand_root, root)
    manifest = root / "characters/tovi/packs/v1/pack.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("readiness: approved", "readiness: draft"),
        encoding="utf-8",
    )
    return load_brand(root)

