class CollectionSchemas:
    """MongoDB guards envelope shape; Pydantic and graph validation enforce domain rules."""

    REQUIRED = {
        "workspace_control": ["controlId", "phase", "epoch", "sequence", "leaseUntil"],
        "workspace_leases": ["leaseId", "epoch", "expiresAt"],
        "semantic_models": ["modelId", "name", "version", "createdAt", "updatedAt"],
        "semantic_revisions": [
            "modelId",
            "revisionId",
            "revisionNumber",
            "approvalStatus",
            "version",
            "createdAt",
            "updatedAt",
        ],
        "semantic_objects": [
            "modelId",
            "revisionId",
            "objectId",
            "kind",
            "name",
            "definition",
            "version",
            "createdAt",
            "updatedAt",
        ],
        "semantic_evidence": [
            "modelId",
            "revisionId",
            "recordId",
            "objectIds",
            "result",
            "checkedAt",
            "version",
            "createdAt",
            "updatedAt",
        ],
        "semantic_examples": [
            "modelId",
            "revisionId",
            "recordId",
            "objectIds",
            "businessQuestion",
            "version",
            "createdAt",
            "updatedAt",
        ],
        "semantic_contracts": [
            "contractId",
            "contractVersion",
            "definition",
            "version",
            "createdAt",
            "updatedAt",
        ],
    }

    REQUIRED.update(
        {
            "semantic_managed_tables": [
                "modelId",
                "revisionId",
                "sourceId",
                "definition",
                "definitionHash",
                "version",
                "createdAt",
                "updatedAt",
            ],
            "engine_runs": [
                "modelId",
                "revisionId",
                "runId",
                "state",
                "requestHash",
                "createdAt",
                "updatedAt",
            ],
            "engine_table_resources": [
                "modelId",
                "revisionId",
                "resourceKind",
                "resourceId",
                "physicalName",
                "storageOwner",
                "columns",
            ],
            "engine_read_leases": ["readId", "artifactId", "expiresAt"],
            "engine_artifact_pins": ["runId", "artifactId"],
            "engine_run_inputs": ["runId", "inputId", "document"],
            "engine_run_steps": ["runId", "stepId", "state"],
            "engine_artifacts": [
                "artifactId",
                "runId",
                "physicalName",
                "state",
                "createdAt",
            ],
            "engine_artifact_bindings": [
                "modelId",
                "revisionId",
                "artifactId",
                "definitionHash",
            ],
        }
    )

    @classmethod
    def validator(cls, collection):
        properties = {
            "version": {"bsonType": ["int", "long"], "minimum": 1},
            "createdAt": {"bsonType": "date"},
            "updatedAt": {"bsonType": "date"},
        }
        for name in (
            "modelId",
            "revisionId",
            "objectId",
            "recordId",
            "contractId",
            "sourceId",
            "runId",
            "artifactId",
        ):
            properties[name] = {
                "bsonType": "string",
                "pattern": "^[A-Za-z][A-Za-z0-9_-]{0,127}$",
            }
        if collection == "engine_read_leases":
            properties["expiresAt"] = {"bsonType": "date"}
        if collection == "engine_runs":
            properties["state"] = {
                "enum": [
                    "queued",
                    "running",
                    "waiting",
                    "succeeded",
                    "failed",
                    "cancelled",
                    "superseded",
                ]
            }
        if collection == "engine_artifacts":
            properties["state"] = {
                "enum": ["candidate", "ready", "retiring", "dropped"]
            }
        if collection == "semantic_managed_tables":
            properties["definition"] = {"bsonType": "object"}
            properties["definitionHash"] = {
                "bsonType": "string",
                "pattern": "^[a-f0-9]{64}$",
            }
        if collection == "semantic_objects":
            properties["kind"] = {
                "enum": [
                    "source",
                    "dimension",
                    "transaction",
                    "measure",
                ]
            }
            properties["definition"] = {"bsonType": "object"}
        if collection == "semantic_revisions":
            properties["approvalStatus"] = {"enum": ["draft", "published"]}
        clauses = [
            {
                "$jsonSchema": {
                    "bsonType": "object",
                    "required": cls.REQUIRED[collection],
                    "properties": properties,
                }
            },
            {"tenantId": {"$exists": False}},
            {"physicalBinding": {"$exists": False}},
        ]
        if collection == "semantic_objects":
            clauses += [
                {"isBase": {"$exists": False}},
                {
                    "$or": [
                        {"kind": {"$ne": "source"}},
                        {
                            "displayName": {"$exists": False},
                            "definition.columns.displayName": {"$exists": False},
                        },
                    ]
                },
            ]
        return {"$and": clauses}
