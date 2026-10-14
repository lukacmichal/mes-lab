"""Nobody walks through anybody: the traffic check (sim/traffic.py) and what it does in a running plant."""

import math

import simpy

from mes_lab.sim.traffic import CLEAR_PP, PERSON, ROBOT, Plan, Traffic, touch


def _traffic(now=0.0):
    env = simpy.Environment(initial_time=now)
    return env, Traffic(env)


def test_two_people_who_meet_pass_side_by_side_on_their_own_lanes():
    env, t = _traffic()
    # walking east on the south lane, walking west on the north lane of the same walkway (12 apart)
    wait_a, _ = t.plan("a", PERSON, [(0, 106), (200, 106)], 25)
    wait_b, _ = t.plan("b", PERSON, [(200, 94), (0, 94)], 25)
    assert wait_a == 0 and wait_b == 0
    # on one line they would walk into each other: the second one waits... until the first is through
    env2, t2 = _traffic()
    t2.plan("a", PERSON, [(0, 100), (200, 100)], 25)
    wait, _ = t2.plan("b", PERSON, [(200, 100), (0, 100)], 25)
    assert wait is None or wait >= 200 / 25 - 1


def test_at_a_crossing_the_later_one_waits_until_the_first_has_passed():
    env, t = _traffic()
    t.plan("robot", ROBOT, [(0, 50), (300, 50)], 30)                  # crosses x=150 at t=5
    wait, plan = t.plan("person", PERSON, [(150, -60), (150, 100)], 25)  # would be at y=50 at t=4.4
    assert wait is not None and wait > 0
    # sampled: they never touch
    for i in range(0, 400):
        s = i * 0.05
        rx = min(300.0, 30 * s)
        f = min(1.0, max(0.0, (s - plan.t0) / (plan.t1 - plan.t0)))
        py = -60 + 160 * f
        gap_x, gap_y = abs(rx - 150), abs(50 - py)
        assert gap_x >= 16 + 5.5 or gap_y >= 10.5 + 5.5, (s, rx, py)


def test_a_standing_robot_blocks_a_walk_through_it_but_not_one_beside_it():
    env, t = _traffic()
    t.stand("r", ROBOT, 100, 50, face=0)
    wait, _ = t.plan("p", PERSON, [(0, 50), (200, 50)], 25)
    assert wait is None                                                # ask again later
    wait, _ = t.plan("q", PERSON, [(0, 80), (200, 80)], 25)            # 30 below the robot's middle: free
    assert wait == 0


def test_robots_are_rectangles_along_their_way():
    a = (0, 10, 0, 0, 0, 0, 0.0)                                       # a robot standing along x
    beside = (0, 10, 0, 22.5, 0, 22.5, 0.0)                             # another one parallel, 22.5 below
    behind = (0, 10, 30, 0, 30, 0, 0.0)                                 # another one 30 behind it, in line
    assert not touch(ROBOT, a, ROBOT, beside)
    assert touch(ROBOT, a, ROBOT, behind)
    assert touch(PERSON, (0, 1, 0, 0, 0, 0, 0), PERSON, (0, 1, CLEAR_PP - 1, 0, CLEAR_PP - 1, 0, 0))


def test_a_plan_that_starts_later_is_where_it_starts_until_then():
    p = Plan.make(PERSON, [(0, 0), (10, 0)], 5.0, 10.0)
    pieces = list(p.pieces(0.0, 10.0))
    assert pieces[0][:6] == (0.0, 5.0, 0, 0, 0, 0)                     # standing at the start
    assert math.isclose(pieces[1][1], 6.0)                             # then one second of walking


def test_in_the_running_plant_people_and_robots_keep_apart(model):
    """Two hours around a shift change: the people and the robots, looked at every second, hardly ever
    touch (a brush past now and then is allowed where somebody waited very long; see scripts/overlaps.py)."""
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("overlaps", Path(__file__).parent.parent / "scripts" / "overlaps.py")
    overlaps = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(overlaps)
    hits, where, plant = overlaps.run(hours=1.5, seed=4, start_h=7.0, step=1.0, quiet=True)
    total = sum(hits.values())
    robots = sum(n for (kinds, *_), n in hits.items() if "R" in kinds)
    assert robots <= 3, dict(hits)
    assert total <= 25, dict(hits)
    assert plant.traffic.waits > 20                                    # people and robots did let each other pass
