"""Local KOM archive: revisions, one-time migration, and full attempt history."""
import hashlib
import http.client
import json
import os
import tempfile
import threading
import unittest
import uuid
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from wattze import kom_archive, online_kom, state, storage
from wattze.http_handler import Handler


def independent_revision(ele, step, segs):
    canonical = {"ele": [round(float(v), 6) for v in ele], "step": round(float(step), 6),
                 "segs": [{"at": round(float(s["at"]), 6), "end": round(float(s["end"]), 6)} for s in segs]}
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]


class RevisionTests(unittest.TestCase):
    def test_revision_matches_server_formula_and_shipped_route(self):
        route = {"ele": [100, 110.1234567, 120, 110], "step": 25, "segs": [{"at": 0, "end": 75, "label": "Klim"}]}
        expected = independent_revision(route["ele"], route["step"], route["segs"])
        self.assertEqual(kom_archive.route_revision(route), expected)
        self.assertEqual(len(expected), 24)
        with open(os.path.join(state.ROUTE_DIR, "grebbeberg.json"), encoding="utf-8") as f:
            shipped = json.load(f)
        shipped_revision = independent_revision(shipped["ele"], shipped["step"], shipped["segs"])
        self.assertEqual(kom_archive.route_revision(shipped), shipped_revision)
        self.assertEqual(online_kom._route("grebbeberg", 0), shipped_revision)
        self.assertEqual(online_kom._route("grebbeberg", 1), shipped_revision)

    def test_rounding_and_profile_changes(self):
        base = {"ele": [1.0, 2.0], "step": 25, "segs": [{"at": 0, "end": 25}]}
        same = {"ele": [1.0000004, 2.0000004], "step": 25.0000004, "segs": [{"at": 0.0000004, "end": 25.0000004}]}
        other = {"ele": [1.0000006, 2.0], "step": 25, "segs": [{"at": 0, "end": 25}]}
        self.assertEqual(kom_archive.route_revision(base), kom_archive.route_revision(same))
        self.assertNotEqual(kom_archive.route_revision(base), kom_archive.route_revision(other))
        for broken in ({"ele": [1.0], "step": 25, "segs": [{"at": 0, "end": 25}]},
                       {"ele": [1.0, float("nan")], "step": 25, "segs": [{"at": 0, "end": 25}]},
                       {"ele": [True, 2.0], "step": 25, "segs": [{"at": 0, "end": 25}]},
                       {"ele": [1.0, 2.0], "step": 0, "segs": [{"at": 0, "end": 25}]},
                       {"ele": [1.0, 2.0], "step": 25, "segs": []},
                       None):
            self.assertIsNone(kom_archive.route_revision(broken))


class ArchiveStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ride = os.path.join(self.tmp.name, "rides")
        self.routes = os.path.join(self.tmp.name, "routes")
        os.makedirs(self.ride)
        os.makedirs(self.routes)
        self.kom_file = os.path.join(self.ride, "koms.json")
        self.route = {"id": "official", "name": "Officieel", "ele": [100, 110.1234567, 120, 110],
                      "step": 25, "segs": [{"at": 0, "end": 75, "label": "Klim"}]}
        with open(os.path.join(self.routes, "official.json"), "w", encoding="utf-8") as f:
            json.dump(self.route, f)
        other = {"id": "huidig", "name": "Oude berg", "ele": [5, 9, 8], "step": 25,
                 "segs": [{"at": 0, "end": 50, "label": "Nieuwe naam"}]}
        with open(os.path.join(self.routes, "huidig.json"), "w", encoding="utf-8") as f:
            json.dump(other, f)
        for target, name, value in ((state, "RIDE_DIR", self.ride), (state, "ROUTE_DIR", self.routes),
                                    (state, "KOM_FILE", self.kom_file), (storage, "KOM_FILE", self.kom_file)):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def write_koms(self, data):
        with open(self.kom_file, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def test_migration_keeps_only_known_fields_and_unknown_variants(self):
        self.write_koms({
            "kom_Oude berg|10.00|2_0": [
                {"time": 88.5, "avgP": 241, "date": 1_700_000_000_000, "user": "Hans"},
                {"time": "snel"},
                {"date": 5},
            ],
            "kom_Losse klim|3.20|0_1": [{"time": 40, "date": "2024-05-01"}],
            "not-a-kom": [{"time": 10, "date": 1}],
        })
        archived = kom_archive.ensure_archive()
        again = kom_archive.ensure_archive()
        self.assertEqual(len(again["attempts"]), len(archived["attempts"]))
        self.assertTrue(archived["migrated"])
        by_key = {a["key"]: a for a in archived["attempts"]}
        self.assertEqual(set(by_key), {"kom_Oude berg|10.00|2_0", "kom_Losse klim|3.20|0_1"})
        old = by_key["kom_Oude berg|10.00|2_0"]
        self.assertEqual(old["route_name"], "Oude berg")
        self.assertEqual(old["route_km"], "10.00")
        self.assertEqual(old["route_seed"], "2")
        self.assertEqual(old["segment_index"], 0)
        self.assertIsNone(old["segment_name"])
        self.assertIsNone(old["revision"])
        self.assertIsNone(old["route_id"])
        self.assertEqual(old["time"], 88.5)
        self.assertEqual(old["avgP"], 241)
        self.assertEqual(old["date"], 1_700_000_000_000)
        self.assertEqual(old["user"], "Hans")
        self.assertIsNone(old["simulated"])
        self.assertIsNone(old["erg"])
        self.assertIsNone(old["paused"])
        self.assertTrue(old["legacy"])
        self.assertEqual(str(uuid.UUID(old["id"])), old["id"])
        loose = by_key["kom_Losse klim|3.20|0_1"]
        self.assertIsNone(loose["avgP"])
        self.assertIsNone(loose["user"])
        self.assertEqual(loose["date"], "2024-05-01")
        self.assertIsNone(loose["revision"])
        # A leaderboard edit after migration must not invent the deleted history.
        self.write_koms({"kom_Oude berg|10.00|2_0": [
            {"time": 88.5, "avgP": 241, "date": 1_700_000_000_000, "user": "Hans"},
            {"time": 70, "avgP": 300, "date": 1_800_000_000_000, "user": "Hans"},
        ]})
        self.assertEqual(len(kom_archive.ensure_archive()["attempts"]), 2)

    def test_new_attempt_is_complete_and_outside_top_20_still_archived(self):
        self.write_koms({})
        kom_archive.ensure_archive()
        file_revision = independent_revision(self.route["ele"], self.route["step"], self.route["segs"])
        ids = []
        for n in range(21):
            entry = {"time": 100 + n, "avgP": 200 + n, "date": 1_000 + n, "user": "Hans"}
            meta = {"route_id": "official", "route_name": "Officieel", "route_km": 1.875,
                    "route_seed": 0, "segment_index": 0, "segment_name": "Klim",
                    "simulated": n == 0, "erg": False, "paused": n == 3}
            stored = kom_archive.record_attempt("kom_Officieel|1.88|0_0", entry, meta)
            ids.append(stored["attempts"][-1]["id"])
        self.assertEqual(len(ids), len(set(ids)))
        view = kom_archive.public_archive()
        self.assertEqual(len(view["attempts"]), 21)
        self.assertIn("komen niet terug", view["notice"])
        first = view["attempts"][0]
        self.assertEqual(first["revision"], file_revision)
        self.assertEqual(first["route_id"], "official")
        self.assertEqual(first["route_name"], "Officieel")
        self.assertEqual(first["segment_name"], "Klim")
        self.assertEqual(first["route_km"], "1.88")
        self.assertEqual(first["user"], "Hans")
        self.assertEqual(first["time"], 100)
        self.assertEqual(first["avgP"], 200)
        self.assertIs(first["simulated"], True)
        self.assertIs(first["erg"], False)
        self.assertIs(first["paused"], False)
        self.assertIs(first["legacy"], False)
        paused = view["attempts"][3]
        self.assertIs(paused["paused"], True)
        self.assertIs(paused["simulated"], False)
        variant = {"ele": [0, 10, 30], "step": 25, "segs": [{"at": 0, "end": 50}]}
        variant_revision = independent_revision(variant["ele"], variant["step"], variant["segs"])
        self.assertNotEqual(variant_revision, file_revision)
        kom_archive.record_attempt("kom_Officieel|1.88|0_0", {"time": 50, "avgP": 180, "date": 5, "user": "Hans"},
                                   {"route_id": "official", "route_name": "Officieel", "segment_index": 0,
                                    "segment_name": "Klim", "course": variant,
                                    "simulated": False, "erg": True, "paused": False})
        revisions = {a["revision"] for a in kom_archive.public_archive()["attempts"]}
        self.assertEqual(revisions, {file_revision, variant_revision})
        bare = kom_archive.record_attempt("kom_Vrije route|20.00|7_0",
                                          {"time": 12, "date": 9}, None)
        free = bare["attempts"][-1]
        self.assertEqual(free["route_name"], "Vrije route")
        self.assertIsNone(free["revision"])
        self.assertIsNone(free["simulated"])
        self.assertIsNone(free["segment_name"])
        self.assertFalse(free["legacy"])

    def test_import_is_legacy_and_route_id_cannot_escape_the_route_dir(self):
        self.write_koms({})
        kom_archive.record_legacy("kom_Import|1.00|0_0", {"time": 15, "avgP": 0, "date": 4, "user": ""})
        row = kom_archive.public_archive()["attempts"][0]
        self.assertTrue(row["legacy"])
        self.assertIsNone(row["simulated"])
        self.assertEqual(row["avgP"], 0)
        self.assertIsNone(kom_archive.revision_for_route_id("../official"))
        self.assertIsNone(kom_archive.revision_for_route_id("missing"))
        self.assertEqual(kom_archive.revision_for_route_id("official"),
                         independent_revision(self.route["ele"], self.route["step"], self.route["segs"]))

    def test_completed_routes_keep_every_attempt_and_do_not_invent_old_data(self):
        self.write_koms({})
        file_revision = independent_revision(self.route["ele"], self.route["step"], self.route["segs"])
        self.assertEqual(kom_archive.profile_revision(self.route), file_revision)
        plain = {"id": "plain", "name": "Vlak", "ele": [1.0, 2.0, 3.0], "step": 25}
        with open(os.path.join(self.routes, "plain.json"), "w", encoding="utf-8") as f:
            json.dump(plain, f)
        self.assertIsNone(kom_archive.route_revision(plain))
        plain_revision = kom_archive.profile_revision(plain)
        self.assertEqual(len(plain_revision), 24)
        for time, paused in ((1800, True), (1700, False)):
            kom_archive.record_route(
                {"time": time, "avgP": 210, "date": 5000 + time, "user": "Hans"},
                {"route_id": "official", "route_name": "Officieel", "route_km": 1.875, "route_seed": 0,
                 "simulated": True, "erg": False, "paused": paused})
        kom_archive.record_route({"time": 0, "avgP": 1, "date": 1, "user": "Hans"},
                                 {"route_id": "official", "route_name": "Officieel"})
        kom_archive.record_route({"time": 900, "avgP": 150, "date": 9, "user": "Hans"},
                                 {"route_id": "plain", "route_name": "Vlak", "route_km": 0.05, "route_seed": 0,
                                  "simulated": False, "erg": True, "paused": False})
        routes = [row for row in kom_archive.public_archive()["attempts"] if row["kind"] == "route"]
        self.assertEqual(sorted(row["time"] for row in routes), [900, 1700, 1800])
        climbed = next(row for row in routes if row["time"] == 1800)
        self.assertEqual(climbed["revision"], file_revision)
        self.assertEqual(climbed["route_id"], "official")
        self.assertIsNone(climbed["segment"])
        self.assertIsNone(climbed["segment_index"])
        self.assertIsNone(climbed["segment_name"])
        self.assertEqual(climbed["user"], "Hans")
        self.assertEqual(climbed["avgP"], 210)
        self.assertIs(climbed["simulated"], True)
        self.assertIs(climbed["paused"], True)
        self.assertIs(climbed["erg"], False)
        self.assertFalse(climbed["legacy"])
        self.assertEqual(next(row for row in routes if row["time"] == 900)["revision"], plain_revision)
        rides = [{"file": "a.tcx", "name": "Officieel 1.9km", "type": "game", "date": "2024-01-01 10:00",
                  "duration": 1800, "distance": 1875, "avg_w": 200}]
        with open(os.path.join(self.ride, "rides.json"), "w", encoding="utf-8") as f:
            json.dump(rides, f)
        self.assertEqual(kom_archive.route_attempts_from_saved_rides(rides), [])
        self.assertEqual(len(kom_archive.ensure_archive()["attempts"]), 3)
        data, imported = kom_archive.import_route_history(
            {"pr_Oude berg|10.00|2": {"time": 412, "avg": 238, "np": 250, "date": 111, "dist": 10}},
            {"ghost_Oude berg|10.00|2": {"time": 412.4, "pts": [[0, 0], [1, 10]], "avgP": 999}})
        self.assertEqual(imported, 1)
        old = [row for row in kom_archive.public_archive()["attempts"]
               if row["legacy"] and row["kind"] == "route"]
        self.assertEqual(len(old), 1)
        self.assertEqual(old[0]["time"], 412)
        self.assertEqual(old[0]["avgP"], 238)
        self.assertEqual(old[0]["date"], 111)
        self.assertIsNone(old[0]["revision"])
        self.assertIsNone(old[0]["route_id"])
        self.assertIsNone(old[0]["user"])
        self.assertIsNone(old[0]["simulated"])
        self.assertIsNone(old[0]["erg"])
        self.assertIsNone(old[0]["paused"])
        self.assertEqual(old[0]["route_name"], "Oude berg")
        again, imported_again = kom_archive.import_route_history(
            {"pr_Oude berg|10.00|2": {"time": 412, "avg": 238, "date": 111}},
            {"ghost_Los|3.00|1": {"time": 80.2, "avgP": 400}})
        self.assertEqual(imported_again, 1)
        ghosts = [row for row in again["attempts"] if row.get("route_name") == "Los"]
        self.assertEqual(len(ghosts), 1)
        self.assertIsNone(ghosts[0]["avgP"])
        self.assertIsNone(ghosts[0]["date"])
        self.assertIsNone(ghosts[0]["revision"])
        self.assertIn("niet overgenomen", kom_archive.public_archive()["notice"])


class ArchiveHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.daemon_threads = True
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        ride = os.path.join(self.tmp.name, "rides")
        routes = os.path.join(self.tmp.name, "routes")
        os.makedirs(ride)
        os.makedirs(routes)
        self.kom_file = os.path.join(ride, "koms.json")
        route = {"id": "official", "name": "Officieel", "ele": [1, 2, 3, 2], "step": 25,
                 "segs": [{"at": 0, "end": 75, "label": "Klim"}]}
        with open(os.path.join(routes, "official.json"), "w", encoding="utf-8") as f:
            json.dump(route, f)
        self.revision = independent_revision(route["ele"], route["step"], route["segs"])
        with open(self.kom_file, "w", encoding="utf-8") as f:
            json.dump({"kom_Oud|5.00|1_0": [{"time": 30, "avgP": 150, "date": 10, "user": "Oud"}]}, f)
        for target, name, value in ((state, "RIDE_DIR", ride), (state, "ROUTE_DIR", routes),
                                    (state, "KOM_FILE", self.kom_file), (storage, "KOM_FILE", self.kom_file)):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def request(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            raw = json.dumps(body).encode() if body is not None else None
            headers = {"Content-Type": "application/json"} if raw is not None else {}
            conn.request(method, path, raw, headers)
            response = conn.getresponse()
            payload = response.read()
            if path.startswith("/koms"):
                return response.status, json.loads(payload)
            return response.status, payload
        finally:
            conn.close()

    def test_record_keeps_leaderboard_and_archives_everything(self):
        key = "kom_Officieel|1.88|0_0"
        for n in range(21):
            status, payload = self.request("POST", "/koms/record", {
                "key": key,
                "entry": {"time": 50 + n, "avgP": 210, "date": 100 + n, "user": "Hans"},
                "archive": {"route_id": "official", "route_name": "Officieel", "route_km": 1.875,
                            "route_seed": 0, "segment_index": 0, "segment_name": "Klim",
                            "simulated": False, "erg": n == 1, "paused": False},
            })
            self.assertEqual(status, 200)
            self.assertTrue(payload["ok"])
            self.assertNotIn("attempts", payload)
        board = payload["koms"][key]
        self.assertEqual(len(board), 20)
        self.assertEqual(board[0]["time"], 50)
        self.assertNotIn(70, [row["time"] for row in board])
        self.assertEqual(set(board[0]), {"time", "avgP", "date", "user"})
        status, archive = self.request("GET", "/koms/archief")
        self.assertEqual(status, 200)
        times = sorted(row["time"] for row in archive["attempts"] if row["route_id"] == "official")
        self.assertEqual(times, list(range(50, 71)))
        self.assertTrue(any(row["legacy"] and row["route_name"] == "Oud" and row["revision"] is None
                            for row in archive["attempts"]))
        recorded = [row for row in archive["attempts"] if row["time"] == 51][0]
        self.assertEqual(recorded["revision"], self.revision)
        self.assertIs(recorded["erg"], True)
        status, cleared = self.request("POST", "/koms/clear", {"prefix": "kom_Officieel|1.88|0_"})
        self.assertEqual(status, 200)
        self.assertNotIn(key, cleared["koms"])
        self.assertIn("kom_Oud|5.00|1_0", cleared["koms"])
        _, archive = self.request("GET", "/koms/archief")
        self.assertEqual(len([row for row in archive["attempts"] if row["route_id"] == "official"]), 21)
        status, rejected = self.request("POST", "/koms/record", {"key": "nope", "entry": {"time": 1}})
        self.assertEqual(status, 400)
        self.assertFalse(rejected["ok"])

    def test_route_results_stay_out_of_the_segment_leaderboard(self):
        status, first = self.request("POST", "/koms/route", {
            "entry": {"time": 1800, "avgP": 210, "date": 5000, "user": "Hans"},
            "archive": {"route_id": "official", "route_name": "Officieel", "route_km": 1.875,
                        "route_seed": 0, "simulated": False, "erg": False, "paused": True},
        })
        self.assertEqual(status, 200)
        self.assertTrue(first["ok"])
        self.assertNotIn("koms", first)
        status, second = self.request("POST", "/koms/route", {
            "entry": {"time": 0, "avgP": 1, "date": 1, "user": "Hans"},
            "archive": {"route_id": "official", "route_name": "Officieel"},
        })
        self.assertEqual(second["attempts"], first["attempts"])
        status, archive = self.request("GET", "/koms/archief")
        routes = [row for row in archive["attempts"] if row["kind"] == "route"]
        self.assertEqual([row["time"] for row in routes], [1800])
        self.assertEqual(routes[0]["revision"], self.revision)
        self.assertIsNone(routes[0]["segment"])
        self.assertIs(routes[0]["paused"], True)
        status, imported = self.request("POST", "/koms/route-import", {
            "prs": {"pr_Oud|5.00|1": {"time": 44, "avg": 150, "date": 10, "np": 160}},
            "ghosts": {"ghost_Oud|5.00|1": {"time": 44.2, "pts": [[0, 0]], "avgP": 999}},
        })
        self.assertEqual(imported["imported"], 1)
        _, archive = self.request("GET", "/koms/archief")
        legacy = [row for row in archive["attempts"] if row["kind"] == "route" and row["legacy"]]
        self.assertEqual(len(legacy), 1)
        self.assertEqual(legacy[0]["avgP"], 150)
        self.assertIsNone(legacy[0]["user"])
        self.assertIsNone(legacy[0]["revision"])
        with open(self.kom_file, encoding="utf-8") as f:
            board = json.load(f)
        self.assertNotIn("route_Officieel|1.88|0", board)

    def test_pages_serve_archive_and_profile(self):
        status, page = self.request("GET", "/klassementen")
        self.assertEqual(status, 200)
        text = page.decode()
        self.assertIn("Mijn KOM's", text)
        self.assertIn("Hele route", text)
        self.assertIn('id="tabGeneral"', text)
        self.assertIn('id="tabFriends"', text)
        self.assertIn("komen niet terug", text)
        self.assertIn("/klassementen/profiel", text)
        status, profile = self.request("GET", "/klassementen/profiel")
        self.assertEqual(status, 200)
        self.assertIn("Jouw rijdersprofiel", profile.decode())
        status, home = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(">Klassementen<", home.decode())
