"""Count SELECT presses once per press."""

import os
import sys


APP_DIR = "/system/apps/select_counter"
os.chdir(APP_DIR)
sys.path.insert(0, APP_DIR)

count = 0


def centered(text, y, size=1):
    width, _ = screen.measure_text(text, font_size=size)
    screen.text(text, (screen.width - width) // 2, y, size)


def update():
    global count

    if badge.pressed(BUTTON_SELECT):
        count += 1

    screen.pen = color.rgb(13, 17, 23)
    screen.rectangle(screen.clip)

    screen.font = font.nope
    screen.pen = color.rgb(255, 255, 255)
    centered("SELECT Counter", 12)

    screen.pen = color.rgb(211, 250, 55)
    value = str(count)
    width, _ = screen.measure_text(value)
    size = max(1, min(4, (screen.width - 16) // max(1, width)))
    _, height = screen.measure_text(value, font_size=size)
    centered(value, (screen.height - height) // 2, size)

    screen.font = font.nope
    screen.pen = color.rgb(255, 255, 255)
    centered("SELECT: +1", screen.height - 20)


run(update)
