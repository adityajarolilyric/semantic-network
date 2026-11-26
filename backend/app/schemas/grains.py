"""Stable semantic grain owners; a Transaction binding can change independently."""

from typing import Literal

from .common import Identifier, Schema


class GrainReference(Schema):
    kind: Literal["transaction", "dimension"]
    objectId: Identifier
