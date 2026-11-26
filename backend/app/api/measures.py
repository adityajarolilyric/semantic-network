from fastapi import Depends, Header, Response

from ..core.http import expected_version
from ..formula_language.service import FormulaLanguage
from ..schemas.common import Identifier
from ..schemas.formulas import FormulaValidationInput
from ..schemas.measures import MeasureBuildRequest, MeasureDocument, MeasureInput
from ..services.measures import MeasureService
from .base import RevisionResourceAPI


class MeasureAPI(RevisionResourceAPI):
    def __init__(self, service: MeasureService, builds=None, network=None):
        super().__init__(service, "measures", MeasureDocument)
        self.builds = builds
        self.network = network
        self.router.add_api_route(
            "/formula/language", self.formula_language, methods=["GET"]
        )
        self.router.add_api_route(
            "/formula/validate", self.validate_formula, methods=["POST"]
        )
        self.router.add_api_route(
            "/formula/attributes", self.formula_attributes, methods=["GET"]
        )
        self.router.add_api_route(
            "/formula/measures", self.formula_measures, methods=["GET"]
        )
        self.router.add_api_route(
            "/execution/base-visibility", self.base_visibility, methods=["GET"]
        )
        self.router.add_api_route(
            "/execution/build", self.build_values, methods=["POST"], status_code=202
        )
        self.router.add_api_route(
            "/execution/levels/{level_id}", self.values, methods=["GET"]
        )
        self.router.add_api_route(
            "/execution/levels/{level_id}/sql", self.final_sql, methods=["GET"]
        )
        self.router.add_api_route("/execution/levels/{level_id}/output-sql", self.final_sql, methods=["GET"])
        self.router.add_api_route("/execution/levels/{level_id}/build-sql", self.build_sql, methods=["GET"])
        self.router.add_api_route(
            "/{resource_id}/capabilities", self.capabilities, methods=["GET"]
        )

    async def formula_language(self, model_id: Identifier, revision_id: Identifier):
        return FormulaLanguage.catalog()

    async def validate_formula(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        payload: "FormulaValidationInput",
    ):
        return await self.service.validate_formula(model_id, revision_id, payload)

    async def formula_attributes(
        self, model_id: Identifier, revision_id: Identifier, dimensionId: Identifier
    ):
        return await self.service.formula_attributes(model_id, revision_id, dimensionId)

    async def formula_measures(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        nativeGrainId: Identifier,
        objectId: Identifier | None = None,
    ):
        return await self.service.formula_measures(
            model_id, revision_id, nativeGrainId, objectId
        )

    async def base_visibility(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        nativeGrainId: Identifier | None = None,
        nativeDimensionId: Identifier | None = None,
    ):
        return await self.builds.base_visibility(
            model_id, revision_id, nativeGrainId or nativeDimensionId
        )

    async def build_values(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        payload: MeasureBuildRequest | None = None,
        expected: int = Depends(expected_version),
        key: str = Header(alias="Idempotency-Key"),
    ):
        return await self.network.submit(
            model_id,
            revision_id,
            expected,
            key,
            mode=payload.mode if payload else "full",
        )

    async def values(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        level_id: Identifier,
        preview: bool = False,
    ):
        return await self.builds.status(model_id, revision_id, level_id, preview)

    async def build_sql(self, model_id: str, revision_id: str, level_id: str):
        return await self.builds.build_sql(model_id, revision_id, level_id)

    async def final_sql(
        self, model_id: Identifier, revision_id: Identifier, level_id: Identifier
    ):
        return await self.builds.final_sql(model_id, revision_id, level_id)

    async def capabilities(
        self, model_id: Identifier, revision_id: Identifier, resource_id: Identifier
    ):
        return await self.builds.capabilities(model_id, revision_id, resource_id)

    async def create(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        payload: MeasureInput,
        response: Response,
    ):
        return await super().create(model_id, revision_id, payload, response)

    async def update(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        resource_id: Identifier,
        payload: MeasureInput,
        response: Response,
        expected: int = Depends(expected_version),
    ):
        return await super().update(
            model_id, revision_id, resource_id, payload, response, expected
        )
