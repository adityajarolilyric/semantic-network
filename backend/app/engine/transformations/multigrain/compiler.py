"Transaction matching and last-application overlay, without persistent SQL objects."

from ..dates.calendar import effective_calendar_objects
from .inputs import ResolutionInputCompiler
from .matching import ResolutionMatchCompiler
from .overlay import ResolutionOverlayCompiler
from .planner import TransactionResolutionPlanner


class TransactionResolutionCompiler:
    def __init__(self, host, transaction, profiles, max_rows):
        self.host, self.tx, self.profiles, self.max_rows = (
            host,
            transaction,
            profiles,
            max_rows,
        )
        self.d = transaction["definition"]
        self.source = host.managed[self.d["sourceId"]]
        self.planner = TransactionResolutionPlanner(
            effective_calendar_objects(list(host.objects.values()), host.managed),
            transaction,
            self.source["columns"],
        )
        self.config, self.level = (
            self.planner.config,
            self.d.get("nativeGrainId") or self.d["dimensionId"],
        )
        self.inputs_compiler = ResolutionInputCompiler(self)
        self.matching_compiler = ResolutionMatchCompiler(self)
        self.overlay_compiler = ResolutionOverlayCompiler(self)

    def compile(self):
        prepared = self.inputs_compiler.compile()
        branches, latest_nodes = ([], [])
        for profile in self.profiles:
            branch, latest = self.matching_compiler.compile(prepared, profile)
            branches.append(branch)
            latest_nodes.append(latest)
        return self.overlay_compiler.compile(prepared, branches, latest_nodes)
