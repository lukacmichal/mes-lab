"""OPC UA: the machines of the plant as a server, the way a machine or a PLC exposes its data, and the MES's
browser of it.

Address space (namespace urn:acme:plant1, string node ids that read like the path):

    Objects/ACME/Plant1                     PlantTime, Speed, Paused, RunId
      SMT/SMT-01                            Order, Material, Planned, Released, Good, Wip, NoOrder
        SMT-01-PP1                          State, Reason, Since, Serial, Order, Material, Cycles, Good,
                                            Bad, LastCycleTime, LastResult
          Measurements/PASTE_VOLUME         the last process value (Double; the unit in the description)

All values are read-only. The server runs in its own thread with its own event loop; the simulation hands
it the changes once a wall second (`update`), the thread writes them into the address space.
"""

from __future__ import annotations

import asyncio
import logging
import math
import threading
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .shopfloor import units, views

if TYPE_CHECKING:
    from mes_lab.model.schema import PlantModel
    from mes_lab.sim.plant import PlantSim

log = logging.getLogger("mes_lab.opcua")
logging.getLogger("asyncua").setLevel(logging.ERROR)     # every browse of the MES would log its session timeout

NAMESPACE = "urn:acme:plant1"
PLANT_VARS = {"PlantTime": "DateTime", "Speed": "Double", "Paused": "Boolean", "RunId": "String"}
LINE_VARS = {"Order": "String", "Material": "String", "Planned": "Int32", "Released": "Int32", "Good": "Int32",
             "Wip": "Int32", "NoOrder": "Boolean"}
STATION_VARS = {"State": "String", "Reason": "String", "Since": "DateTime", "Serial": "String", "Order": "String",
                "Material": "String", "Cycles": "Int32", "Good": "Int32", "Bad": "Int32",
                "LastCycleTime": "Double", "LastResult": "String"}
DESCRIPTION = {
    "State": "RUNNING, MINOR_STOP, STARVED, BLOCKED, DOWN, CHANGEOVER, MAINTENANCE, NO_ORDER",
    "Since": "plant time the state began", "Serial": "the piece in the station",
    "Cycles": "cycles this run (a panel on SMT, a unit in assembly)", "Good": "good pieces this run (boards on SMT)",
    "Bad": "pieces found bad this run", "LastCycleTime": "seconds the last cycle took",
    "PlantTime": "the clock of the simulated plant", "Speed": "x real time", "Wip": "pieces on the line",
}


def _when(iso: str | None) -> datetime | None:
    return datetime.fromisoformat(iso) if iso else None


def node_values(model: PlantModel, v: dict[str, Any]) -> dict[str, Any]:
    """String node id (without the namespace) -> value."""
    out: dict[str, Any] = {}
    p = v["plant"]
    out.update({"Plant1.PlantTime": _when(p["plant_time"]), "Plant1.Speed": float(p["speed"] or 0.0),
                "Plant1.Paused": bool(p["paused"]), "Plant1.RunId": p["run_id"]})
    for code, line in v["lines"].items():
        base = f"Plant1.{model.lines[code].area}.{code}"
        out.update({f"{base}.Order": line["order"], f"{base}.Material": line["material"],
                    f"{base}.Planned": line["qty"] or 0, f"{base}.Released": line["released"] or 0,
                    f"{base}.Good": line["good"] or 0, f"{base}.Wip": line["wip"] or 0,
                    f"{base}.NoOrder": bool(line["no_order"])})
    for code, st in v["stations"].items():
        spec = model.stations[code]
        base = f"Plant1.{model.lines[spec.line].area}.{spec.line}.{code}"
        out.update({f"{base}.State": st["state"], f"{base}.Reason": st["reason"], f"{base}.Since": _when(st["since"]),
                    f"{base}.Serial": st["serial"], f"{base}.Order": st["order"], f"{base}.Material": st["material"],
                    f"{base}.Cycles": st["cycles"], f"{base}.Good": st["good"], f"{base}.Bad": st["bad"],
                    f"{base}.LastCycleTime": float(st["cycle_s"]) if st["cycle_s"] is not None else math.nan,
                    f"{base}.LastResult": st["result"]})
        for name, value in st["values"].items():
            out[f"{base}.Measurements.{name}"] = float(value)
    return out


