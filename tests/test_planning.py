from mes_lab.model.schema import Planning
from mes_lab.planning import pick_line, smt_lots

P = Planning(safety_stock=1000, min_lot=480, max_lot=720)


def test_no_order_above_safety_stock():
    assert smt_lots(stock=900, open_supply=480, open_demand=300, planning=P, boards_per_panel=4) == []


def test_shortfall_split_into_lots_rounded_to_panels():
    # projected = 100 + 0 - 960 = -860 -> shortfall 1860 -> 720 + 720 + 420->480
    lots = smt_lots(stock=100, open_supply=0, open_demand=960, planning=P, boards_per_panel=4)
    assert lots == [720, 720, 480]
    assert all(lot % 40 == 0 for lot in lots)  # whole magazines of 10 panels


def test_small_shortfall_still_makes_a_minimum_lot():
    assert smt_lots(stock=990, open_supply=0, open_demand=0, planning=P, boards_per_panel=6) == [480]
    # 482 boards -> 13 magazines of 40 boards
    assert smt_lots(stock=518, open_supply=0, open_demand=0, planning=P, boards_per_panel=4) == [520]


def test_pick_line_prefers_same_material_within_tolerance():
    workload = {"SMT-01": 3600.0, "SMT-02": 2000.0, "SMT-03": 9000.0}
    last = {"SMT-01": "PCBA-A", "SMT-02": "PCBA-B", "SMT-03": "PCBA-A"}
    assert pick_line(workload, last, "PCBA-A") == "SMT-01"   # 1600 s worse, saves a changeover
    assert pick_line(workload, last, "PCBA-C") == "SMT-02"
    assert pick_line(workload, last, "PCBA-A", tolerance_s=600) == "SMT-02"
