import re
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.config import campaign_defaults
from app.offers import require_available_offer
from app.tracker import request

router = APIRouter()


class Campaign(BaseModel):
    name: str = Field(min_length=1, max_length=255, pattern=r"\S")
    country: str
    offer_id: int = Field(gt=0)
    domain_id: Optional[int] = Field(default=None, gt=0)
    group_id: Optional[int] = Field(default=None, gt=0)
    traffic_source_id: Optional[int] = Field(default=None, gt=0)

    @field_validator("country")
    @classmethod
    def normalize_country(cls, value: str) -> str:
        codes = [code.strip() for code in value.split(",")]
        if any(not re.fullmatch(r"[A-Za-z]{2}", code) for code in codes):
            raise ValueError("Введите двухбуквенные коды стран через запятую")
        return ",".join(dict.fromkeys(code.upper() for code in codes))


OPTION_PATHS = {
    "domains": "domains", "groups": "groups?type=campaigns", "sources": "traffic_sources",
}


@router.get("/api/options")
def options():
    return {key: request("GET", path) for key, path in OPTION_PATHS.items()}


def domain_name(value):
    return value.strip().lower().removeprefix("https://").removeprefix("http://").rstrip("/")


def resolve_domain(primary):
    primary_host = domain_name(primary)
    primary_ids = set()
    fallback_id = None
    healthy = {"ok", "success", "active", "ready", "valid", "connected", "configured",
               "correct", "working", "available", "online"}
    for item in request("GET", OPTION_PATHS["domains"]):
        host = domain_name(item.get("domain") or item.get("name") or "")
        domain_id = item.get("id")
        if not host or type(domain_id) is not int or domain_id <= 0 or item.get("state", "active") != "active":
            continue
        statuses = {str(item[key]).strip().lower() for key in ("status", "network_status") if item.get(key)}
        if item.get("error_solution") or not statuses <= healthy:
            continue
        if host == primary_host:
            primary_ids.add(domain_id)
        elif statuses and fallback_id is None:
            fallback_id = domain_id
    if len(primary_ids) > 1:
        raise HTTPException(422, f"Домен «{primary}» соответствует нескольким ID в Keitaro")
    if primary_ids:
        return primary_ids.pop()
    if fallback_id is not None:
        return fallback_id
    raise HTTPException(422, f"Домен «{primary}» недоступен. В списке Keitaro нет активного домена "
                             "с подтверждённой настройкой. Проверьте статус доменов и права API-ключа.")


def resolve_campaign_defaults(settings):
    defaults = campaign_defaults()
    if "domain_id" not in settings:
        settings["domain_id"] = resolve_domain(defaults["domain_id"])
    for field, option, label in (
        ("group_id", "groups", "Группа кампаний"),
        ("traffic_source_id", "sources", "Источник трафика"),
    ):
        if field in settings:
            continue
        expected = defaults[field].strip()
        matches = [item["id"] for item in request("GET", OPTION_PATHS[option])
                   if item.get("state") != "deleted" and item.get("name") == expected]
        if not matches:
            raise HTTPException(422, f"{label} «{expected}» отсутствует в Keitaro")
        if len(set(matches)) > 1:
            raise HTTPException(422, f"{label} «{expected}» соответствует нескольким объектам в Keitaro")
        settings[field] = matches[0]


@router.post("/api/campaigns", status_code=201)
def create_campaign(data: Campaign):
    require_available_offer(data.offer_id)
    settings = data.model_dump(exclude={"offer_id", "country"}, exclude_none=True)
    resolve_campaign_defaults(settings)
    settings.update(alias=f"adrobot-{uuid4().hex[:12]}", type="position", state="disabled")
    settings["group_id"] = str(settings["group_id"])
    campaign = request("POST", "campaigns", settings)
    campaign_id = campaign["id"]
    try:
        for stream in (
            {
                "name": f"{data.country} → Google", "type": "regular", "position": 1,
                "schema": "redirect", "action_type": "http", "action_payload": "https://www.google.com/",
                "filters": [{"name": "country", "mode": "accept", "payload": data.country.split(",")}],
            },
            {
                "name": "Офферы", "type": "default", "position": 2, "schema": "landings",
                "action_type": "http",
                "offers": [{"offer_id": data.offer_id, "state": "active", "share": 100}],
            },
        ):
            request("POST", "streams", {**stream, "campaign_id": campaign_id})
        request("POST", f"campaigns/{campaign_id}/enable")
    except HTTPException as error:
        raise HTTPException(error.status_code, f"Не удалось завершить настройку кампании {campaign_id}. {error.detail}") from None
    return {"id": campaign_id}
