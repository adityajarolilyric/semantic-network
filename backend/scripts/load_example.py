"""Create the PLT/PT draft through the public API; never writes warehouse data."""

import argparse
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8788")
    args = parser.parse_args()
    sample = json.loads(
        (Path(__file__).resolve().parents[1] / "examples" / "plt_pt.json").read_text()
    )
    headers = {"Content-Type": "application/json"}
    if os.getenv("METADATA_API_KEY"):
        headers["Authorization"] = "Bearer " + os.environ["METADATA_API_KEY"]

    def post(path, payload):
        request = Request(
            args.url.rstrip("/") + "/api/v1" + path,
            data=json.dumps(payload).encode(),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=30) as response:
                return json.load(response)
        except HTTPError as error:
            raise SystemExit(
                f"Metadata API returned HTTP {error.code} for {path}; inspect the draft before retrying. Existing resources are not overwritten."
            ) from None

    source_model = sample["semantic_models"][0]
    model = post(
        "/models",
        {
            "modelId": source_model["_id"],
            "name": source_model["name"],
            "displayName": source_model["displayName"],
            "description": source_model["description"],
        },
    )
    revision = sample["semantic_revisions"][0]
    model_path = "/models/" + model["modelId"]
    created = post(
        model_path + "/revisions",
        {
            "revisionId": revision["_id"],
            "expectedModelVersion": model["version"],
            "baseDimensionId": revision["baseDimensionId"],
        },
    )
    root = model_path + "/revisions/" + created["revisionId"]
    resources = {
        "source": "sources",
        "dimensionMembers": "dimension-members",
        "dimension": "dimensions",
        "transaction": "transactions",
        "measure": "measures",
    }
    for obj in sample["semantic_objects"]:
        payload = {
            k: v
            for k, v in obj.items()
            if k
            not in {"_id", "modelId", "revisionId", "version", "createdAt", "updatedAt"}
        }
        post(root + "/" + resources[obj["kind"]], payload)
    report = post(root + "/validate", {})
    if not report["valid"]:
        raise SystemExit(
            "The draft was created but has semantic validation diagnostics; inspect the revision."
        )
    print(
        "Created the 16-object PLT/PT draft and validated its metadata. It has not been published."
    )


if __name__ == "__main__":
    main()
