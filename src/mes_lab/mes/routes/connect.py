"""Connectivity: the Unified Namespace (MQTT) and the OPC UA server of the machines, as the MES sees them."""

from __future__ import annotations

import logging

from fastapi import Depends, FastAPI, HTTPException

from mes_lab.connect import opcua as connect_opcua

from ..context import Mes

log = logging.getLogger("mes_lab.mes")


def register(app: FastAPI, m: Mes) -> None:
    @app.get("/api/v1/uns", tags=["connectivity"])
    def uns_tree(prefix: str = "", user: str = Depends(m.viewer)) -> dict:
        """The Unified Namespace as the MES sees it (MQTT): every topic under `prefix` with its last payload."""
        if m.uns is None:
            return {"status": {"configured": False}, "rows": []}
        return {"status": m.uns.status(), "rows": m.uns.tree(prefix)}

    @app.get("/api/v1/opcua/browse", tags=["connectivity"])
    def opcua_browse(node: str | None = None, user: str = Depends(m.viewer)) -> dict:
        """The OPC UA server of the machines: the children of a node, the values of its variables."""
        if not m.opcua_url:
            raise HTTPException(503, "OPC UA is not configured (MES_OPCUA_URL)")
        try:
            return connect_opcua.browse(m.opcua_url, node)
        except LookupError as exc:
            raise HTTPException(404, str(exc)[:300]) from None
        except Exception as exc:  # noqa: BLE001 - the server of the machines is another process
            raise HTTPException(503, f"OPC UA server not reachable: {type(exc).__name__}: {exc}"[:300]) from None
