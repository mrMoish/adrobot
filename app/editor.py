from threading import Lock
from time import monotonic
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.db import Session, StreamState
from app.offers import require_available_offer
from app.tracker import request

router = APIRouter()


# Serializes state changes and remote writes in the documented single-worker setup.
write_lock = Lock()


class Edit(BaseModel):
    action: Literal["add", "remove", "restore", "pin", "unpin"]
    offer_id: int = Field(gt=0)
    share: Optional[int] = Field(default=None, ge=1, le=100)


def incoming_offers(stream):
    return [
        {key: offer[key] for key in ("offer_id", "state", "share") if key in offer}
        for offer in stream.get("offers", []) if offer.get("state") != "deleted"
    ]


def membership(offers):
    return {offer["offer_id"]: (offer["share"], offer.get("state", "active")) for offer in offers}


def baseline(offers):
    return [[offer_id, list(details)] for offer_id, details in sorted(membership(offers).items())]


def reconcile(row, remote):
    old_ids = set(membership(row.published))
    remote_members = membership(remote)
    new_ids = set(remote_members)
    row.archive = sorted((set(row.archive) | (old_ids - new_ids)) - new_ids)
    row.pins = {
        key: value for key, value in row.pins.items()
        if remote_members.get(int(key)) == (value, "active")
    }
    row.published = remote


def snapshot(row):
    if row.draft is not None:
        return row.draft
    return {"offers": row.published, "archive": row.archive, "pins": row.pins}


def discard_unchanged_draft(row):
    draft = row.draft
    if (draft is not None and draft["pins"] == row.pins
            and set(draft["archive"]) == set(row.archive)
            and membership(draft["offers"]) == membership(row.published)):
        row.draft = None


def view(row):
    discard_unchanged_draft(row)
    return {
        **snapshot(row),
        "dirty": row.draft is not None,
        "conflict": row.draft is not None
        and row.draft["base"] != baseline(row.published),
    }


def validate_weights(offers, pins):
    active = {}
    for offer in offers:
        if offer.get("state", "active") != "active":
            continue
        key = str(offer["offer_id"])
        if key in active:
            raise HTTPException(422, "Активный оффер встречается в потоке несколько раз")
        active[key] = offer.get("share")
    if (any(type(share) is not int or not 1 <= share <= 100 for share in active.values())
            or sum(active.values()) != 100):
        raise HTTPException(422, "Доли активных офферов должны быть целыми положительными числами в сумме 100%")
    if any(type(share) is not int or active.get(key) != share for key, share in pins.items()):
        raise HTTPException(422, "Закреплённая доля должна совпадать с весом активного оффера")


def redistribute(offers, pins):
    active = sorted(
        offer["offer_id"] for offer in offers if offer.get("state", "active") == "active"
    )
    if not active:
        raise HTTPException(422, "В потоке должен остаться хотя бы один активный оффер")
    free = [offer_id for offer_id in active if str(offer_id) not in pins]
    remaining = 100 - sum(pins.values())
    if (free and remaining < len(free)) or (not free and remaining != 0):
        raise HTTPException(422, "Закреплённые доли не позволяют распределить 100% трафика")
    shares = dict(pins)
    if free:
        base, extra = divmod(remaining, len(free))
        shares.update({str(offer_id): base + (index < extra) for index, offer_id in enumerate(free)})
    return [{**offer, "share": shares.get(str(offer["offer_id"]), 0)} for offer in offers]


catalog_lock = Lock()
catalog_expires = 0.0
campaign_catalog = ()


def cached_campaigns():
    global campaign_catalog, catalog_expires
    with catalog_lock:
        if monotonic() < catalog_expires:
            return campaign_catalog
        rows = {}
        seen = set()
        offset = 0
        while True:
            page = request("GET", f"campaigns?limit=250&offset={offset}")
            if not page:
                break
            ids = {item["id"] for item in page}
            if ids <= seen:
                raise HTTPException(502, "Keitaro повторил страницу каталога кампаний")
            seen.update(ids)
            for item in page:
                if item.get("state") != "deleted":
                    rows[item["id"]] = {"id": item["id"], "name": item["name"]}
            offset += len(page)
        campaign_catalog = tuple(rows.values())
        catalog_expires = monotonic() + 60
        return campaign_catalog


@router.get("/api/campaigns/search")
def search_campaigns(q: str):
    query = q.strip().casefold()
    if not query:
        return []
    matches = []
    for campaign in cached_campaigns():
        if query in campaign["name"].casefold():
            matches.append(campaign)
            if len(matches) == 20:
                break
    return matches


