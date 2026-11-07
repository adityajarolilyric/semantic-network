"""One-time, backed-up migration to Dimension aggregates. Never invoked by the service.

Run with --backup /absolute/path.json for a dry run; add --apply to commit.
Historical data and physical ClickHouse tables are retained. Old build fingerprints
become stale and are refreshed by the next explicit Build Semantic operation.
"""

import argparse
import asyncio
from pathlib import Path
import sys
from datetime import datetime, timezone
from bson import json_util
from pymongo import AsyncMongoClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.app.core.config import Settings
from backend.app.services.dimension_grains import dimension_grain

OLD_GRAIN = "planning" + "Level"
OLD_RAW = "dimension"
KEYS = {
    "base" + OLD_GRAIN[0].upper() + OLD_GRAIN[1:] + "Id": "baseDimensionId",
    "native" + OLD_GRAIN[0].upper() + OLD_GRAIN[1:] + "Id": "nativeDimensionId",
    "source" + OLD_GRAIN[0].upper() + OLD_GRAIN[1:] + "Id": "sourceDimensionId",
    "counting" + OLD_GRAIN[0].upper() + OLD_GRAIN[1:] + "Id": "countingDimensionId",
    OLD_GRAIN + "Id": "dimensionId",
    "dimensionId": "memberCatalogId",
    "dimensionColumnId": "memberColumnId",
    "dimensionBindings": "memberBindings",
    "dimensionOwnerId": "memberCatalogId",
}


def convert(value):
    if isinstance(value, list):
        return [convert(item) for item in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            new_key = KEYS.get(key, key.replace(OLD_GRAIN + ":", "dimension:"))
            if key in {"kind", "resourceKind"}:
                item = "dimensionMembers" if item == OLD_RAW and key == "kind" else item
                item = "dimension" if item == OLD_GRAIN else item
                item = "dimensionPreview" if item == OLD_GRAIN + "Preview" else item
            result[new_key] = convert(item)
        return result
    return value


def migrate_objects(documents):
    converted = [convert(document) for document in documents]
    members = {
        (d["modelId"], d["revisionId"], d["objectId"]): d
        for d in converted
        if d["kind"] == "dimensionMembers"
    }
    dimensions = [d for d in converted if d["kind"] == "dimension"]
    attached = set()
    for dimension in dimensions:
        key = (
            dimension["modelId"],
            dimension["revisionId"],
            dimension.get("memberCatalogId"),
        )
        catalog = members.get(key)
        if catalog:
            dimension["memberCatalog"] = catalog["definition"]
            dimension["isTimeDim"] = catalog["isTimeDim"]
            attached.add(key)
    for key, catalog in members.items():
        if key in attached:
            continue
        dimension = dimension_grain({k: v for k, v in catalog.items() if k != "_id"})
        dimension.update(
            {
                k: catalog[k]
                for k in ("modelId", "revisionId", "version", "createdAt", "updatedAt")
            }
        )
        dimensions.append(dimension)
    return [
        d for d in converted if d["kind"] not in {"dimension", "dimensionMembers"}
    ] + dimensions


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    settings = Settings()
    client = AsyncMongoClient(settings.mongo_uri.get_secret_value())
    database = client[settings.mongo_database]
    try:
        collections = await database.list_collection_names()
        snapshot = {
            name: await database[name].find({}).to_list(None) for name in collections
        }
        if snapshot.get("semantic_revisions") and all(
            "baseDimensionId" in r
            and ("base" + OLD_GRAIN[0].upper() + OLD_GRAIN[1:] + "Id") not in r
            for r in snapshot["semantic_revisions"]
        ):
            print("Metadata already uses Dimensions; no changes made.")
            return
        active = [
            r
            for r in snapshot.get("engine_runs", [])
            if r.get("state") in {"queued", "running", "waiting"}
        ]
        if active:
            raise RuntimeError("Finish or cancel active engine jobs before migrating.")
        if args.backup.exists():
            raise RuntimeError(
                "Choose a new backup path; existing backups are never overwritten."
            )
        args.backup.write_text(
            json_util.dumps(
                {
                    "database": settings.mongo_database,
                    "createdAt": datetime.now(timezone.utc),
                    "collections": snapshot,
                }
            )
        )
        args.backup.chmod(0o600)
        objects = migrate_objects(snapshot.get("semantic_objects", []))
        from backend.app.schemas.objects import OBJECT_DOCUMENT

        from backend.app.domain.measure_policies import normalize_measure

        normalised = []
        for document in objects:
            validated = OBJECT_DOCUMENT.validate_python(
                normalize_measure({k: v for k, v in document.items() if k != "_id"})
            ).model_dump()
            if "_id" in document:
                validated["_id"] = document["_id"]
            normalised.append(validated)
        objects = normalised
        for revision in snapshot.get("semantic_revisions", []):
            scoped = [
                d
                for d in objects
                if d["modelId"] == revision["modelId"]
                and d["revisionId"] == revision["revisionId"]
            ]
            # Graph data types can depend on managed schemas, so full data checks run on build.
            if not all(d["kind"] != "dimensionMembers" for d in scoped):
                raise RuntimeError("Standalone member metadata remained.")
        print(
            f"Backup: {args.backup}. Dimensions: {sum(d['kind'] == 'dimension' for d in objects)}. Apply: {args.apply}"
        )
        if not args.apply:
            return
        async with client.start_session() as session:
            async with await session.start_transaction():
                if await database.engine_runs.count_documents(
                    {"state": {"$in": ["queued", "running", "waiting"]}},
                    session=session,
                ):
                    raise RuntimeError(
                        "An engine job started; finish it before migrating."
                    )
                for revision in snapshot.get("semantic_revisions", []):
                    current = await database.semantic_revisions.find_one(
                        {"_id": revision["_id"]}, session=session
                    )
                    if current is None or current["version"] != revision["version"]:
                        raise RuntimeError(
                            "Metadata changed since the backup; rerun with a new backup path."
                        )
                for name, documents in snapshot.items():
                    replacements = (
                        objects
                        if name == "semantic_objects"
                        else [convert(d) for d in documents]
                    )
                    if name == "semantic_revisions":
                        for revision in replacements:
                            revision["metadataVocabulary"] = "dimensions-v1"
                            revision["version"] += 1
                    # Write only changed documents and delete only absorbed member records.
                    before = {d["_id"]: d for d in documents}
                    keep = set()
                    for document in replacements:
                        if "_id" in document:
                            keep.add(document["_id"])
                            if document != before.get(document["_id"]):
                                await database[name].replace_one(
                                    {"_id": document["_id"]}, document, session=session
                                )
                        else:
                            await database[name].insert_one(document, session=session)
                    removed = set(before) - keep
                    if removed:
                        await database[name].delete_many(
                            {"_id": {"$in": list(removed)}}, session=session
                        )
        print(
            "Migration committed. Physical tables were retained; explicitly Build Semantic to refresh stale build fingerprints."
        )
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
