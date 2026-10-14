"""Conveyors between the areas of the plant.

Between two stations of a line a piece moves instantly (the conveyor places in
between are the buffer). Between *areas* it rides a conveyor that is as long as the
floor plan says (see mes_lab.layout), so it needs real time:

  goods-in -> SMT line (magazines of bare panels)      supply:<SMT>
  SMT unloader -> SMT loader (second side)             ret:<SMT>
  SMT depaneler -> supermarket                         sm:<SMT>
  supermarket -> final assembly loader                 feed:<FA>
  final inspection -> packing                          pack:<FA>
  palletizer -> finished goods warehouse               wh:<PACK>
  SPI -> washer -> loader                              wash:<SMT>

Pieces for repair and quarantine do not ride conveyors: robots carry them (see amr.py).

A ride is registered while it lasts, so the live view can draw the piece exactly
where it is, and kept for a while after it ended (`ended`): the page shows the plant a
moment late and draws a ride that began and ended between two of its looks as well.
"""

from __future__ import annotations

import itertools
import logging
from collections import deque
from collections.abc import Callable
from typing import TYPE_CHECKING

from .core import Item

if TYPE_CHECKING:
    from .plant import PlantSim

log = logging.getLogger("mes_lab.logistics")

FALLBACK_RIDE_S = 30.0


class Ride:
    __slots__ = ("id", "route", "item", "t0", "dur")

    def __init__(self, ride_id: int, route: str, item: Item, t0: float, dur: float):
        self.id = ride_id
        self.route = route
        self.item = item
        self.t0 = t0
        self.dur = dur


class Logistics:
    def __init__(self, plant: PlantSim):
        self.plant = plant
        self.env = plant.env
        self.rides: dict[int, Ride] = {}
        self.ended: deque[Ride] = deque()                  # the rides of the last moments, oldest first
        self._ids = itertools.count(1)
        self._warned: set[str] = set()

    def duration(self, route: str) -> float:
        known = self.plant.layout.routes.get(route)
        if known is None:
            if route not in self._warned:
                self._warned.add(route)
                log.warning("no conveyor %s in the floor plan, using %.0f s", route, FALLBACK_RIDE_S)
            return FALLBACK_RIDE_S
        return known.duration

    def send(self, route: str, item: Item, arrive: Callable[[], None] | None = None,
             dur: float | None = None) -> Ride:
        """Put an item on a conveyor; `arrive` runs when it reaches the end."""
        ride = Ride(next(self._ids), route, item, self.env.now,
                    self.duration(route) if dur is None else dur)
        self.rides[ride.id] = ride
        self.env.process(self._ride(ride, arrive))
        return ride

    def _ride(self, ride: Ride, arrive: Callable[[], None] | None):
        yield self.env.timeout(ride.dur)
        del self.rides[ride.id]
        ride.item.at = self.env.now
        self.ended.append(ride)
        if arrive is not None:
            arrive()

    def recent(self, keep_s: float) -> list[Ride]:
        """The rides that ended in the last `keep_s` seconds."""
        now = self.env.now
        while self.ended and self.ended[0].t0 + self.ended[0].dur < now - keep_s:
            self.ended.popleft()
        return list(self.ended)
