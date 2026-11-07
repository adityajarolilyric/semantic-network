from typing import Annotated, Union

from pydantic import Field, TypeAdapter

from .dimension_members import DimensionMembersDocument, DimensionMembersInput
from .dimensions import DimensionDocument, DimensionInput
from .measures import MeasureDocument, MeasureInput
from .sources import SourceDocument, SourceInput
from .transactions import TransactionDocument, TransactionInput

ObjectInputUnion = Annotated[
    Union[
        SourceInput,
        DimensionMembersInput,
        DimensionInput,
        TransactionInput,
        MeasureInput,
    ],
    Field(discriminator="kind"),
]
ObjectDocument = Annotated[
    Union[
        SourceDocument,
        DimensionMembersDocument,
        DimensionDocument,
        TransactionDocument,
        MeasureDocument,
    ],
    Field(discriminator="kind"),
]
OBJECT_INPUT = TypeAdapter(ObjectInputUnion)
OBJECT_DOCUMENT = TypeAdapter(ObjectDocument)