class OpcUaServer:
    def __init__(self, endpoint: str, model: PlantModel):
        self.endpoint = endpoint
        self.model = model
        self.pending: dict[str, Any] = {}
        self.last: dict[str, Any] = {}
        self.lock = threading.Lock()
        self.ready = threading.Event()
        self.failed: str | None = None
        self.written = 0
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name="opcua", daemon=True)
        self.thread.start()
        self.ready.wait(30)

    # ------------------------------------------------------------------ the server thread
    def _run(self) -> None:
        try:
            asyncio.run(self._main())
        except Exception as exc:  # noqa: BLE001 - the plant runs on without OPC UA
            self.failed = str(exc)
            log.exception("OPC UA server stopped")
            self.ready.set()

    async def _main(self) -> None:
        from asyncua import Server, ua

        server = Server()
        await server.init()
        server.set_endpoint(self.endpoint)
        server.set_server_name("ACME Plant 1 - machines (MES lab)")
        server.set_security_policy([ua.SecurityPolicyType.NoSecurity])
        idx = await server.register_namespace(NAMESPACE)
        self.vtypes: dict[str, Any] = {}
        await self._build(server, idx, ua)
        async with server:
            self.ready.set()
            log.info("OPC UA server on %s", self.endpoint)
            while not self._stop.is_set():
                await asyncio.sleep(0.5)
                with self.lock:
                    batch, self.pending = self.pending, {}
                for name, value in batch.items():
                    vt = self.vtypes.get(name)
                    if vt is None:
                        continue
                    await server.write_attribute_value(ua.NodeId(name, idx), ua.DataValue(ua.Variant(value, vt)))
                self.written += len(batch)

    async def _build(self, server, idx: int, ua) -> None:
        def nid(name: str):
            return ua.NodeId(name, idx)

        async def variables(parent, prefix: str, spec: dict[str, str]) -> None:
            for name, vt in spec.items():
                vtype = getattr(ua.VariantType, vt)
                node = await parent.add_variable(nid(f"{prefix}.{name}"), ua.QualifiedName(name, idx),
                                                 ua.Variant(None if vt in ("String", "DateTime") else
                                                            (False if vt == "Boolean" else 0), vtype))
                if name in DESCRIPTION:
                    await node.write_attribute(ua.AttributeIds.Description,
                                               ua.DataValue(ua.Variant(ua.LocalizedText(DESCRIPTION[name]))))
                self.vtypes[f"{prefix}.{name}"] = vtype

        acme = await server.nodes.objects.add_folder(nid("ACME"), ua.QualifiedName("ACME", idx))
        plant = await acme.add_object(nid("Plant1"), ua.QualifiedName("Plant1", idx))
        await variables(plant, "Plant1", PLANT_VARS)
        unit_of = units(self.model)
        for area in self.model.areas:
            if not area.lines:
                continue
            a = await plant.add_object(nid(f"Plant1.{area.code}"), ua.QualifiedName(area.code, idx))
            for code in area.lines:
                line = self.model.lines[code]
                ln = await a.add_object(nid(f"Plant1.{area.code}.{code}"), ua.QualifiedName(code, idx))
                if line.is_job_line:
                    await variables(ln, f"Plant1.{area.code}.{code}", LINE_VARS)
                for st in line.stations:
                    prefix = f"Plant1.{area.code}.{code}.{st.code}"
                    so = await ln.add_object(nid(prefix), ua.QualifiedName(st.code, idx))
                    await variables(so, prefix, STATION_VARS)
                    meas = unit_of.get(st.type, {})
                    if meas:
                        folder = await so.add_folder(nid(f"{prefix}.Measurements"), ua.QualifiedName("Measurements", idx))
                        for name, unit in meas.items():
                            node = await folder.add_variable(nid(f"{prefix}.Measurements.{name}"),
                                                             ua.QualifiedName(name, idx), ua.Variant(math.nan, ua.VariantType.Double))
                            if unit:
                                await node.write_attribute(ua.AttributeIds.Description,
                                                           ua.DataValue(ua.Variant(ua.LocalizedText(unit))))
                            self.vtypes[f"{prefix}.Measurements.{name}"] = ua.VariantType.Double

    # ------------------------------------------------------------------ from the simulation
    def update(self, plant: PlantSim) -> int:
        """Hand the changed values to the server thread (called from the simulation, once a wall second)."""
        if self.failed:
            return 0
        values = node_values(self.model, views(plant))
        changed = {k: val for k, val in values.items()
                   if k not in self.last or not _same(self.last[k], val)}
        self.last.update(changed)
        with self.lock:
            self.pending.update(changed)
        return len(changed)

    def stop(self) -> None:
        self._stop.set()
        self.thread.join(5)


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
        return True
    return a == b


# ---------------------------------------------------------------------- the MES's browser
async def _browse(url: str, node_id: str | None) -> dict:
    from asyncua import Client, ua

    async with Client(url, timeout=4) as client:
        idx = await client.get_namespace_index(NAMESPACE)
        node = client.get_node(ua.NodeId(node_id, idx)) if node_id else client.get_node(ua.NodeId("ACME", idx))
        try:
            await node.read_browse_name()
        except ua.UaStatusCodeError as exc:
            raise LookupError(f"no node {node_id} ({exc})") from None
        children = await node.get_children()
        rows = []
        for child in children:
            klass = await child.read_node_class()
            name = (await child.read_browse_name()).Name
            row: dict[str, Any] = {"node_id": child.nodeid.Identifier, "ns": idx, "name": name,
                                   "class": klass.name}
            if klass == ua.NodeClass.Variable:
                dv = await child.read_data_value()
                value = dv.Value.Value if dv.Value is not None else None
                row["type"] = dv.Value.VariantType.name if dv.Value is not None else None
                row["value"] = value.isoformat() if isinstance(value, datetime) else (
                    None if isinstance(value, float) and math.isnan(value) else value)
                desc = await child.read_description()
                row["description"] = desc.Text if desc and desc.Text and desc.Text != name else None
            rows.append(row)
        rows.sort(key=lambda r: (r["class"] != "Object", r["name"]))
        return {"endpoint": url, "namespace": NAMESPACE, "ns": idx, "node_id": node_id or "ACME", "children": rows}


def browse(url: str, node_id: str | None = None) -> dict:
    """The children of a node (the ACME folder by default) with the values of the variables."""
    return asyncio.run(_browse(url, node_id))
