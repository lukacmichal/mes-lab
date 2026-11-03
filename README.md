# MES lab

A virtual automotive electronics plant and the manufacturing execution system
(MES) that runs it. The plant is a discrete-event simulation; the MES is a
FastAPI + PostgreSQL web application. Everything is fictional:
**ACME Automotive Electronics, Plant 1** builds body control modules, LED drivers,
battery management boards and park distance control units for invented customers.

**Live:** https://152.70.49.202.sslip.io — anybody can look around; changing anything needs a
signed-in user.

![The plant seen from above, live](docs/floor.jpg)

## What runs where

```
 +---------------------------- plant (SimPy) ----------------------------+
 |  SMT-01..05      supermarket     FA-01..05      PACK-01..02  warehouse |
 |  10 stations ->  (PCB assys)  -> 6-13 stations -> KLT/pallet ->        |
 |        \__ wash (SPI) __/          \__ repair center RPR-01 __/        |
 |                                                                        |
 |  edge gateway: condenses machine signals (state time per hour, counts, |
 |  defects, downtime events, job progress, live snapshot) and publishes  |
 |  where every piece is right now (floor snapshot, memory only)          |
 +------------------------------+-----------------------------------------+
                                |  REST, once per wall-clock second
                                v
 +------------------------------+-----------------------------------------+
 |  MES (FastAPI)               |                                         |
 |  /api/v1/edge/ingest         upserts into PostgreSQL                   |
 |  /api/v1/dispatch/next-job   lines ask for work -> demo planner        |
 |  /api/v1/sim/runs            a new run = empty shop floor              |
 |  web: plant floor (live, from above), overview, lines, andon, people,  |
 |  material, KPI, orders, ERP, trace, quality, SPC, holds, training,     |
 |  settings, guide (EN/SK)                                               |
 +------------------------------------------------------------------------+
```

* **Plant model** (`config/*.yaml`): areas, lines, stations, station types with
  their behaviour (MTBF/MTTR, minor stops, changeovers, defects and detection
  probabilities, false calls), materials and routings with ideal cycle times,
  shifts, defect codes and downtime reasons. The equipment and material model
  follows ISA-95.
* **Simulation** (`src/mes_lab/sim`): every station is a SimPy process with the
  states RUNNING, STARVED, BLOCKED, DOWN, MINOR_STOP, CHANGEOVER, MAINTENANCE and
  NO_ORDER. Double-sided boards run bottom side first, then top side. Misprinted
  panels are washed and printed again, boards found bad by AOI get a bad mark and
  are repaired after depaneling, units failing in final assembly go to the repair
  center and come back to the station that rejected them, carried by robots (below). Conveyor ovens hold
  several items at a time. Time runs 1x to 600x faster than the wall clock, with a
  fixed random seed.
* **MES** (`src/mes_lab/mes`): master data synced from the YAML, work orders and
  jobs (one job = one routing operation on one line), dispatching, the condensed
  production history, and the web pages. The final assembly orders come from the
  fake ERP (below); the MES keeps the SMT supermarket above its safety stock itself
  (`src/mes_lab/planning.py`).
* **Connectivity** (`src/mes_lab/connect`): the simulated machines are an OPC UA server (asyncua; ACME >
  Plant1 > area > line > station, typed variables with units), and the edge gateway publishes every change
  into a Unified Namespace in MQTT (`acme/plant1/<area>/<line>/<station>/state|piece|counts|cycle|values/...`,
  retained, with a last will). The MES adds the queue of every line and mirrors the tree; its Connectivity
  page shows the UNS and browses the OPC UA server live. Mosquitto on the server, amqtt locally.
* **Fake ERP** (`src/mes_lab/erp`, `config/erp.yaml`): customer call-offs (VDA 4905 /
  DELFOR, firm days and a forecast), hourly MRP into production orders, and the books
  (confirmations with goods receipts, goods issues, stock counts). It never reads the
  MES's tables: the two exchange messages (production order, order received,
  confirmation, DESADV, stock count) with at-least-once delivery, retries through the
  ERP's nightly maintenance, lost answers caught by idempotency, and an error queue for
  refused messages (`/erp`).
