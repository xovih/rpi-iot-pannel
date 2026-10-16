from __future__ import annotations

from typing import Any

import requests


class ApiError(RuntimeError):
    def __init__(self, status_code: int | None, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class ApiClient:
    def __init__(self, base_url: str, device_token: str) -> None:
        self.base_url = base_url
        self.session = requests.Session()
        self.session.headers.update({"X-Device-Token": device_token, "Accept": "application/json"})

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self.session.request(
                method, f"{self.base_url}{path}", timeout=(2, 8), **kwargs
            )
        except requests.RequestException as exc:
            raise ApiError(None, f"Backend {self.base_url} tidak dapat dijangkau: {exc}") from exc
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if not response.ok:
            message = payload.get("message") or f"Backend menjawab HTTP {response.status_code}."
            raise ApiError(response.status_code, message)
        if not payload.get("success", False):
            raise ApiError(response.status_code, payload.get("message", "Backend menolak permintaan panel."))
        return payload.get("data")

    def health(self) -> dict[str, Any]:
        return self.request("GET", "/health")

    def bootstrap(self) -> dict[str, Any]:
        return self.request("GET", "/api/iot/bootstrap")

    def queue(self, machine_code: str) -> list[dict[str, Any]]:
        return self.request("GET", f"/api/production/panel/machines/{machine_code}/queue")

    def add_queue(self, machine_code: str, peti_id: str) -> dict[str, Any]:
        return self.request("POST", "/api/production/panel/queue", json={"machineCode": machine_code, "petiId": peti_id})

    def resolve_operator(self, machine_code: str, npp: str) -> dict[str, Any]:
        return self.request(
            "POST",
            "/api/production/panel/operators/resolve",
            json={"machineCode": machine_code, "npp": npp},
        )

    def start(self, queue_id: str, operators: list[str]) -> dict[str, Any]:
        return self.request("POST", f"/api/production/panel/queue/{queue_id}/start", json={"operatorNpps": operators})

    def finish(self, queue_id: str, event_id: str, yield_qty: int, scrap_qty: int, reason: str | None) -> dict[str, Any]:
        body: dict[str, Any] = {"eventId": event_id, "yieldQty": yield_qty, "scrapQty": scrap_qty}
        if reason:
            body["reasonReject"] = reason
        return self.request("POST", f"/api/production/panel/queue/{queue_id}/finish", json=body)

    def delete_queue(self, queue_id: str) -> None:
        self.request("DELETE", f"/api/production/panel/queue/{queue_id}")

    def ingest(self, samples: list[dict[str, Any]]) -> dict[str, int]:
        return self.request("POST", "/api/iot/ingest", json={"samples": samples})

    def heartbeat(self) -> None:
        self.request("POST", "/api/master/gateways/heartbeat", json={})
