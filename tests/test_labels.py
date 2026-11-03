"""Labels: the bar codes, the templates, printing at packing and the settings page."""

import re
import uuid
from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from mes_lab.labels import barcode, unknown
from mes_lab.mes import models as db
from mes_lab.mes.labels import Labels, sscc_text
from mes_lab.sim.mes_client import MesClient

T = datetime.fromisoformat("2026-01-05T08:00:00+01:00")
BAR = re.compile(r'<rect x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)"/>')


def _image(modules: list[int], px: int = 3):
    np = pytest.importorskip("numpy")
    row, bar = [255] * 30, True
    for w in modules:
        row += [0 if bar else 255] * (w * px)
        bar = not bar
    row += [255] * 30
    return np.array([row] * 40, dtype=np.uint8)


def _read(img) -> list[str]:
    zxing = pytest.importorskip("zxingcpp")
    return [r.text for r in zxing.read_barcodes(img)]


def read_svg(svg: str) -> set[str]:
    """Every bar code of a label, read back by a scanner (zxing) from the bars the SVG draws."""
    np = pytest.importorskip("numpy")
    groups: dict[tuple[str, str], list[tuple[float, float]]] = {}
    for x, y, w, h in BAR.findall(svg):
        groups.setdefault((y, h), []).append((float(x), float(w)))
    found = set()
    for bars in groups.values():
        scale = 12                                       # px per mm
        left = min(x for x, _ in bars) - 6
        right = max(x + w for x, w in bars) + 6
        row = np.full(int((right - left) * scale) + 1, 255, dtype=np.uint8)
        for x, w in bars:
            row[int(round((x - left) * scale)):int(round((x + w - left) * scale))] = 0
        found.update(_read(np.tile(row, (40, 1))))
    return found


def test_bar_codes_read_back():
    for data in ["PKW-55120-BCM", "Q24", "SK000001234", "HWO0000012", "VS-20931"]:
        assert _read(_image(barcode.code39(data))) == [data]
    assert _read(_image(barcode.code128("00" + "385812340000012344", gs1=True))) == ["(00)385812340000012344"]
    assert _read(_image(barcode.code128("WO0000012"))) == ["WO0000012"]
    with pytest.raises(ValueError):
        barcode.code39("a,b")


def test_templates_know_their_placeholders(model):
    assert unknown("{customer_part} / {qty}") == [] and unknown("{nonsense}") == ["nonsense"]
    for t in model.labels.templates.values():
        w, h = t.size_mm
        assert all(f.x + f.w <= w and f.y + f.h <= h for f in t.fields.values())
    assert sscc_text("385812340000012344") == "(00) 3 8581234 000001234 4"


def _pack_a_box(client, app):
    run_id = str(uuid.uuid4())
    mes = MesClient(client=client)
    mes.register_run(run_id, 5, None, {"PCBA-BMS-12S": [f"SX{i:02d}" for i in range(12)]}, plant_start=T.isoformat())
    job = mes.next_job(run_id, "FA-04", T.isoformat())
    steps = list(app.state.model.materials["BMS-12S"].routing[0].steps)
    station_of = {c.type: c.stations[0].code for c in app.state.model.lines["FA-04"].cells}

    def check_in(station, serial=None, consume=None, records=()):
        return client.post("/api/v1/trace/check-in", json={
            "run_id": run_id, "station": station, "plant_time": T.isoformat(), "serial": serial, "consume": consume,
            "job_id": job["id"], "records": list(records)}).json()

    def rec(serial, station, step):
        return {"serial": serial, "station": station, "step": step, "job_id": job["id"], "start": T.isoformat(),
                "end": T.isoformat(), "result": "PASS", "defects": []}
    for i in range(12):
        unit = check_in("FA-04-LDR", consume=f"SX{i:02d}")["serial"]
        packed = check_in("PACK-02-PCK", serial=unit, records=[rec(unit, station_of[s], s) for s in steps])
    assert packed["full"]
    return run_id, packed["container"], job, rec