* **Traceability** (`src/mes_lab/mes/trace.py`): interlocked stations check every
  piece in before working on it. The MES refuses scrapped pieces, pieces on hold,
  pieces with open defects and pieces that skipped a routing step, and it issues
  the serial numbers (panel and boards at the PCB laser, the product at the final
  assembly loader, KLT boxes and SSCC pallet numbers at packing). Every operation is
  checked out with its result, defects and measurements. Genealogy links
  panel -> board -> unit -> KLT -> pallet; repairs return a unit to the station that
  found its defect; holds (serial, work order, or station + time window, plus
  everything made from it) stop pieces at their next check-in and park them in
  quarantine until they are released or scrapped.
* **SMT material** (`config/smt_material.yaml`, `sim/material.py`, `sim/receiving.py`, `mes/material.py`,
  `/material`): every placement machine has a feeder for each part of its job. Before a reel runs out, the
  operator brings the next one from the rack at the line, scans it and splices it; the MES refuses a wrong
  part, a part that is not in the program and a moisture-sensitive reel past its floor life (MSL; such
  reels are baked). Solder paste has a thaw time and an open time. Reels come by truck from three fictional
  suppliers, are scanned in at goods-in against the delivery note, stored first in, first out and brought
  to the lines by a material handler (kanban cards, a round every half hour). The MES knows which reel was
  on which feeder while a board was placed, so a supplier lot leads to its boards, and a hold on the lot
  takes its reels off the machines and out of the racks and the store.
* **Hour by hour and andon** (`mes/hourly.py`, `/andon`): the MES plans every hour of a line from its work
  orders (ideal cycle of the bottleneck x 0.85) and a board above the line shows the plan against what was
  made. A breakdown calls the foreman after 15 minutes and the production manager after an hour, and ten
  minutes into each shift the foreman and the technicians meet for five minutes at the andon board.
* **SPC and master samples** (`mes/spc.py`, `/spc`): process values drift slowly in the simulation (screw
  torque, press force, coating weight, paste volume, oven temperature). The MES charts hourly subgroups of
  five against control limits and checks run rules; an alarm sends a technician to adjust the machine, and
  a point beyond the limits (or a value out of tolerance) puts what the station made since the last good
  subgroup on hold. In-circuit and end-of-line testers check their master sample at the start of every
  shift; until that check has passed, the MES refuses production units at the tester.
* **People: qualifications and absences** (`/people`, `mes/qualifications.py`): a qualification matrix per
  shift (SMT, final assembly, inspection, packing, repair; the inspection and repair certificates expire),
  sickness and holidays covered by relief people at the start of the shift, and a check-in refuses an
  operator without a valid qualification. A signed-in user grants, renews or takes away a qualification
  with a reason. Taking one away counts from the next shift, the MES stays the master of these decisions,
  and an operator who lost the qualification of their own seat goes to retraining.
* **Labels** (`config/labels.yaml`, `src/mes_lab/labels`): the KLT box label after VDA 4902 (Code 39 with
  data identifiers) and the pallet label with the SSCC as GS1-128. A template is a set of fields in
  millimetres; a label is printed when a full box or a closed pallet passes the printer, stored with its
  values, shown in Trace and printed again from there with a reason. Templates are changed in Settings;
  the tests read the barcodes back with a barcode reader library.
* **Training scenarios** (`/training`, `src/mes_lab/training.py`, `mes/training.py`): exercises in the live
  plant: a bad supplier lot on the feeders, a storm of false calls at an AOI, a reflow oven failure and a
  customer complaint about one shipped unit. The trainee looks for the pieces at risk and puts holds; at
  the end the MES compares the holds with what really happened (the genealogy of the lot, what the AOI let
  through, what was in the oven, what the screwdriver did in the suspect window) and grades every piece:
  contained, caught by a tester, escaped or still open.
