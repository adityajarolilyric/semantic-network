"""One-time count metadata migration through the API; never runs in the service.

Capture a journal before deploying the simplified API, then apply it afterward.
Only count definitions are updated. Run an incremental network build afterward;
Full Refresh is unnecessary and would replace manual edits.
"""

import argparse
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

META = {"modelId", "revisionId", "version", "createdAt", "updatedAt"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8788")
    parser.add_argument("--model", default="default_model")
    parser.add_argument("--revision", default="revision_1")
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root = f"/api/v1/models/{args.model}/revisions/{args.revision}"
    headers = {"Content-Type": "application/json"}
    if os.getenv("METADATA_API_KEY"):
        headers["Authorization"] = "Bearer " + os.environ["METADATA_API_KEY"]

    def request(path, method="GET", body=None, version=None):
        req = Request(
            args.url.rstrip("/") + path,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={**headers, **({"If-Match": str(version)} if version else {})},
        )
        with urlopen(req, timeout=30) as response:
            return json.load(response)

    def save(journal):
        with args.journal.open("w") as handle:
            os.chmod(args.journal, 0o600)
            json.dump(journal, handle, indent=2)

    if not args.apply:
        if args.journal.exists():
            raise SystemExit(
                "Journal already exists; retain the original backup and use --apply."
            )
        entries, cursor = [], None
        while True:
            page = request(
                root + "/measures?limit=100" + ("&cursor=" + cursor if cursor else "")
            )
            entries.extend(
                {"before": item, "applied": False}
                for item in page["items"]
                if item["definition"].get("countingPlanningLevelId")
            )
            cursor = page.get("nextCursor")
            if not cursor:
                break
        save({"scope": root, "entries": entries})
        print(
            f"Captured {len(entries)} count definitions. Deploy the new API, then run --apply."
        )
        return
    journal = json.loads(args.journal.read_text())
    if journal["scope"] != root:
        raise SystemExit("Journal scope differs from this migration target.")
    schemas = request("/openapi.json")["components"]["schemas"]
    definitions = [
        schema
        for name, schema in schemas.items()
        if name.startswith("CalculatedMeasureDefinition")
    ]
    if not definitions or any(
        "countingPlanningLevelId" in schema["properties"] for schema in definitions
    ):
        raise SystemExit("Deploy the simplified API before applying this migration.")
    for entry in journal["entries"]:
        if entry["applied"]:
            continue
        old = entry["before"]
        current = request(root + "/measures/" + old["objectId"])
        if (
            current["definition"]["nativePlanningLevelId"]
            != old["definition"]["countingPlanningLevelId"]
        ):
            raise SystemExit(
                "Output level changed concurrently; inspect the journal before retrying."
            )
        if current["version"] != old["version"]:
            if current["version"] == old["version"] + 1:
                payload = {
                    key: value for key, value in current.items() if key not in META
                }
                expected = {key: value for key, value in old.items() if key not in META}
                expected["definition"] = dict(expected["definition"])
                expected["definition"].pop("countingPlanningLevelId", None)
                # A prior successful same-level update may have lost its journal write.
                if (
                    old["definition"]["nativePlanningLevelId"]
                    == current["definition"]["nativePlanningLevelId"]
                    and payload == expected
                ):
                    entry["applied"] = True
                    save(journal)
                    continue
            raise SystemExit(
                "Measure changed since capture; inspect the journal before retrying."
            )
        payload = {key: value for key, value in current.items() if key not in META}
        updated = request(
            root + "/measures/" + old["objectId"], "PUT", payload, current["version"]
        )
        entry.update(applied=True, version=updated["version"])
        save(journal)
    print(
        f"Migrated {len(journal['entries'])} count definitions; warehouse data was not rebuilt."
    )


if __name__ == "__main__":
    main()
