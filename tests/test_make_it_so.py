"""Host-side regression checks for Make It So scores and pause controls."""

import copy
from pathlib import Path
import types
import unittest
from unittest.mock import Mock, patch


APP = Path(__file__).resolve().parents[1] / "badge/apps/make_it_so/__init__.py"


class MakeItSoTests(unittest.TestCase):
    def setUp(self):
        self.saved = {}
        self.writes = []
        self.state = Mock()
        self.state.load.side_effect = lambda key, data: data.update(copy.deepcopy(self.saved))
        self.state.save.side_effect = self.save

    def save(self, key, data):
        self.assertEqual(key, "make_it_so")
        self.saved = copy.deepcopy(data)
        self.writes.append(copy.deepcopy(data))

    def load_app(self):
        badge = Mock(ticks=0, ticks_delta=16)
        badge.pressed.return_value = False
        badge.held.return_value = False
        badge.imu.return_value = (0, 0, 16384, 0, 0, 0)
        screen = Mock(width=320, height=240)
        screen.measure_text.side_effect = lambda text: (len(text) * 6, 8)
        namespace = {
            "badge": badge,
            "screen": screen,
            "image": types.SimpleNamespace(X2=2),
            "font": types.SimpleNamespace(smart="smart", sins="sins"),
            "color": types.SimpleNamespace(rgb=lambda *rgb: rgb),
            "shape": Mock(),
            "vec2": lambda x, y: (x, y),
            "HIRES": 1,
            "VSYNC": 2,
            "run": Mock(),
        }
        for action in ("UP", "DOWN", "LEFT", "RIGHT", "SELECT", "BACK", "MENU"):
            namespace["BUTTON_" + action] = action.lower()
        with patch("os.chdir"), patch.dict("sys.modules", {
            "badgeware": types.SimpleNamespace(State=self.state),
        }):
            exec(compile(APP.read_text(), str(APP), "exec"), namespace)
        namespace["run"].assert_called_once_with(namespace["update"])
        return namespace

    def collect(self, app, points=10):
        app["collect"](points, app["cx"], app["cy"], app["GOLD"])

    def press(self, app, action):
        app["badge"].pressed.side_effect = lambda button: button == action
        app["update"]()
        app["badge"].pressed.side_effect = None

    def test_empty_table_does_not_save_or_record_zero(self):
        app = self.load_app()
        self.assertEqual(app["scores"], {"best": 0, "high_scores": []})
        self.press(app, "menu")
        self.assertEqual(len([call for call in app["screen"].text.call_args_list
                              if call.args[0] == "-"]), 5)
        self.assertFalse(self.writes)

    def test_migrates_existing_best_without_duplicate_rows(self):
        self.saved = {"best": 50}
        app = self.load_app()
        self.assertEqual(app["scores"]["high_scores"], [50])
        self.collect(app, 25)
        self.collect(app, 25)
        self.collect(app, 25)
        self.assertEqual(app["scores"], {"best": 75, "high_scores": [75, 50]})

    def test_one_entry_per_flight_and_reopening_preserves_ties(self):
        app = self.load_app()
        self.collect(app)
        self.collect(app, 25)
        self.assertEqual(self.saved["high_scores"], [35])
        reopened = self.load_app()
        self.assertEqual(reopened["score"], 0)
        self.collect(reopened, 35)
        self.assertEqual(self.saved["high_scores"], [35, 35])
        self.collect(reopened)
        self.assertEqual(self.saved["high_scores"], [45, 35])

    def test_top_five_sorted_and_only_changed_table_is_saved(self):
        self.saved = {"best": 100, "high_scores": [50, 100, 60, 90, 80]}
        app = self.load_app()
        self.collect(app, 25)
        self.collect(app, 25)
        self.assertFalse(self.writes)
        self.collect(app)
        self.assertEqual(self.saved["high_scores"], [100, 90, 80, 60, 60])
        self.collect(app, 25)
        self.assertEqual(self.saved["high_scores"], [100, 90, 85, 80, 60])
        self.assertEqual(self.saved["best"], 100)
        self.assertEqual(len(self.writes), 2)

    def test_saved_data_is_sanitized_and_best_is_preserved(self):
        self.saved = {"best": 100, "high_scores": [20, None, "99", -1, 0, True, 50]}
        with patch("builtins.print") as log:
            app = self.load_app()
        self.assertEqual(app["scores"]["high_scores"], [100, 50, 20])
        log.assert_called_once()

    def test_invalid_state_types_and_best_recovery(self):
        for best, table, expected in (
            (True, None, []),
            (-10, "bad", []),
            ("100", [10, 30], [30, 10]),
            (0, [10, 20, 30, 40, 50, 60], [60, 50, 40, 30, 20]),
        ):
            with self.subTest(best=best, table=table), patch("builtins.print"):
                self.saved = {"best": best, "high_scores": table}
                app = self.load_app()
                self.assertEqual(app["scores"]["high_scores"], expected)
                self.assertEqual(app["scores"]["best"], expected[0] if expected else 0)

    def test_save_errors_are_not_silenced(self):
        app = self.load_app()
        self.state.save.side_effect = OSError("Storage unavailable")
        with self.assertRaises(OSError):
            self.collect(app)

    def test_table_pauses_motion_collection_and_timers(self):
        app = self.load_app()
        self.collect(app)
        app["badge"].ticks = 100
        app["stars"][0].z = 20
        before_z = app["stars"][0].z
        deadlines = [obj.next_ms for obj in [app["galaxy"]] + app["saucers"]]
        self.press(app, "menu")
        app["badge"].ticks = 10100
        app["badge"].held.return_value = True
        app["update"]()
        self.assertEqual(app["stars"][0].z, before_z)
        self.assertEqual(app["score"], 10)
        self.assertEqual(app["speed"], app["CRUISE"])
        self.assertEqual(len(self.writes), 1)
        app["badge"].held.return_value = False
        self.press(app, "back")
        self.assertIsNone(app["scores_opened"])
        self.assertEqual(app["started"], 10000)
        self.assertEqual(app["popups"][0][3], 10000)
        self.assertEqual([obj.next_ms for obj in [app["galaxy"]] + app["saucers"]],
                         [deadline + 10000 for deadline in deadlines])
        self.assertLess(app["stars"][0].z, before_z)

    def test_menu_toggles_and_back_only_recentres_in_flight(self):
        app = self.load_app()
        app["tilt"].recentre = Mock()
        self.press(app, "menu")
        self.assertEqual(app["scores_opened"], 0)
        self.press(app, "back")
        app["tilt"].recentre.assert_not_called()
        self.press(app, "back")
        app["tilt"].recentre.assert_called_once()
        self.press(app, "menu")
        self.press(app, "menu")
        self.assertIsNone(app["scores_opened"])

    def test_collection_values_and_warp_controls_are_unchanged(self):
        app = self.load_app()
        galaxy = app["galaxy"]
        galaxy.spawn()
        galaxy.x = galaxy.y = 0
        galaxy.z = app["CATCH_Z"]
        galaxy.update(app["CRUISE"], 0, 0, 0.016)
        self.assertEqual(app["score"], 10)
        saucer = app["saucers"][0]
        saucer.spawn()
        saucer.x = saucer.y = saucer.vx = 0
        saucer.z = app["CATCH_Z"]
        saucer.update(app["CRUISE"], 0, 0, 0.016)
        self.assertEqual(app["score"], 35)
        app["badge"].held.side_effect = lambda button: button == "select"
        app["update"]()
        self.assertGreater(app["speed"], app["CRUISE"])
        app["badge"].held.side_effect = None
        app["badge"].held.return_value = False
        speed = app["speed"]
        app["update"]()
        self.assertLess(app["speed"], speed)

    def test_table_draws_ranked_scores_and_flight_caption(self):
        self.saved = {"best": 50, "high_scores": [50, 25]}
        app = self.load_app()
        self.collect(app)
        self.press(app, "menu")
        labels = [call.args[0] for call in app["screen"].text.call_args_list]
        self.assertEqual(labels, [
            "High scores", "Flight 10 - paused",
            "1.", "50", "2.", "25", "3.", "10", "4.", "-", "5.", "-",
            "Menu / Back: resume",
        ])


if __name__ == "__main__":
    unittest.main()