* **Plant floor** (`/`, `src/mes_lab/layout.py`, `sim/floor.py`, `web/static/floor-*.js`):
  the plant seen from above, live. The floor plan is computed from the plant model
  and serves two readers: the page draws machines, belts and warehouses from it, and
  the simulation takes the length of every belt from it, so a piece that rides from an
  SMT line to the supermarket needs as long as the picture shows (goods-in -> SMT,
  SMT -> supermarket -> final assembly -> packing -> warehouse, washing, the return belt of
  the second board side underneath the machines). Where a walkway or a robot lane crosses a
  belt, the belt goes over it on a bridge (tested: no path crosses a belt anywhere else). The simulation
  publishes once a second where every piece is (in a machine, in the queue in front of
  it, on a belt), which phase every machine is in and how far its cycle has got. The
  page follows each piece by its id and draws only that: a tool moves while a piece is
  really inside, and what a piece looks like comes from the stations it has passed and
  from its product. The same view is on every line page, and as a close-up in the
  pop-up of a station. Pieces, KLT boxes and pallets are drawn in their real
  proportions (`config/products.yaml`), a piece shows its serial number and a box or
  pallet lists what is inside (the serial browser reads the trace API).
* **People and shipping** (`config/crew.yaml`, `sim/crew.py`, `sim/shipping.py`,
  `layout_crew.py`, `web/static/floor-people.js`): operators sit in seats (machine or team
  desk of a line), maintenance and IT technicians come for breakdowns (`config/codes.yaml`
  says which reasons are for IT), the foreman walks his rounds, logistics workers load the
  trucks. Everyone walks along the aisles and through the doors of the plan at walking speed (around the furniture with room for the shoulders, never through walls; the snapshot carries the last walks, so the page draws the real way between two snapshots) and has a desk of their own, chair pushed in, monitor, keyboard and mouse, where they face the screen; IT takes a laptop to a call, maintenance fetches measuring instruments when the fault asks for them; three shifts, a
  hand-over at the seat (the next person waits beside it, the old one walks out through a turnstile), a half hour lunch and ten minutes of coffee each, covered by a relief person who waits at a place of their own (and now and then a trip to the toilet: the men's or the women's, into a cabin whose door shuts, then the hands are washed); everybody going in passes an ESD gate (one at a time, the queue moves up) and the technicians put on an ESD coat, hand over to their successor around the end of the shift and leave the coat in the locker; logistics workers idle for five minutes sit down at the table in their corner (cards with a colleague, a book alone); a sign shows the head count per shift. People are men and women (`crew.women`, female surnames in their Slovak form) and look the same on the plan and on the badge photo of their panel.
  Finished pallets wait in the warehouse until the truck of their customer comes (timetable
  per customer); it takes the oldest ones and the delivery note (ASN) reaches the MES,
  which marks pallets, boxes and units `SHIPPED`.
* **Traffic** (`sim/traffic.py`, `layout_crew.py`): nobody walks through anybody. Every aisle has a
  walkway with two lanes (people keep to the right) and beyond it a robot lane; people stand beside
  the walkways (operator, technician and the next person each have their own place at a machine).
  Every walk and every drive is checked against everybody else's plan before it starts (people are
  circles, robots 32 x 21 rectangles along their way); the later one waits where they are until the
  crossing, the door or the turnstile is free. `scripts/overlaps.py` counts what still touches.
