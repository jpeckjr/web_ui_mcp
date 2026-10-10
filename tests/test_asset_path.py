import pytest

from src.adapters.informatica import AssetLocation, parse_asset_path


def test_project_folder_asset():
    loc = parse_asset_path("staging/salesforce/mt_stage_salesforce_contract")
    assert loc == AssetLocation("staging", "salesforce", "mt_stage_salesforce_contract")
    assert loc.path == "staging/salesforce/mt_stage_salesforce_contract"


def test_project_root_asset():
    loc = parse_asset_path("staging/mt_root_task")
    assert loc.folder is None
    assert loc.path == "staging/mt_root_task"


@pytest.mark.parametrize("bad", ["", "  ", "only_one", "a//b", "a/b/c/d"])
def test_rejects_bad_paths(bad):
    with pytest.raises(ValueError):
        parse_asset_path(bad)
