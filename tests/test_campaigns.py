import os
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from app.main import app


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.responses = {
            ("GET", "offers"): (200, [
                {"id": 7, "name": "Offer", "action_payload": "https://example.test/"},
                {"id": 8, "name": "Deleted", "state": "deleted"},
            ]),
            ("GET", "offers/7"): (200, {"id": 7, "name": "Offer"}),
            ("GET", "domains"): (200, [{"id": 1, "name": "in.posdk.xyz"}]),
            ("GET", "groups?type=campaigns"): (200, [{"id": 2, "name": "FORTESTS"}]),
            ("GET", "traffic_sources"): (200, [{"id": 3, "name": "FORTESTS"}]),
            ("POST", "campaigns"): (200, {"id": 10}),
            ("POST", "streams"): (200, {"id": 11}),
            ("POST", "campaigns/10/enable"): (200, {}),
        }
        self.enter_patch(patch.dict(os.environ, {
            "KEITARO_URL": "https://keitaro.example.test",
            "KEITARO_API_KEY": "test-key",
            "KEITARO_CAMPAIGN_DOMAIN": "https://in.posdk.xyz/",
            "KEITARO_CAMPAIGN_GROUP": "FORTESTS",
            "KEITARO_CAMPAIGN_SOURCE": "FORTESTS",
        }))
        self.enter_patch(patch("app.tracker.httpx.request", side_effect=self.remote))
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def enter_patch(self, patcher):
        patcher.start()
        self.addCleanup(patcher.stop)

    def remote(self, method, url, headers, json, timeout):
        self.assertEqual(headers, {"Api-Key": "test-key"})
        path = url.split("/admin_api/v1/", 1)[1]
        self.calls.append((method, path, json))
        status, data = self.responses[(method, path)]
        return httpx.Response(status, json=data, request=httpx.Request(method, url))

    def create(self, **fields):
        return self.client.post("/api/campaigns", json={
            "name": "Test", "country": " mx, AU ,ro, MX ", "offer_id": 7, **fields,
        })

    def test_resolves_defaults_and_enables_after_ordered_streams(self):
        response = self.create()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json(), {"id": 10})
        campaign, google, fallback, enable = [
            (path, body) for method, path, body in self.calls if method == "POST"
        ]
        path, body = campaign
        self.assertEqual(path, "campaigns")
        body.pop("alias")
        self.assertEqual(body, {
            "name": "Test", "type": "position", "state": "disabled",
            "domain_id": 1, "group_id": "2", "traffic_source_id": 3,
        })
        self.assertEqual(google, ("streams", {
            "name": "MX,AU,RO → Google", "type": "regular", "position": 1,
            "schema": "redirect", "action_type": "http",
            "action_payload": "https://www.google.com/",
            "filters": [{"name": "country", "mode": "accept", "payload": ["MX", "AU", "RO"]}],
            "campaign_id": 10,
        }))
        self.assertEqual(fallback, ("streams", {
            "name": "Офферы", "type": "default", "position": 2, "schema": "landings",
            "action_type": "http", "campaign_id": 10,
            "offers": [{"offer_id": 7, "state": "active", "share": 100}],
        }))
        self.assertEqual(enable, ("campaigns/10/enable", None))

    def test_domain_hostname_takes_priority_over_display_name(self):
        self.responses[("GET", "domains")] = (200, [
            {"id": 8, "name": "Test domain", "domain": "IN.POSDK.XYZ"},
        ])
        self.assertEqual(self.create().status_code, 201)
        campaign = next(body for method, path, body in self.calls if path == "campaigns")
        self.assertEqual(campaign["domain_id"], 8)

    def test_domain_priority_and_fallback(self):
        backup = {"id": 9, "name": "BACKUP.EXAMPLE", "state": "active", "network_status": "ok"}
        primary = {"id": 1, "name": "in.posdk.xyz"}
        for domains, expected in (([backup, primary], 1), ([dict(backup, id=2, status="error"), dict(backup, id=3, state="disabled"), backup, dict(backup, id=4)], 9),
                                  ([dict(primary, state="deleted"), backup], 9)):
            with self.subTest(domains=domains):
                self.calls.clear()
                self.responses[("GET", "domains")] = (200, domains)
                self.assertEqual(self.create().status_code, 201)
                self.assertEqual(sum(path == "domains" for _, path, _ in self.calls), 1)
                campaign = next(body for _, path, body in self.calls if path == "campaigns")
                self.assertEqual(campaign["domain_id"], expected)

    def test_fallback_requires_confirmed_status(self):
        self.responses[("GET", "domains")] = (200, [{"id": 22, "name": "unrelated.example"}])
        response = self.create()
        self.assertEqual(response.status_code, 422)
        self.assertIn("нет активного домена", response.json()["detail"])
        self.assertTrue(all(method == "GET" for method, _, _ in self.calls))

    def test_unhealthy_preferred_domain(self):
        backup = {"id": 2, "name": "backup.test", "network_status": "ok"}
        for problem in ({"status": "error"}, {"network_status": "offline"},
                        {"status": "ok", "error_solution": "Fix DNS"}):
            primary = {"id": 1, "name": "in.posdk.xyz", **problem}
            for backups in ([], [backup]):
                with self.subTest(problem=problem, backups=backups):
                    self.calls.clear()
                    self.responses["GET", "domains"] = (200, [primary, *backups])
                    response = self.create()
                    if backups:
                        self.assertEqual(response.status_code, 201)
                        body = next(body for _, path, body in self.calls if path == "campaigns")
                        self.assertEqual(body["domain_id"], backup["id"])
                    else:
                        self.assertEqual(response.status_code, 422)
                        self.assertTrue(all(method == "GET" for method, _, _ in self.calls))

    def test_invalid_geo_never_calls_keitaro(self):
        for country in ("", "MEX", "MX,,AU", "MX,"):
            with self.subTest(country=country):
                self.assertEqual(self.create(country=country).status_code, 422)
        self.assertEqual(self.calls, [])

    def test_missing_settings_prevent_writes(self):
        for path, name in (
            ("domains", "in.posdk.xyz"), ("groups?type=campaigns", "Группа"),
            ("traffic_sources", "Источник"),
        ):
            with self.subTest(path=path):
                original = self.responses[("GET", path)]
                self.responses[("GET", path)] = (200, [])
                response = self.create()
                self.responses[("GET", path)] = original
                self.assertEqual(response.status_code, 422)
                self.assertIn(name, response.json()["detail"])
        self.assertTrue(all(method == "GET" for method, _, _ in self.calls))

    def test_ambiguous_configured_group_prevents_writes(self):
        self.responses[("GET", "groups?type=campaigns")] = (200, [
            {"id": 4, "name": "QA"}, {"id": 5, "name": "QA"},
        ])
        with patch.dict(os.environ, {"KEITARO_CAMPAIGN_GROUP": "QA"}):
            response = self.create()
        self.assertEqual(response.status_code, 422)
        self.assertIn("«QA»", response.json()["detail"])
        self.assertTrue(all(method == "GET" for method, _, _ in self.calls))

    def test_existing_options_and_explicit_ids_remain_supported(self):
        self.assertEqual(self.client.get("/api/options").json(), {
            "domains": [{"id": 1, "name": "in.posdk.xyz"}],
            "groups": [{"id": 2, "name": "FORTESTS"}],
            "sources": [{"id": 3, "name": "FORTESTS"}],
        })
        self.calls.clear()
        self.assertEqual(self.create(country="mx", domain_id=5, group_id=6, traffic_source_id=9).status_code, 201)
        self.assertEqual([path for method, path, _ in self.calls if method == "GET"], ["offers/7"])

    def test_catalog_returns_unlinked_offers_with_distinct_ids(self):
        self.responses[("GET", "offers")] = (200, [
            {"id": 7, "name": "Offer"}, {"id": 9, "name": "Offer"},
            {"id": 12, "name": "Other offer"}, {"id": 13, "name": "Archived", "state": "deleted"},
        ])
        result = self.client.get("/api/offers")
        self.assertEqual(result.status_code, 200)
        self.assertEqual([item["id"] for item in result.json()], [7, 9, 12])
        self.assertEqual(self.calls, [("GET", "offers", None)])

    def test_editor_catalog_diagnostics_and_picker(self):
        self.assertEqual(self.client.get("/api/offers?diagnostics=true").json(),
                         {"upstream_status": 200, "received": 2, "available": 1})
        self.assertIn('id="editor-offers"', self.client.get("/editor").text)
        self.assertTrue(all(method == "GET" for method, _, _ in self.calls))

    def test_offer_catalog_and_existing_lookup(self):
        response = self.client.get("/api/offers")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [{"id": 7, "name": "Offer"}])
        self.assertEqual(self.client.get("/api/offers/7").json(), {"id": 7, "name": "Offer"})

    def test_validation_failure_shows_operation_and_reason_without_api_key(self):
        self.responses[("POST", "campaigns")] = (422, {
            "message": "Invalid input test-key", "errors": {"alias": ["Already used"]},
        })
        response = self.create()
        self.assertEqual(response.status_code, 422)
        detail = response.json()["detail"]
        self.assertIn("POST /campaigns", detail)
        self.assertIn("alias: Already used", detail)
        self.assertNotIn("test-key", detail)

    def test_offer_catalog_explains_upstream_authentication_failure(self):
        self.responses[("GET", "offers")] = (401, {"error": "private upstream message"})
        response = self.client.get("/api/offers")
        self.assertEqual(response.status_code, 502)
        self.assertIn("HTTP 401", response.json()["detail"])
        self.assertIn("Admin API", response.json()["detail"])
        self.assertNotIn("private upstream message", response.text)
        self.assertNotIn("test-key", response.text)

    def test_archived_name_conflict(self):
        self.responses[("POST", "campaigns")] = (422, {"name": "Name has already been used in the archive"})
        response = self.create()
        self.assertEqual(response.status_code, 422)
        self.assertIn("Введите другое название", response.json()["detail"])
        self.assertEqual([path for method, path, _ in self.calls if method == "POST"], ["campaigns"])

    def test_validation_formats_on_campaign_creation(self):
        for payload in (
            [{"attribute": "group_id", "message": "Invalid group"}],
            {"error": {"messages": {"group_id": ["Invalid group"]}}},
            {"group_id": "Invalid group"},
        ):
            with self.subTest(payload=payload):
                self.responses[("POST", "campaigns")] = (422, payload)
                response = self.create()
                self.assertEqual(response.status_code, 422)
                self.assertIn("group_id: Invalid group", response.json()["detail"])
                self.assertFalse(any(path == "streams" for _, path, _ in self.calls))

    def test_missing_or_deleted_offer_prevents_writes(self):
        for remote in ((404, {}), (200, {"id": 7, "state": "deleted"})):
            self.responses[("GET", "offers/7")] = remote
            response = self.create()
            self.assertEqual(response.status_code, 404)
            self.assertIn("оффер", response.json()["detail"])
        self.assertTrue(all(method == "GET" for method, _, _ in self.calls))

    def test_stream_failure_reports_campaign_id_and_does_not_enable(self):
        self.responses[("POST", "streams")] = (500, {})
        response = self.create()
        self.assertEqual(response.status_code, 502)
        self.assertIn("кампании 10", response.json()["detail"])
        self.assertNotIn("campaigns/10/enable", [path for _, path, _ in self.calls])

    def test_late_creation_failures_stop_at_failed_step(self):
        for fail_at in ("second_stream", "enable"):
            with self.subTest(fail_at=fail_at):
                self.calls.clear()

                def fail_step(method, url, headers, json, timeout):
                    response = self.remote(method, url, headers, json, timeout)
                    if fail_at == "second_stream" and url.endswith("/streams") and json["position"] == 2:
                        return httpx.Response(503, request=response.request)
                    if fail_at == "enable" and url.endswith("/enable"):
                        raise httpx.ReadTimeout("timeout", request=response.request)
                    return response

                with patch("app.tracker.httpx.request", side_effect=fail_step):
                    response = self.create()
                self.assertEqual(response.status_code, 502)
                self.assertIn("кампании 10", response.json()["detail"])
                expected = ["campaigns", "streams", "streams"]
                if fail_at == "enable":
                    expected.append("campaigns/10/enable")
                self.assertEqual([path for method, path, _ in self.calls if method == "POST"], expected)

    def test_catalog_access_limit_and_upstream_failure(self):
        for upstream, expected, reason in ((403, 403, "права"), (429, 429, "лимит"), (503, 502, "сервера")):
            with self.subTest(upstream=upstream):
                self.responses["GET", "offers"] = (upstream, {"error": "test-key"})
                response = self.client.get("/api/offers")
                self.assertEqual(response.status_code, expected)
                self.assertIn(reason, response.json()["detail"])
                self.assertNotIn("test-key", response.text)