* **Robots (AMR)** (`sim/amr.py`, `sim/supply.py`, `config/crew.yaml` `amr:`, `web/static/floor-robots.js`): nothing
  flies across the plant. A rejected piece goes into the red NOK tote at the exit of its cell; a fleet
  manager sends the nearest free autonomous mobile robot, which drives along its lane in the
  aisle, slides sideways into the gap beside the tote and brings it to the pass-through rack in the wall of the repair
  centre. The robots also run the line-side material as a two-bin kanban: bins with the parts of a unit
  (`kit` in `products.yaml`) to the rack at the loader of every final assembly line and the empty bins
  back, stacks of empty KLT boxes to packing; with no full bin left the loader stops. Battery: drains
  while driving (more with a load) and a little while standing, charges on the charger. The repair person of a free bench fetches the piece from the rack, repairs it and carries
  it to the outbound rack (or the scrap bin); a robot takes it back to the cell the MES names
  (boards to the supermarket). Pieces the MES refuses go to the quarantine cage and back the same
  way. Robots have a battery, chargers in a depot and one dock per stop; the page draws every
  piece in the tote, on the robot, in the rack or in the hands of the repair person.
* **Calls from the MES** (`POST /api/v1/crew/calls`, "Call a person" on the Floor page): like the
  andon button of a station, a signed-in user calls somebody of maintenance, IT, the foreman or
  logistics to a machine; the call reaches the plant with the answer to its next report, the
  nearest free person walks there, stays and walks back, and the page follows the call.
* **Pause and a jump in time** (`mes/floor.py` `SimControl`, `sim/runner.py` `live_step`): a signed-in
  user stops the whole plant (it ends by itself after half an hour) or sends it ahead to a later plant
  time as fast as the MES keeps up, then paused or running on.
* **KPI** (`/kpi`, `src/mes_lab/kpi.py`, `mes/kpi_views.py`): OEE (availability x performance x quality
  at the constraint of each line), FPY and RTY, automation degree, productivity per person-hour,
  MTBF/MTTR, scrap and repairs, robot utilization, the capacity of the areas in lines, and the cost of
  a good unit with the margin (`config/costs.yaml`), over this shift, 8 h, 24 h or 7 days.
* **Tuning game** (`/tuning`, `src/mes_lab/tuning.py`, `mes/tuning_views.py`): change the plant (SMT
  lines, robots, technicians, relief people, bins of parts, buffers), run it offline in a worker
  process with a fixed seed and compare its margin per hour with the plant as it is; the best
  scenarios stay on a board. A signed-in user can put a scenario into the live plant
  (`mes/scenario.py`, `POST /api/v1/plant/scenario`): the plant learns it with the answer to its next
  report, starts a new run built with those knobs and tells the MES which lines it has; the work of the
  missing lines moves to the others, the planner and the pages follow, and every page says so.
* **Settings and the change log** (`/settings`, `src/mes_lab/settings.py`, `mes/settings_store.py`,
  `mes/settings_views.py`): the master data on the web: products and customers, the bill of materials
  of every board, routings with their ideal cycles, line and station names, planned maintenance and spec
  limits, defect and downtime codes, shifts and breaks, users and roles (administrator, process engineer,
  quality, shift supervisor). The YAML stays the plant as built; a change is an override (a path by codes,
  `materials/PCBA-BCM-200/routing/SMT_BOT/steps/PNP_CHIP`) that goes into a draft, is checked against the
  whole plant at once and is released with a reason as a numbered revision after a trial hour of the
  plant offline, in force now or from the next shift start (plant time). The MES rebuilds its own plant
  model and the master data tables; changes the machines need make the plant start a new run built with
  the same overrides (`GET /api/v1/sim/scenario`). Released revisions never change; the log shows every
  change with the value before and after, who, when and why.
* **Live view at any speed** (`web/static/floor-core.js`): the picture runs a second behind the plant on
  purpose, so every walk, drive and ride (`trh`, `at` in the snapshot) is known in full before it is
  drawn: at 60x people still go along the walkways, not straight through the walls.
* **Explanations**: a guide page (`/guide`) and a help box on every page, in English
  and Slovak, explain the plant and the screens for people who never saw a factory.

## Running it locally (Windows)

