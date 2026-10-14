"""HTTP client the plant (simulation) uses to talk to the MES."""

from __future__ import annotations

import httpx


class MesClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8070", token: str | None = None,
                 client: httpx.Client | None = None, timeout: float = 5.0):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        # trust_env=False: never route plant traffic through a system proxy
        self.http = client or httpx.Client(base_url=base_url, timeout=timeout, trust_env=False)
        self.http.headers.update(headers)

    def register_run(self, run_id: str, seed: int, speed: float | None,
                     initial_stock: dict[str, list[str]] | None = None,
                     lines: list[str] | None = None, knobs: dict | None = None) -> dict:
        r = self.http.post("/api/v1/sim/runs", json={"run_id": run_id, "seed": seed, "speed": speed,
                                                     "initial_stock": initial_stock or {},
                                                     "lines": lines, "knobs": knobs})
        r.raise_for_status()
        return r.json()

    def scenario(self) -> dict:
        """The plant the MES asks for: {"knobs": ... or None, "version": n}."""
        try:
            r = self.http.get("/api/v1/sim/scenario")
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError:
            return {"knobs": None, "version": None}

    def trace(self, path: str, body: dict) -> dict:
        """Check-in or repair report; raises when the MES does not answer properly."""
        r = self.http.post(f"/api/v1/trace/{path}", json=body, timeout=3.0)
        r.raise_for_status()
        return r.json()

    def next_job(self, run_id: str, line: str, plant_time: str) -> dict | None:
        r = self.http.post("/api/v1/dispatch/next-job",
                           json={"run_id": run_id, "line": line, "plant_time": plant_time})
        if r.status_code == 204:
            return None
        r.raise_for_status()
        return r.json()

    def ingest(self, payload: dict) -> dict:
        r = self.http.post("/api/v1/edge/ingest", json=payload)
        r.raise_for_status()
        return r.json()


class MesSink:
    def __init__(self, client: MesClient):
        self.client = client

    def send(self, payload: dict) -> dict | None:
        return self.client.ingest(payload)
