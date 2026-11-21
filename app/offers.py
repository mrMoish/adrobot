from fastapi import APIRouter, HTTPException

from app.tracker import request, request_with_status

router = APIRouter()


@router.get("/api/offers")
def offers(diagnostics: bool = False):
    status, rows = request_with_status("GET", "offers")
    available = [
        {"id": item["id"], "name": item["name"]}
        for item in rows if item.get("state") != "deleted"
    ]
    if diagnostics:
        return {"upstream_status": status, "received": len(rows), "available": len(available)}
    return available


@router.get("/api/offers/{offer_id}")
def offer(offer_id: int):
    result = request("GET", f"offers/{offer_id}")
    return {"id": result["id"], "name": result["name"]}


def require_available_offer(offer_id):
    try:
        selected_offer = request("GET", f"offers/{offer_id}")
    except HTTPException as error:
        if error.status_code != 404:
            raise
        raise HTTPException(404, "Выбранный оффер отсутствует в Keitaro", headers={"X-Error-Field": "offer_id"}) from None
    if selected_offer.get("state") == "deleted":
        raise HTTPException(404, "Выбранный оффер удалён в Keitaro", headers={"X-Error-Field": "offer_id"})