```
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\python -m mes_lab.dev --speed 60
```

Then open http://127.0.0.1:8070. `start-local.cmd` does the last step.
Without `MES_DATABASE_URL`, development uses a private PostgreSQL from the
`pgserver` package with its data in `var/pgdata` (created on first start).
The simulation speed can be changed in the page header.

Other entry points:

| Command | What it does |
|---|---|
| `python -m mes_lab.sim offline --hours 168` | one plant week without the MES, as fast as possible, prints a report |
| `python -m mes_lab.mes` | only the MES |
| `python -m mes_lab.mes user NAME` | create a web user (prints a generated password) |
| `python -m mes_lab.sim run --speed 60` | only the plant, against a running MES |
| `python -m pytest` | tests (model, floor plan, simulation, live snapshot, MES API, simulation driving the MES; the browser code runs in Node when it is installed) |

Environment: `MES_DATABASE_URL` (postgresql+psycopg://...), `MES_EDGE_TOKEN`
(required bearer token on the shop-floor API when set), `MES_LOGIN` (`actions`: anybody
may look, changes need a signed-in user; `all`: everything needs one; `off`: development),
`MES_SECRET_KEY`, `MES_COOKIE_SECURE=1`, `MES_RETENTION_DAYS` (unit-level history
kept for that many plant days), `MES_URL` for the plant, `MES_MQTT_URL`,
`MES_OPCUA_ENDPOINT` (the plant's OPC UA server) and `MES_OPCUA_URL` (where the MES
reads it); without the last three, MQTT and OPC UA stay off.

The schema is managed with Alembic; the MES migrates on start. After changing
`models.py` run `python scripts/new_migration.py "what changed"` and review the file.

## Deployment

`scripts/deploy.sh` ships the committed HEAD to an Ubuntu 24.04 VM: PostgreSQL,
Caddy (HTTPS), two systemd services (`mes-lab-mes`, `mes-lab-sim`) and a host
firewall opening 80/443 (`deploy/`). Secrets are generated on the server in
`/etc/mes-lab/mes.env`. Users: `python -m mes_lab.mes user NAME`.

## Layout

```
config/                   plant model (YAML)
src/mes_lab/model         loading and validating the plant model
src/mes_lab/layout.py     floor plan: machine and belt coordinates, shared by page and simulation
src/mes_lab/sim           simulation: stations, lines, people, robots, material, edge gateway
src/mes_lab/mes           MES: schema, ingest, dispatching, traceability, read models, web
src/mes_lab/erp           the fake ERP and the messages between it and the MES
src/mes_lab/connect       OPC UA server of the machines, MQTT unified namespace
src/mes_lab/labels        label templates and barcodes
src/mes_lab/planning.py   planning rules shared by the MES and offline runs
src/mes_lab/kpi.py        KPIs, shared by the MES and offline runs
tests/
```

## Author

Designed and built by Michal Lukáč ([github.com/lukacmichal](https://github.com/lukacmichal)).
The plant, its products and its customers are fictional.

## Roadmap

1. Plant model, simulation core, database schema, minimal web - **done**
2. Check-in/check-out at stations, route enforcement, serial numbers and
   genealogy, search, quality and repair, holds; first deployment - **done**
3. Settings web (products, BOMs, routings, stations, reasons, shifts, users) with change log - **done** (0.15.0)
4. Material (reels, MSL, feeder setup, solder paste), shipping (ASN), fake ERP, label templates
   (VDA 4902 box label, GS1-128 pallet label) - **done** (0.18.0)
5. OPC UA, MQTT / unified namespace, KPIs (OEE, FPY, MTBF/MTTR) - **done** (KPIs 0.6.0, OPC UA and UNS 0.19.0)
6. Training scenarios (bad supplier lot, AOI false call storm, reflow oven failure, customer complaint;
   graded containment) - **done** (0.21.0); a read-only AI assistant still open