def test_a_full_box_gets_its_label_and_a_reprint_prints_the_same(app, client):
    run_id, klt, job, rec = _pack_a_box(client, app)
    assert client.get(f"/api/v1/labels/{klt}").status_code == 404             # not past the printer yet
    payload = {"run_id": run_id, "plant_time": T.isoformat(), "records": [rec(klt, "PACK-02-LBL", "BOX_LABEL")]}
    client.post("/api/v1/edge/ingest", json=payload)
    client.post("/api/v1/edge/ingest", json=payload)                            # reported twice: one label
    label = client.get(f"/api/v1/labels/{klt}").json()
    d = label["data"]
    assert label["template"] == "KLT" and label["station"] == "PACK-02-LBL" and label["reprints"] == 0
    assert d["qty"] == 12 and d["customer_part"] == "VLT-BMS12S-03" and d["package_id"] == klt
    assert d["batch"] == job["order_no"] and d["net_kg"] == "1.4" and d["gross_kg"] == "4.1"
    svg = client.get(f"/api/v1/labels/{klt}/svg").text
    assert read_svg(svg) == {"PVLT-BMS12S-03", "Q12", "VVL0177", f"S{klt}", f"H{job['order_no']}"}
    with Session(app.state.engine) as s:
        assert s.scalar(select(db.Label.id).where(db.Label.serial == klt)) is not None
    assert client.post(f"/api/v1/labels/{klt}/reprint", json={"reason": "torn"}).status_code == 200
    again = client.get(f"/api/v1/labels/{klt}").json()
    assert again["reprints"] == 1 and again["prints"][1]["reason"] == "torn"
    assert client.get(f"/api/v1/labels/{klt}/svg").text == svg
    unit = client.get(f"/api/v1/trace/units/{klt}").json()
    assert unit["label"]["serial"] == klt

    # the pallet: its label carries the SSCC as GS1-128
    pallet = client.post("/api/v1/trace/check-in", json={
        "run_id": run_id, "station": "PACK-02-PAL", "plant_time": T.isoformat(), "serial": klt}).json()["container"]
    with Session(app.state.engine) as s:
        unit = s.scalar(select(db.Unit).where(db.Unit.serial == pallet))
        printed = Labels(app.state.model).print(s, unit, T, "PACK-02-PAL")
        assert printed.data["qty"] == 12 and printed.data["boxes"] == 1 and printed.data["sscc"] == pallet
        codes = read_svg(Labels.svg(printed))
        s.rollback()
    assert f"(00){pallet}" in codes and "Q12" in codes


def test_the_label_templates_are_changed_on_the_settings_page(client):
    before = client.get("/api/v1/settings/labels/KLT/preview?state=draft&material=BCM-200").text
    assert "Receiver" in before and "KW-55120-BCM" in before
    r = client.post("/api/v1/settings/draft", json={"path": "labels/templates/KLT/fields/receiver/title", "value": "Ship to"})
    assert r.status_code == 200, r.text
    assert "Ship to" in client.get("/api/v1/settings/labels/KLT/preview?state=draft").text
    assert "Ship to" not in client.get("/api/v1/settings/labels/KLT/preview?state=force").text
    bad = client.post("/api/v1/settings/draft", json={"path": "labels/templates/KLT/fields/dock/value", "value": "{nonsense}"})
    assert bad.status_code >= 400 and "nonsense" in bad.text
    off = client.post("/api/v1/settings/draft", json={"path": "labels/templates/KLT/fields/dock/x", "value": 200})
    assert off.status_code >= 400 and "does not fit" in off.text
    page = client.get("/api/v1/settings").json()
    assert {t["code"] for t in page["labels"]["templates"]} == {"KLT", "PALLET"}
    assert client.get("/settings").status_code == 200
