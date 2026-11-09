import pytest

from backend.app.core.errors import MetadataError
from backend.app.engine.execution.storage.column_updates import write_groups


def test_write_groups_checkpoint_dirty_dependencies_and_ignore_preserved_inputs():
    artifact = {
        "measureOrder": ["Forecast", "Sales", "Margin", "DoubleMargin"],
        "measureDependencies": {
            "Margin": ["Forecast", "Sales"],
            "DoubleMargin": ["Margin"],
        },
    }
    columns = [{"measureId": m} for m in artifact["measureOrder"]]
    assert write_groups(artifact, columns) == [
        ["Forecast", "Sales"],
        ["Margin"],
        ["DoubleMargin"],
    ]
    assert write_groups(artifact, columns[2:]) == [["Margin"], ["DoubleMargin"]]
    assert write_groups(artifact, []) == []


def test_write_groups_rejects_an_omitted_changed_measure():
    with pytest.raises(MetadataError, match="omits a changed measure"):
        write_groups(
            {"measureOrder": [], "measureDependencies": {}}, [{"measureId": "missing"}]
        )
