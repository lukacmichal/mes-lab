import copy

import pytest

from mes_lab import DEFAULT_CONFIG_DIR
from mes_lab.model import ModelError, build_model
from mes_lab.model.loader import _read
from mes_lab.model.schema import AreaKind


def test_plant_layout(model):
    assert len(model.lines_of_kind(AreaKind.SMT)) == 5
    assert len(model.lines_of_kind(AreaKind.FA)) == 5
    assert len(model.lines_of_kind(AreaKind.PACK)) == 2
    assert model.lines["FA-01"].stations[-1].code == "FA-01-FIN"
    # a cell of two testers shares one position in the flow
    eol = [s for s in model.lines["FA-01"].stations if s.type == "EOL_TESTER"]
    assert [s.code for s in eol] == ["FA-01-EOL1", "FA-01-EOL2"]
    assert eol[0].cell == eol[1].cell
    assert len(set(model.stations)) == len(model.stations)


def test_every_finished_good_has_a_path_through_the_plant(model):
    for fg in model.finished_goods():
        pcba = model.materials[fg.pcba]
        assert pcba.routing[-1].steps.get("DEPANELER")
        assert any(fg.code in l.materials for l in model.lines_of_kind(AreaKind.FA))
        assert model.pack_line_for(fg.code)


def _broken(mutate):
    data = copy.deepcopy(_read(DEFAULT_CONFIG_DIR))
    mutate(data)
    with pytest.raises(ModelError) as exc:
        build_model(data)
    return exc.value.problems


def test_unknown_station_type_in_routing_is_reported():
    def mutate(d):
        d["materials"][0]["routing"][0]["steps"]["WAVE_SOLDER"] = 30
    problems = _broken(mutate)
    assert any("WAVE_SOLDER" in p for p in problems)


def test_fa_line_must_have_all_stations_of_its_product():
    def mutate(d):
        fa = next(a for a in d["areas"] if a["code"] == "FA")
        fa["lines"][2]["materials"] = ["BCM-200"]  # LED line cannot build BCM-200
    problems = _broken(mutate)
    assert any("FA-03 lacks" in p for p in problems)


def test_unknown_defect_code_is_reported():
    def mutate(d):
        d["station_types"]["AOI"]["detects"]["FLUX_RESIDUE"] = 0.5
    assert any("FLUX_RESIDUE" in p for p in _broken(mutate))
