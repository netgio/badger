"""Host-side checks for the SELECT counter's frame callback."""

from pathlib import Path
import types
import unittest
from unittest.mock import Mock, patch


APP = Path(__file__).resolve().parents[1] / "badge/apps/select_counter/__init__.py"


class SelectCounterTests(unittest.TestCase):
    def load_app(self):
        screen = Mock(width=160, height=120, clip=(0, 0, 160, 120))
        screen.measure_text.side_effect = lambda text, font_size=1: (
            len(text) * 6 * font_size, 8 * font_size
        )
        namespace = {
            "badge": Mock(),
            "screen": screen,
            "font": types.SimpleNamespace(nope="nope", sins="sins"),
            "color": types.SimpleNamespace(rgb=lambda *rgb: rgb),
            "BUTTON_SELECT": "select",
            "run": Mock(),
        }
        with patch("os.chdir"), patch("sys.path", []):
            exec(compile(APP.read_text(), str(APP), "exec"), namespace)
        namespace["run"].assert_called_once_with(namespace["update"])
        return namespace

    def test_starts_at_zero_and_draws_the_count(self):
        app = self.load_app()
        app["badge"].pressed.return_value = False
        app["update"]()
        self.assertEqual(app["count"], 0)
        app["screen"].text.assert_any_call("0", 68, 44, 4)

    def test_increments_only_on_select_press_edges(self):
        app = self.load_app()
        app["badge"].pressed.side_effect = [True, False, False, True, False]
        expected = [1, 1, 1, 2, 2]
        for count in expected:
            app["update"]()
            self.assertEqual(app["count"], count)
        self.assertEqual(
            app["badge"].pressed.call_args_list,
            [unittest.mock.call("select")] * len(expected),
        )
        app["badge"].held.assert_not_called()

    def test_reopening_starts_a_new_count(self):
        app = self.load_app()
        app["badge"].pressed.return_value = True
        app["update"]()
        self.assertEqual(app["count"], 1)
        self.assertEqual(self.load_app()["count"], 0)


if __name__ == "__main__":
    unittest.main()