@router.get("/api/campaigns")
def campaigns():
    return cached_campaigns()


@router.post("/api/campaigns/{campaign_id}/fetch")
def fetch(campaign_id: int):
    with write_lock, Session.begin() as db:
        streams = request("GET", f"campaigns/{campaign_id}/streams")
        result = []
        for stream in streams:
            if stream.get("state") == "deleted":
                continue
            summary = {"id": stream["id"], "name": stream["name"], "editable": stream["schema"] == "landings"}
            result.append(summary)
            if summary["editable"]:
                remote = incoming_offers(stream)
                row = db.get(StreamState, stream["id"])
                if not remote and not has_offer_history(row):
                    summary["editable"] = False
                    continue
                if row is None:
                    row = StreamState(id=stream["id"], campaign_id=campaign_id, published=remote, archive=[], pins={})
                    db.add(row)
                else:
                    reconcile(row, remote)
                summary.update(view(row))
        return result


def has_offer_history(row):
    return row is not None and bool(row.published or row.archive or row.draft)


def get_row(db, stream_id):
    row = db.get(StreamState, stream_id)
    if row is None:
        raise HTTPException(404, "Сначала загрузите потоки кампании")
    if not has_offer_history(row):
        raise HTTPException(409, "У этого потока нет офферов для редактирования")
    return row


@router.post("/api/streams/{stream_id}/edit")
def edit(stream_id: int, data: Edit):
    with write_lock, Session.begin() as db:
        row = get_row(db, stream_id)
        current = view(row)
        if current["conflict"]:
            raise HTTPException(409, "Поток изменился в Keitaro. Сбросьте черновик и повторите правки.")
        offers = {offer["offer_id"]: offer for offer in current["offers"]}
        archive = set(current["archive"])
        pins = dict(current["pins"])
        offer_id = data.offer_id
        if data.action == "add":
            if offer_id in offers or offer_id in archive:
                raise HTTPException(422, "Оффер уже есть в потоке или в архиве")
            require_available_offer(offer_id)
            offers[offer_id] = {"offer_id": offer_id, "state": "active", "share": 0}
        elif data.action == "restore" and offer_id in archive:
            require_available_offer(offer_id)
            archive.remove(offer_id)
            offers[offer_id] = {"offer_id": offer_id, "state": "active", "share": 0}
        elif offer_id not in offers:
            raise HTTPException(422, "Оффер отсутствует в активном списке")
        elif data.action == "remove":
            del offers[offer_id]
            archive.add(offer_id)
            pins.pop(str(offer_id), None)
        elif data.action == "pin" and data.share is not None and offers[offer_id].get("state", "active") == "active":
            pins[str(offer_id)] = data.share
        elif data.action == "unpin":
            pins.pop(str(offer_id), None)
        else:
            raise HTTPException(422, "Недопустимое изменение")
        updated_offers = list(offers.values())
        # Toggling a lock on an existing share doesn't change traffic distribution.
        if data.action != "unpin" and not (data.action == "pin" and data.share == offers[offer_id]["share"]):
            updated_offers = redistribute(updated_offers, pins)
        validate_weights(updated_offers, pins)
        row.draft = {
            "offers": updated_offers,
            "archive": sorted(archive),
            "pins": pins,
            "base": baseline(row.published),
        }
        return view(row)


@router.post("/api/streams/{stream_id}/cancel")
def cancel(stream_id: int):
    with write_lock, Session.begin() as db:
        row = get_row(db, stream_id)
        row.draft = None
        return view(row)


@router.post("/api/streams/{stream_id}/push")
def push(stream_id: int):
    with write_lock, Session.begin() as db:
        row = get_row(db, stream_id)
        discard_unchanged_draft(row)
        if row.draft is None:
            return view(row)
        draft = row.draft
        validate_weights(draft["offers"], draft["pins"])
        remote = request("GET", f"streams/{stream_id}")
        offers = incoming_offers(remote)
        if remote["schema"] != "landings" or remote.get("state") == "deleted":
            raise HTTPException(409, "Поток больше недоступен для правок")
        if membership(offers) != membership(draft["offers"]):
            if baseline(offers) != draft["base"]:
                raise HTTPException(409, "Поток изменился в Keitaro. Загрузите его заново.")
            response = request("PUT", f"streams/{stream_id}", {"offers": draft["offers"]})
            offers = incoming_offers(response)
            if membership(offers) != membership(draft["offers"]):
                raise HTTPException(502, "Keitaro вернул другие доли. Загрузите поток заново.")
        row.published = offers
        row.archive = draft["archive"]
        row.pins = draft["pins"]
        row.draft = None
        return view(row)
