import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import editor
from app.db import Base, StreamState
from app.main import app


class SectionTests(unittest.TestCase):
    def test_campaign_list_only_reads_campaigns(self):
        rows = [{"id": 4, "name": "Campaign", "token": "private"},
                {"id": 5, "name": "Archived", "state": "deleted"}]
        with patch("app.editor.catalog_expires", 0), patch("app.editor.campaign_catalog", ()), patch(
            "app.editor.request", side_effect=[rows, []]
        ) as remote, TestClient(app) as client:
            response = client.get("/api/campaigns")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), [{"id": 4, "name": "Campaign"}])
            self.assertEqual([call.args for call in remote.call_args_list],
                             [("GET", "campaigns?limit=250&offset=0"), ("GET", "campaigns?limit=250&offset=2")])

    def test_search_pages_caches_and_limits_matches(self):
        page = [{"id": i, "name": "Demo"} for i in range(1, 23)]
        pages = [page, [{"id": 23, "name": "На второй странице"}], []]
        with patch("app.editor.catalog_expires", 0), patch("app.editor.campaign_catalog", ()), patch(
            "app.editor.request", side_effect=pages
        ) as remote, TestClient(app) as client:
            self.assertEqual(client.get("/api/campaigns/search", params={"q": " "}).json(), [])
            remote.assert_not_called()
            self.assertEqual(len(client.get("/api/campaigns/search?q=demo").json()), 20)
            result = client.get("/api/campaigns/search", params={"q": "ВТОРОЙ"})
            self.assertEqual(result.json(), pages[1])
            catalog = client.get("/api/campaigns")
            self.assertEqual(catalog.json(), page + pages[1])
            self.assertNotIn("content-disposition", catalog.headers)
            self.assertIn('id="all-campaigns"', client.get("/editor").text)
            self.assertEqual([call.args[1] for call in remote.call_args_list],
                             ["campaigns?limit=250&offset=0", "campaigns?limit=250&offset=22",
                              "campaigns?limit=250&offset=23"])

    def test_navigation_and_assets(self):
        with TestClient(app) as client:
            redirect = client.get("/", follow_redirects=False)
            self.assertEqual(redirect.status_code, 307)
            self.assertEqual(redirect.headers["location"], "/creator")
            for page, form, other in (("creator", "create", "fetch"), ("editor", "fetch", "create")):
                response = client.get("/" + page)
                self.assertEqual(response.status_code, 200)
                self.assertIn(f'<a href="/{page}" aria-current="page"', response.text)
                self.assertIn(f'<form id="{form}"', response.text)
                self.assertNotIn(f'<form id="{other}"', response.text)
                self.assertIn(f'src="/static/{page}.js"', response.text)
                self.assertEqual(client.get(f"/static/{page}.js").status_code, 200)
            self.assertEqual(client.get("/static/style.css").status_code, 200)
            self.assertEqual(client.get("/static/common.js").status_code, 200)

    def editor_client(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        engine = create_engine(f"sqlite:///{Path(folder.name) / 'test.db'}")
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        session = patch("app.editor.Session", sessionmaker(engine))
        session.start()
        self.addCleanup(session.stop)
        client = TestClient(app)
        self.addCleanup(client.close)
        return client

    def test_disabled_weight_survives_edit_and_push(self):
        client = self.editor_client()
        disabled = dict(offer_id=3, state="disabled", share=15)
        stream = dict(id=9, name="Test", schema="landings", offers=[
            dict(offer_id=1, share=50), dict(offer_id=2, share=50), disabled])
        with patch("app.editor.request", return_value=[stream]):
            client.post("/api/campaigns/2/fetch").raise_for_status()
        draft = client.post("/api/streams/9/edit", json=dict(action="remove", offer_id=1)).json()
        expected = [dict(offer_id=2, share=100), disabled]
        self.assertEqual(draft["offers"], expected)
        with editor.Session.begin() as db:
            row = db.get(StreamState, 9)
            row.draft = {**row.draft, "offers": [expected[0], {**disabled, "share": 0}]}
        with patch("app.editor.request", side_effect=[stream, {**stream, "offers": expected}]) as upstream:
            client.post("/api/streams/9/push").raise_for_status()
            self.assertEqual(upstream.call_args.args, ("PUT", "streams/9", {"offers": expected}))

    def test_malformed_fetch_preserves_previous_state(self):
        client = self.editor_client()
        stream = dict(id=9, name="Test", schema="landings", offers=[dict(offer_id=1, share=100)])
        with patch("app.editor.request", return_value=[stream]):
            client.post("/api/campaigns/2/fetch").raise_for_status()
        with patch("app.editor.request", return_value=[{**stream, "offers": [dict(offer_id=1)]}]):
            self.assertEqual(client.post("/api/campaigns/2/fetch").status_code, 502)
        self.assertEqual(client.post("/api/streams/9/cancel").json()["offers"], stream["offers"])

    def test_external_removal_and_empty_streams(self):
        client = self.editor_client()
        offer = dict(offer_id=7, state="active", share=100)
        stream = dict(id=9, name="Offers", schema="landings", offers=[offer])
        empty = dict(id=10, name="Empty", schema="landings", offers=[])
        redirect = dict(id=11, name="Google", schema="redirect")

        def fetch():
            response = client.post("/api/campaigns/2/fetch")
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()

        with patch("app.editor.request", return_value=[stream, empty, redirect]) as remote:
            initial, *summaries = fetch()
            self.assertEqual(initial["offers"], [offer])
            self.assertEqual(summaries, [
                {"id": 10, "name": "Empty", "editable": False},
                {"id": 11, "name": "Google", "editable": False},
            ])
            stream["offers"] = []
            for _ in range(2):
                archived = fetch()[0]
                self.assertTrue(archived["editable"])
                self.assertEqual(archived["offers"], [])
                self.assertEqual(archived["archive"], [7])
                self.assertFalse(archived["dirty"])
            self.assertTrue(all(call.args == ("GET", "campaigns/2/streams")
                                for call in remote.call_args_list))

        with patch("app.offers.request", return_value={"id": 7}) as remote:
            restored = client.post("/api/streams/9/edit", json={"action": "restore", "offer_id": 7}).json()
            self.assertTrue(restored["dirty"])
            self.assertEqual(restored["offers"], [offer])
            remote.assert_called_once_with("GET", "offers/7")

        with patch("app.editor.request", side_effect=[stream, {**stream, "offers": [offer]}]) as remote:
            published = client.post("/api/streams/9/push").json()
            self.assertFalse(published["dirty"])
            self.assertEqual(published["archive"], [])
            self.assertEqual(remote.call_args_list[-1].args, ("PUT", "streams/9", {"offers": [offer]}))

    def test_invalid_imported_weights_and_legacy_draft(self):
        client = self.editor_client()
        for share in (40, 60):
            with self.subTest(share=share):
                offers = [dict(offer_id=i, share=share) for i in (1, 2)]
                stream = dict(id=9, schema="landings", name="Test", offers=offers)
                with patch("app.editor.request", return_value=[stream]):
                    client.post("/api/campaigns/2/fetch").raise_for_status()
                response = client.post("/api/streams/9/edit", json=dict(action="pin", offer_id=1, share=share))
                self.assertEqual(response.status_code, 422)
                # This draft could have been saved by an earlier release.
                with editor.Session.begin() as db:
                    row = db.get(StreamState, 9)
                    self.assertIsNone(row.draft)
                    row.draft = dict(offers=offers, pins={"1": share, "2": share},
                                     archive=[], base=editor.baseline(offers))
                with patch("app.editor.request") as remote:
                    self.assertEqual(client.post("/api/streams/9/push").status_code, 422)
                    remote.assert_not_called()
                with patch("app.editor.request", return_value=[stream]):
                    self.assertTrue(client.post("/api/campaigns/2/fetch").json()[0]["dirty"])
                self.assertFalse(client.post("/api/streams/9/cancel").json()["dirty"])

    def test_all_active_weights_can_be_pinned_at_100_percent(self):
        client = self.editor_client()
        stream = dict(id=9, schema="landings", name="Test",
                      offers=[dict(offer_id=1, share=73), dict(offer_id=2, share=27)])
        with patch("app.editor.request", return_value=[stream]):
            client.post("/api/campaigns/2/fetch").raise_for_status()
        for offer in stream["offers"]:
            client.post("/api/streams/9/edit", json={"action": "pin", **offer}).raise_for_status()
        with patch("app.editor.request", return_value=stream) as remote:
            state = client.post("/api/streams/9/push").json()
            self.assertFalse(state["dirty"])
            self.assertEqual(state["pins"], {"1": 73, "2": 27})
            remote.assert_called_once_with("GET", "streams/9")

    def test_unavailable_offer_preserves_existing_draft_and_archive(self):
        client = self.editor_client()
        stream = dict(id=9, schema="landings", name="Test",
                      offers=[dict(offer_id=i, share=50) for i in (1, 2)])
        with patch("app.editor.request", return_value=[stream]):
            client.post("/api/campaigns/2/fetch").raise_for_status()
            draft = client.post("/api/streams/9/edit", json=dict(action="remove", offer_id=2)).json()
            for action, offer_id in (("restore", 2), ("add", 3)):
                for missing in (True, False):
                    with self.subTest(action=action, missing=missing), patch(
                        "app.offers.request", side_effect=HTTPException(404) if missing else None,
                        return_value={"id": offer_id, "state": "deleted"},
                    ) as lookup:
                        response = client.post("/api/streams/9/edit", json=dict(action=action, offer_id=offer_id))
                        self.assertEqual(response.status_code, 404)
                        lookup.assert_called_once_with("GET", f"offers/{offer_id}")
                        state = client.post("/api/campaigns/2/fetch").json()[0]
                        for key in ("offers", "pins", "archive", "dirty", "base"):
                            self.assertEqual(state[key], draft[key])

    def test_reopen_retry_failed_push_and_push_again(self):
        client = self.editor_client()
        stream = dict(id=9, schema="landings", name="Test",
                      offers=[dict(offer_id=i, state="active", share=50) for i in (1, 2)])
        with patch("app.editor.request", return_value=[stream]):
            client.post("/api/campaigns/2/fetch").raise_for_status()
        draft = client.post("/api/streams/9/edit", json=dict(action="remove", offer_id=2)).json()
        with patch("app.editor.request", side_effect=[stream, HTTPException(502)]) as upstream:
            self.assertEqual(client.post("/api/streams/9/push").status_code, 502)
            self.assertEqual(upstream.call_args.args, ("PUT", "streams/9", {"offers": draft["offers"]}))
        with TestClient(app) as reopened:
            with patch("app.editor.request", return_value=[stream]):
                state = reopened.post("/api/campaigns/2/fetch").json()[0]
                for key in draft:
                    self.assertEqual(state[key], draft[key])
            published = {**stream, "offers": draft["offers"]}
            with patch("app.editor.request", side_effect=[stream, published]) as upstream:
                result = reopened.post("/api/streams/9/push").json()
                self.assertEqual(upstream.call_args.args, ("PUT", "streams/9", {"offers": draft["offers"]}))
                self.assertFalse(result["dirty"])
                self.assertEqual(result["archive"], [2])
            with patch("app.editor.request") as upstream:
                self.assertEqual(reopened.post("/api/streams/9/push").json(), result)
                upstream.assert_not_called()

    def test_external_change_blocks_push_and_cancel_preserves_remote_state(self):
        client = self.editor_client()
        offers = [dict(offer_id=i, state="active", share=50) for i in (1, 2)]
        stream = dict(id=9, name="Offers", schema="landings", offers=offers)
        with patch("app.editor.request", return_value=[stream]):
            client.post("/api/campaigns/2/fetch").raise_for_status()
        client.post("/api/streams/9/edit", json={"action": "remove", "offer_id": 2}).raise_for_status()
        stream["offers"] = [{**offers[1], "share": 100}]
        with patch("app.editor.request", return_value=stream) as remote:
            response = client.post("/api/streams/9/push")
            self.assertEqual(response.status_code, 409)
            remote.assert_called_once_with("GET", "streams/9")
        with patch("app.editor.request", return_value=[stream]) as remote:
            self.assertTrue(client.post("/api/campaigns/2/fetch").json()[0]["conflict"])
            state = client.post("/api/streams/9/cancel").json()
            self.assertFalse(state["dirty"])
            self.assertEqual(state["archive"], [1])
            self.assertEqual(state["offers"], stream["offers"])
            remote.assert_called_once_with("GET", "campaigns/2/streams")

    def test_editor_draft_publish_cancel_archive_and_restore(self):
        client = self.editor_client()
        stream = dict(id=9, name="Test", schema="landings", offers=[
            dict(offer_id=i, state="active", share=share) for i, share in enumerate([60, 30, 10], 1)
        ])
        writes = []

        def remote(method, path, body=None):
            if (method, path) == ("GET", "campaigns/2/streams"):
                return [stream]
            if (method, path) == ("GET", "offers/4"):
                return {"id": 4}
            if path == "streams/9":
                if method == "PUT":
                    writes.append(body)
                    stream.update(body)
                return stream
            self.fail(f"Unexpected request: {method} {path}")

        def action(endpoint, **body):
            response = client.post("/api/streams/9/" + endpoint, json=body)
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()

        with patch("app.editor.request", side_effect=remote), patch("app.offers.request", side_effect=remote):
            self.assertEqual(client.post("/api/campaigns/2/fetch").status_code, 200)
            pinned = action("edit", action="pin", offer_id=1, share=60)
            self.assertTrue(pinned["dirty"])
            self.assertEqual(pinned["offers"], stream["offers"])
            restored = action("edit", action="unpin", offer_id=1)
            self.assertFalse(restored["dirty"])
            self.assertEqual(restored["offers"], stream["offers"])
            self.assertFalse(client.post("/api/campaigns/2/fetch").json()[0]["dirty"])
            draft = action("edit", action="add", offer_id=4)
            action("edit", action="pin", offer_id=1, share=25)
            self.assertTrue(action("edit", action="unpin", offer_id=1)["dirty"])
            self.assertEqual([o["share"] for o in draft["offers"]], [25] * 4)
            self.assertTrue(draft["dirty"])
            self.assertEqual(len(action("cancel")["offers"]), 3)
            self.assertFalse(writes)
            action("edit", action="add", offer_id=4)
            action("push")
            action("edit", action="pin", offer_id=1, share=25)
            action("edit", action="remove", offer_id=4)
            published = action("push")
            self.assertEqual(published["archive"], [4])
            self.assertEqual([o["share"] for o in stream["offers"]], [25, 38, 37])
            action("edit", action="restore", offer_id=4)
            self.assertFalse(action("push")["dirty"])
            self.assertEqual([o["share"] for o in stream["offers"]], [25] * 4)
