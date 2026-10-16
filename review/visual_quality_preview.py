"""Provider-free V3 evaluation in a new copy; never opens production data for writing."""

import argparse
import json
import shutil
import sqlite3
from contextlib import closing
from hashlib import sha256
from pathlib import Path

from tovitunes.config import RuntimeConfig
from tovitunes.execution import ProductionExecutionOwnership
from tovitunes.persistence.db import Database
from tovitunes.render.production import ProductionRenderer


def prepare_copy(source: Path, workspace: Path) -> RuntimeConfig:
    source = source.resolve(strict=True)
    workspace = workspace.resolve()
    if workspace == source or source in workspace.parents or workspace in source.parents:
        raise ValueError("preview workspace must be separate from the production checkout")
    workspace.mkdir(parents=True, exist_ok=False)
    data = workspace / "data"
    data.mkdir()
    with closing(
        sqlite3.connect(f"file:{source.as_posix()}/data/tovitunes.db?mode=ro", uri=True)
    ) as original:
        with closing(sqlite3.connect(data / "tovitunes.db")) as copied:
            original.backup(copied)
        for rel, digest in original.execute("SELECT relative_path,sha256 FROM artifact_versions"):
            path = source / "data" / rel
            target = (data / rel).resolve()
            if data.resolve() not in target.parents:
                raise ValueError("artifact path escapes isolated workspace")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            if sha256(target.read_bytes()).hexdigest() != digest:
                raise ValueError("copied artifact differs from its registered SHA")
    shutil.copytree(source / "brands", workspace / "brands")
    shutil.copytree(source / "data" / "music-benchmark", data / "music-benchmark")
    return RuntimeConfig(
        database_path=data / "tovitunes.db",
        data_root=data,
        brand_root=workspace / "brands" / "tovitunes",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--episode-key", required=True)
    parser.add_argument("--replace-role", action="append", default=[], metavar="ROLE=EXISTING_ROLE")
    args = parser.parse_args()
    config = prepare_copy(args.source_root, args.workspace)
    overrides = dict(item.split("=", 1) for item in args.replace_role)
    with ProductionExecutionOwnership(
        Database(config.database_path), operation="visual-quality-preview"
    ) as owner:
        result = ProductionRenderer(
            config,
            local_preview=True,
            visual_quality=True,
            environment_role_overrides=overrides,
            assert_owner=owner.assert_owned,
        ).render(args.episode_key, visual_story=True)
    args.output.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(result["output_path"], args.output / "preview.mp4")
    (args.output / "preview-result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    for source_path in Path(result["frames_path"]).glob("*.png"):
        shutil.copyfile(source_path, args.output / ("after-" + source_path.name))
    print(
        json.dumps(
            {
                k: result[k]
                for k in ("final_render_id", "mp4_sha256", "provider_calls", "publication")
            }
        )
    )


if __name__ == "__main__":
    main()
