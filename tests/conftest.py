from pathlib import Path

import pytest

from tovitunes.catalog import BrandCatalog, load_brand


@pytest.fixture
def brand_root() -> Path:
    return Path(__file__).resolve().parents[1] / "brands" / "tovitunes"


@pytest.fixture
def catalog(brand_root: Path) -> BrandCatalog:
    return load_brand(brand_root)

