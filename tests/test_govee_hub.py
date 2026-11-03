"""Focused H5075 decoder, log recovery, retention and clock tests."""

import importlib.util
import json
import os
import sys
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

APP = Path(__file__).resolve().parents[1] / "badge/apps/govee_hub"


def load(name):
    spec = importlib.util.spec_from_file_location("govee_" + name, APP / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


protocol = load("protocol")
history = load("history")
clock = load("clock")
views = load("views")


def advertisement(temp=235, humid=456, battery=87, error=False):
    packed = abs(temp) * 1000 + humid
    if temp < 0:
        packed |= 0x800000
    value = bytes((0x88, 0xec, 0, packed >> 16, (packed >> 8) & 255,
                   packed & 255, battery | (128 if error else 0), 0))
    return bytes((len(value) + 1, 255)) + value


class ProtocolTests(unittest.TestCase):
    def test_manager_session_acquired_and_released_without_direct_irq_override(self):
        ble = Mock()
        manager = Mock(in_session=False)
        manager.ble.return_value = ble
        scanner = protocol.Scanner(ble, lambda: 0, manager)
        manager.open.assert_called_once_with(scanner.irq)
        ble.active.assert_not_called()
        ble.irq.assert_not_called()
        scanner.close()
        manager.close.assert_called_once()
        self.assertFalse(scanner.owned)

    def test_positive_negative_battery_and_error(self):
        for temp in (235, -123, 0):
            self.assertEqual(protocol.decode(advertisement(temp))[1],
                             (temp, 456, 87, False))
        self.assertTrue(protocol.decode(advertisement(error=True))[1][3])
        self.assertTrue(protocol.decode(advertisement(battery=101))[1][3])

    def test_truncated_fields_and_wrong_company(self):
        self.assertEqual(protocol.decode(b"\x08\xff\x88"), (None, None))
        payload = bytearray(advertisement())
        payload[2] = 0
        self.assertEqual(protocol.decode(payload), (None, None))

    def test_scanner_bounds_and_cleanup(self):
        ble = Mock()
        ble.active.return_value = False
        scanner = protocol.Scanner(ble, lambda: 0)
        payload = b"\x0d\x09Govee_H5075_X" + advertisement()
        for _ in range(70):
            scanner.irq(5, (0, b"\x01\x02\x03\x04\x05\x06", 0, -50, payload))
        self.assertEqual(len(scanner.queue), 64)
        self.assertEqual(scanner.dropped, 6)
        scanner.poll(0)
        self.assertEqual(len(scanner.devices), 1)
        ble.active.return_value = True
        scanner.close()
        ble.gap_scan.assert_any_call(None)
        ble.active.assert_called_with(False)

    def test_cleanup_when_firmware_has_already_disabled_ble(self):
        ble = Mock()
        ble.active.return_value = False
        scanner = protocol.Scanner(ble, lambda: 0)
        scanner.close()
        ble.gap_scan.assert_not_called()
        ble.irq.assert_called_with(None)
        self.assertFalse(scanner.owned)

    def test_cleanup_radio_shutdown_race_only_suppresses_confirmed_enodev(self):
        ble = Mock()
        ble.active.side_effect = [False, True, False, False]
        scanner = protocol.Scanner(ble, lambda: 0)
        ble.gap_scan.side_effect = OSError(19)
        scanner.close()
        self.assertFalse(scanner.owned)
        ble.irq.assert_called_with(None)
        ble = Mock()
        ble.active.return_value = False
        scanner = protocol.Scanner(ble, lambda: 0)
        ble.active.return_value = True
        ble.gap_scan.side_effect = OSError(5)
        with self.assertRaises(OSError):
            scanner.close()
        self.assertFalse(scanner.owned)


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = self.temp.name
        self.store = history.History(self.root)
        self.now = 1793456000
        self.reading = (235, 456, 87, False)

    def tearDown(self):
        self.temp.cleanup()

    def test_one_record_per_five_minute_bucket(self):
        now = self.now // 300 * 300
        self.assertTrue(self.store.append(0, now, self.reading))
        self.assertFalse(self.store.append(0, now + 299, self.reading))
        self.assertTrue(self.store.append(0, now + 300, self.reading))
        reopened = history.History(self.root)
        self.assertEqual(reopened.last[0], now + 300)
        self.assertFalse(reopened.append(0, now + 301, self.reading))

    def test_error_readings_not_logged(self):
        self.assertFalse(self.store.append(0, self.now, (0, 0, 0, True)))
        self.assertEqual(list(self.store.logs()), [])

    def test_partial_and_corrupt_tail_repaired(self):
        self.store.append(0, self.now, self.reading)
        path = list(self.store.logs())[0][2]
        with open(path, "ab") as dest:
            dest.write(b"\x00\x00\x00")
        reopened = history.History(self.root)
        self.assertIn("Recovered", reopened.warning)
        self.assertEqual(os.stat(path).st_size, history.SIZE)
        reopened.append(0, self.now + 300, self.reading)
        with open(path, "ab") as dest:
            damaged = bytearray(history.encode(self.now + 600, 235, 456, 87))
            damaged[-1] ^= 1
            dest.write(damaged)
        reopened = history.History(self.root)
        self.assertEqual(os.stat(path).st_size, 2 * history.SIZE)

    def test_replace_recovers_backup_after_interrupted_rename(self):
        self.store.pair(0, "0:010203040506")
        path = self.root + "/config.json"
        os.rename(path, path + ".bak")
        with open(path + ".tmp", "w") as dest:
            dest.write("{")
        reopened = history.History(self.root)
        self.assertEqual(reopened.config["sensors"][0], "0:010203040506")

    def test_old_days_pruned_and_exact_cutoff_excluded(self):
        day = self.now // 86400 * 86400
        now = day + 43200
        old = now - 30 * 86400
        self.store.append(0, old - 86400, self.reading)
        self.store.append(0, old - 300, self.reading)
        self.store.append(0, old + 300, self.reading)
        self.store.prune(now)
        self.assertEqual(len(list(self.store.logs())), 1)
        values, low, high, count = self.store.graph(0, now, 30 * 86400, 1)
        self.assertEqual(count, 1)
        self.assertEqual((low, high), (235, 235))
        self.assertIn(None, values)

    def test_shared_storage_reserve_is_enforced(self):
        with patch.object(history.os, "statvfs",
                          return_value=(4096, 4096, 256, 1, 1)):
            with self.assertRaises(OSError):
                self.store.append(0, self.now, self.reading)

    def test_duplicate_or_invalid_pairing_rejected(self):
        self.store.pair(0, "0:010203040506")
        with self.assertRaises(ValueError):
            self.store.pair(1, "0:010203040506")
        with self.assertRaises(ValueError):
            self.store.pair(0, "0:060504030201")
        with self.assertRaises(ValueError):
            self.store.pair(1, "../../wrong")

    def test_thirty_days_of_three_sensors_are_bounded_and_streamed(self):
        start = self.now // 86400 * 86400 - 30 * 86400
        for slot in range(3):
            for day in range(30):
                midnight = start + day * 86400
                path = "%s/%d-%d.bin" % (self.root, slot, midnight // 86400)
                with open(path, "wb") as dest:
                    for index in range(288):
                        dest.write(history.encode(midnight + index * 300,
                                                  235, 456, 87))
        self.assertEqual(sum(os.stat(path)[6] for _, _, path in self.store.logs()),
                         259200)
        reopened = history.History(self.root)
        values, low, high, total = reopened.graph(2, start + 30 * 86400,
                                                 30 * 86400, 2)
        self.assertEqual(total, 8640)
        self.assertEqual(len(values), 80)
        self.assertEqual((low, high), (456, 456))
        self.assertTrue(all(value == 456 for value in values))


class ClockTests(unittest.TestCase):
    def test_untrusted_rtc_not_used_and_network_failure_is_visible(self):
        wifi = Mock()
        wifi.connect.side_effect = OSError("unavailable")
        instance = clock.Clock(lambda: 0, wifi, Mock())
        instance.poll(0)
        self.assertIsNone(instance.now(1000))
        self.assertIn("failed", instance.message)
        self.assertEqual(instance.next_try, 60000)

    def test_ntp_anchor_uses_monotonic_elapsed_across_rtc_changes(self):
        wifi = Mock()
        wifi.connect.return_value = True
        instance = clock.Clock(lambda: 500, wifi, Mock())
        with patch.object(clock.time, "time", return_value=1793456000), \
                patch.object(clock.time, "gmtime", return_value=(2026,)):
            instance.poll(0)
        self.assertEqual(instance.now(30 * 86400000 + 500),
                         1793456000 + 30 * 86400)
        instance.close()
        wifi.disconnect.assert_called_once()


class ViewTests(unittest.TestCase):
    def test_shared_scale_covers_all_values_with_margin(self):
        low, high = views.scale([182, 251, 214], 10, 40, 10)
        self.assertLess(low, 182)
        self.assertGreater(high, 251)
        self.assertEqual((low % 10, high % 10), (0, 0))
        self.assertEqual(views.scale([], 10, 40, 10), None)
        self.assertEqual(views.scale([None], 10, 40, 10), None)

    def test_scale_handles_negative_identical_and_clamped_values(self):
        low, high = views.scale([-125, -125], 10, 40, 10)
        self.assertLess(low, -125)
        self.assertGreater(high, -125)
        self.assertGreaterEqual(high - low, 40)
        self.assertEqual(views.scale([995, 1000], 30, 100, 50, 0, 1000)[1], 1000)
        low, high = views.scale([10, 20], 30, 100, 50, 0, 1000)
        self.assertEqual(low, 0)
        self.assertGreaterEqual(high - low, 100)

    def test_fill_is_bounded(self):
        self.assertEqual(views.fill(150, 100, 200, 156), 78)
        self.assertEqual(views.fill(50, 100, 200, 156), 0)
        self.assertEqual(views.fill(250, 100, 200, 156), 156)
        self.assertEqual(views.fill(5, 5, 5, 156), 0)

    def test_runs_keep_gaps_blank(self):
        self.assertEqual(views.runs([None, 1, 2, None, None, 5, None]),
                         [[(1, 1), (2, 2)], [(5, 5)]])
        self.assertEqual(views.runs([None, None]), [])
        self.assertEqual(views.runs([0, -10]), [[(0, 0), (1, -10)]])

    def make_app(self, store, now_stamp, devices=None):
        scan = Mock(dropped=0)
        scan.devices = devices or {}
        session_clock = Mock(message="")
        session_clock.now.return_value = now_stamp
        ticks = {"ms": 0}
        fake_time = types.SimpleNamespace(ticks_ms=lambda: ticks["ms"],
                                         ticks_diff=lambda a, b: a - b,
                                         gmtime=lambda s: (2026, 10, 31, 12, 0))
        modules = {
            "protocol": types.SimpleNamespace(Scanner=Mock(return_value=scan)),
            "history": types.SimpleNamespace(History=Mock(return_value=store)),
            "clock": types.SimpleNamespace(Clock=Mock(return_value=session_clock)),
            "views": views, "bluetooth": types.SimpleNamespace(BLE=Mock()),
            "badgeware": types.SimpleNamespace(radio=types.SimpleNamespace(radio=Mock())),
            "wifi": Mock(), "ntptime": Mock(), "time": fake_time,
        }
        screen = Mock(width=320, height=240)
        screen.measure_text.return_value = (40, 12)
        namespace = {
            "badge": Mock(ticks=0, **{"battery_level.return_value": 64,
                                      "is_charging.return_value": False}),
            "screen": screen,
            "font": types.SimpleNamespace(nope=1), "shape": Mock(),
            "color": types.SimpleNamespace(rgb=lambda *v: v), "HIRES": 1, "VSYNC": 2,
            "run": Mock(),
        }
        for button in ("BACK", "UP", "DOWN", "SELECT", "MENU", "LEFT", "RIGHT"):
            namespace["BUTTON_" + button] = button
        namespace["badge"].pressed.return_value = False
        with patch.dict(sys.modules, modules), patch("os.chdir"), \
                patch("sys.path", []):
            exec(compile((APP / "__init__.py").read_text(),
                         str(APP / "__init__.py"), "exec"), namespace)
        namespace["report_error"] = Mock()
        namespace["view"] = 4
        return namespace, screen, ticks

    def test_trend_plots_each_paired_sensor_and_leaves_gaps(self):
        bins = [None] * 90
        bins[10:13] = [200, 210, 220]
        bins[50] = -40
        store = Mock(config={"sensors": ["a", None, "c"]}, warning="", last=[0])
        store.graph.return_value = (bins, -40, 220, 4)
        app, screen, ticks = self.make_app(store, 1_800_000_000)
        app["update"]()
        self.assertEqual([c.args for c in store.graph.call_args_list],
                         [(0, 1_800_000_000, 86400, 1, 90),
                          (2, 1_800_000_000, 86400, 1, 90)])
        # Two runs per sensor: one 3-point run drawn as two doubled segments,
        # and an isolated point drawn as a marker, never bridged across the gap.
        self.assertEqual(screen.line.call_count, 2 * 2 * 2)
        texts = " ".join(str(c.args[0]) for c in screen.text.call_args_list)
        self.assertIn("S2 Not paired", texts)
        self.assertNotIn("No saved readings", texts)
        store.graph.reset_mock()
        ticks["ms"] = 59_000
        app["update"]()
        store.graph.assert_not_called()
        ticks["ms"] = 61_000
        app["update"]()
        self.assertEqual(store.graph.call_count, 2)

    def test_trend_without_time_or_history_is_explicit(self):
        store = Mock(config={"sensors": ["a", "b", "c"]}, warning="", last=[0])
        store.graph.return_value = ([None] * 90, None, None, 0)
        app, screen, _ = self.make_app(store, None)
        app["update"]()
        store.graph.assert_not_called()
        texts = " ".join(str(c.args[0]) for c in screen.text.call_args_list)
        self.assertIn("needs WiFi time", texts)
        app, screen, _ = self.make_app(store, 1_800_000_000)
        app["update"]()
        texts = " ".join(str(c.args[0]) for c in screen.text.call_args_list)
        self.assertIn("No saved readings in the last 24 hours", texts)
        screen.line.assert_not_called()

    def test_trend_read_error_is_reported_and_not_retried_every_frame(self):
        store = Mock(config={"sensors": ["a", None, None]}, warning="", last=[0])
        store.graph.side_effect = OSError(5)
        app, screen, _ = self.make_app(store, 1_800_000_000)
        app["update"]()
        app["update"]()
        store.graph.assert_called_once()
        app["report_error"].assert_called_once()
        texts = " ".join(str(c.args[0]) for c in screen.text.call_args_list)
        self.assertIn("History read failed; retry in 60s", texts)

    def header(self, screen):
        return [str(c.args[0]) for c in screen.text.call_args_list]

    def test_badge_battery_in_header_is_throttled(self):
        store = Mock(config={"sensors": [None, None, None]}, warning="", last=[0])
        app, screen, ticks = self.make_app(store, None)
        app["view"] = 0
        app["update"]()
        self.assertIn("64%", self.header(screen))
        app["badge"].battery_level.return_value = 12
        ticks["ms"] = 4_000
        app["update"]()
        self.assertEqual(app["badge"].battery_level.call_count, 1)
        ticks["ms"] = 5_000
        drawn = []
        screen.text.side_effect = lambda *a: drawn.append((a[0], screen.pen))
        app["update"]()
        self.assertEqual(app["badge"].battery_level.call_count, 2)
        self.assertIn(("12%", (255, 130, 130)), drawn)

    def test_badge_battery_charging_and_unavailable(self):
        store = Mock(config={"sensors": [None, None, None]}, warning="", last=[0])
        app, screen, _ = self.make_app(store, None)
        app["badge"].is_charging.return_value = True
        app["update"]()
        self.assertIn("CHG", self.header(screen))
        self.assertNotIn("64%", self.header(screen))
        for failure in (AttributeError("battery_level"), OSError(19)):
            app, screen, _ = self.make_app(store, None)
            app["badge"].battery_level.side_effect = failure
            app["update"]()
            self.assertIn("--", self.header(screen))
            self.assertEqual(app["battery"], (False, None))
        app, screen, _ = self.make_app(store, None)
        app["badge"].battery_level.return_value = None
        app["update"]()
        self.assertIn("--", self.header(screen))
        app, screen, _ = self.make_app(store, None)
        app["badge"].battery_level.return_value = 140
        app["update"]()
        self.assertIn("100%", self.header(screen))

    def test_temperature_bands(self):
        names = [views.BANDS[views.band(t)][1] for t in (-200, -1, 0, 159, 160,
                                                         239, 240, 299, 300, 450)]
        self.assertEqual(names, ["Freezing", "Freezing", "Chilly", "Chilly",
                                 "Comfy", "Comfy", "Warm", "Warm",
                                 "Scorching", "Scorching"])

    def test_left_right_cycles_every_view_and_each_renders(self):
        scan = Mock(dropped=0)
        scan.devices = {
            "a": {"seen": 0, "reading": (-55, 410, 90, False)},
            "b": {"seen": 0, "reading": (215, 980, 50, False)},
        }
        store = Mock(config={"sensors": ["a", "b", None]}, warning="", last=[0])
        session_clock = Mock(message="Waiting for time")
        session_clock.now.return_value = None
        fake_time = types.SimpleNamespace(ticks_ms=lambda: 0,
                                         ticks_diff=lambda a, b: a - b)
        modules = {
            "protocol": types.SimpleNamespace(Scanner=Mock(return_value=scan)),
            "history": types.SimpleNamespace(History=Mock(return_value=store)),
            "clock": types.SimpleNamespace(Clock=Mock(return_value=session_clock)),
            "views": views, "bluetooth": types.SimpleNamespace(BLE=Mock()),
            "badgeware": types.SimpleNamespace(radio=types.SimpleNamespace(radio=Mock())),
            "wifi": Mock(), "ntptime": Mock(), "time": fake_time,
        }
        screen = Mock(width=320, height=240)
        screen.measure_text.return_value = (40, 12)
        namespace = {
            "badge": Mock(ticks=12345, **{"battery_level.return_value": 64,
                                          "is_charging.return_value": False}),
            "screen": screen,
            "font": types.SimpleNamespace(nope=1), "shape": Mock(),
            "color": types.SimpleNamespace(rgb=lambda *v: v), "HIRES": 1, "VSYNC": 2,
            "run": Mock(),
        }
        for button in ("BACK", "UP", "DOWN", "SELECT", "MENU", "LEFT", "RIGHT"):
            namespace["BUTTON_" + button] = button
        with patch.dict(sys.modules, modules), patch("os.chdir"), \
                patch("sys.path", []):
            exec(compile((APP / "__init__.py").read_text(),
                         str(APP / "__init__.py"), "exec"), namespace)
        pressed = {"button": "RIGHT"}
        namespace["badge"].pressed.side_effect = lambda b: b == pressed["button"]
        seen = []
        for _ in range(len(views.MODES)):
            namespace["update"]()
            seen.append(namespace["view"])
            self.assertEqual(namespace["page"], "live")
        self.assertEqual(seen, [1, 2, 3, 4, 0])
        pressed["button"] = "LEFT"
        namespace["update"]()
        self.assertEqual(namespace["view"], 4)
        self.assertEqual(namespace["slot"], 0)
        texts = " ".join(str(c.args[0]) for c in screen.text.call_args_list)
        for expected in ("-5.5", "21.5", "Not paired", "Freezing", "Comfy"):
            self.assertIn(expected, texts)


class LifecycleTests(unittest.TestCase):
    def test_scanner_error_keeps_controls_and_rendering_responsive(self):
        scan = Mock()
        scan.poll.side_effect = OSError(19)
        scan.dropped = 0
        scan.devices = {}
        store = Mock(config={"sensors": [None, None, None]}, warning="")
        session_clock = Mock(message="Waiting for time")
        session_clock.now.return_value = None
        fake_time = types.SimpleNamespace(ticks_ms=lambda: 0,
                                         ticks_diff=lambda a, b: a - b)
        modules = {
            "protocol": types.SimpleNamespace(Scanner=Mock(return_value=scan)),
            "history": types.SimpleNamespace(History=Mock(return_value=store)),
            "clock": types.SimpleNamespace(Clock=Mock(return_value=session_clock)),
            "views": views,
            "bluetooth": types.SimpleNamespace(BLE=Mock()),
            "badgeware": types.SimpleNamespace(radio=types.SimpleNamespace(radio=Mock())),
            "wifi": Mock(), "ntptime": Mock(), "time": fake_time,
        }
        namespace = {
            "badge": Mock(), "screen": Mock(width=320, height=240),
            "font": types.SimpleNamespace(nope=1),
            "color": types.SimpleNamespace(rgb=lambda *v: v), "HIRES": 1, "VSYNC": 2,
            "run": Mock(),
        }
        for button in ("BACK", "UP", "DOWN", "SELECT", "MENU", "LEFT", "RIGHT"):
            namespace["BUTTON_" + button] = button
        namespace["badge"].pressed.side_effect = lambda button: button == "DOWN"
        namespace["screen"].measure_text.return_value = (40, 12)
        namespace["badge"].battery_level.return_value = 80
        namespace["badge"].is_charging.return_value = False
        with patch.dict(sys.modules, modules), patch("os.chdir"), \
                patch("sys.path", []):
            exec(compile((APP / "__init__.py").read_text(),
                         str(APP / "__init__.py"), "exec"), namespace)
        namespace["report_error"] = Mock()
        namespace["update"]()
        self.assertEqual(namespace["slot"], 1)
        self.assertEqual(namespace["scanner_retry"], 60000)
        self.assertTrue(namespace["recording_enabled"])
        self.assertIn("BLE scan failed", namespace["scanner_fault"])
        namespace["screen"].text.assert_called()
        namespace["update"]()
        self.assertEqual(namespace["slot"], 2)
        scan.poll.assert_called_once()

    def test_home_exception_always_closes_ble_and_wifi(self):
        scan = Mock()
        session_clock = Mock()
        fake_time = types.SimpleNamespace(ticks_ms=lambda: 0)
        modules = {
            "protocol": types.SimpleNamespace(Scanner=Mock(return_value=scan)),
            "history": types.SimpleNamespace(History=Mock(return_value=Mock())),
            "clock": types.SimpleNamespace(Clock=Mock(return_value=session_clock)),
            "views": views,
            "bluetooth": types.SimpleNamespace(BLE=Mock()),
            "wifi": Mock(), "ntptime": Mock(), "time": fake_time,
            "badgeware": types.SimpleNamespace(radio=types.SimpleNamespace(radio=Mock())),
        }
        namespace = {
            "badge": Mock(), "screen": Mock(), "font": types.SimpleNamespace(nope=1),
            "color": types.SimpleNamespace(rgb=lambda *v: v), "HIRES": 1, "VSYNC": 2,
            "run": Mock(side_effect=RuntimeError("HOME exit")),
        }
        with patch.dict(sys.modules, modules), patch("os.chdir"), \
                patch("sys.path", []):
            with self.assertRaisesRegex(RuntimeError, "HOME exit"):
                exec(compile((APP / "__init__.py").read_text(),
                             str(APP / "__init__.py"), "exec"), namespace)
        scan.close.assert_called_once()
        session_clock.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
