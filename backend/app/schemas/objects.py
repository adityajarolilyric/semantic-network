from typing import Annotated, Union

from pydantic import Field, TypeAdapter

from .dimensions import DimensionDocument, DimensionInput
from .measures import MeasureDocument, MeasureInput
from .planning_levels import PlanningLevelDocument, PlanningLevelInput
from .sources import SourceDocument, SourceInput
from .transactions import TransactionDocument, TransactionInput

ObjectInputUnion = Annotated[
    Union[
        SourceInput, DimensionInput, PlanningLevelInput, TransactionInput, MeasureInput
    ],
    Field(discriminator="kind"),
]
ObjectDocument = Annotated[
    Union[
        SourceDocument,
        DimensionDocument,
        PlanningLevelDocument,
        TransactionDocument,
        MeasureDocument,
    ],
    Field(discriminator="kind"),
]
OBJECT_INPUT = TypeAdapter(ObjectInputUnion)
OBJECT_DOCUMENT = TypeAdapter(ObjectDocument)
