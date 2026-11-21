import os

import httpx
from fastapi import HTTPException


def validation_messages(value, fields, label=""):
    if isinstance(value, str) and value.strip():
        yield f"{label}: {value.strip()}" if label else value.strip()
    elif isinstance(value, list):
        for item in value:
            yield from validation_messages(item, fields, label)
    elif isinstance(value, dict):
        field = value.get("field") or value.get("attribute") or value.get("property")
        if isinstance(field, str) and field in fields:
            label = field
        for key, item in value.items():
            if key in ("message", "messages", "error", "errors", "detail", "description", "msg"):
                yield from validation_messages(item, fields, label)
            elif key in fields:
                yield from validation_messages(item, fields, key)


def validation_reason(response, body, key):
    fields = set(body or {})
    try:
        data = response.json()
    except ValueError:
        data = response.text if response.headers.get("content-type", "").startswith("text/plain") else None
    messages = dict.fromkeys(validation_messages(data, fields))
    if messages:
        return " · ".join(messages).replace(key, "[скрыто]")[:800]
    if not response.content.strip():
        return "Keitaro вернул пустое тело ответа."
    return "В ответе Keitaro нет распознанного описания ошибки."


def request_with_status(method, path, body=None):
    url = os.getenv("KEITARO_URL", "").rstrip("/")
    key = os.getenv("KEITARO_API_KEY", "")
    if not url or not key:
        raise HTTPException(503, "Укажите KEITARO_URL и KEITARO_API_KEY в .env")
    if url.endswith("/admin"):
        url = url[:-6]
    try:
        response = httpx.request(
            method, url + "/admin_api/v1/" + path,
            headers={"Api-Key": key}, json=body, timeout=15,
        )
        response.raise_for_status()
        return response.status_code, response.json() if response.content else None
    except httpx.HTTPStatusError as error:
        status = error.response.status_code
        if status == 401:
            raise HTTPException(502, "Keitaro отклонил авторизацию (HTTP 401). Проверьте, что "
                                "KEITARO_API_KEY — действующий ключ Admin API для сервера KEITARO_URL. "
                                "После изменения настроек перезапустите Adrobot.") from None
        if status == 403:
            raise HTTPException(403, "Keitaro запретил операцию (HTTP 403). Проверьте права "
                                "пользователя API на этот ресурс.") from None
        if status == 429:
            raise HTTPException(429, "Достигнут лимит запросов Keitaro (HTTP 429).") from None
        if 500 <= status < 600:
            raise HTTPException(502, f"Ошибка сервера Keitaro (HTTP {status}).") from None
        if status == 422:
            reason = validation_reason(error.response, body, key)
            if method == "POST" and path == "campaigns" and reason.casefold().rstrip(".") == (
                "name: name has already been used in the archive"
            ):
                raise HTTPException(422, "Кампания с таким названием уже есть в архиве Keitaro. "
                                         "Введите другое название или восстановите кампанию из архива.",
                                    headers={"X-Error-Field": "name"}) from None
            raise HTTPException(422, f"Keitaro отклонил {method} /{path} (HTTP 422). {reason}") from None
        raise HTTPException(404 if status == 404 else 502, f"Keitaro вернул HTTP {status}") from None
    except (httpx.RequestError, ValueError):
        raise HTTPException(502, "Не удалось получить ответ Keitaro") from None


def request(method, path, body=None):
    _, data = request_with_status(method, path, body)
    return data
