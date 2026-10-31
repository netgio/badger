"""Three H5075 thermometers, live BLE readings and bounded local history."""

import os
import sys
import time

APP_DIR = "/system/apps/govee_hub"
os.chdir(APP_DIR)
sys.path.insert(0, APP_DIR)

from protocol import Scanner
from history import History
from clock import Clock
import views

try:
    import bluetooth
except ImportError:
    bluetooth = None
try:
    import wifi
    import ntptime
except ImportError:
    wifi = ntptime = None

if sys.platform == "webassembly":
    bluetooth = wifi = ntptime = None

firmware_radio = None
if bluetooth:
    from badgeware import radio as firmware_radio

badge.mode(HIRES | VSYNC)
screen.font = font.nope
BG = color.rgb(13, 17, 23)
WHITE = color.rgb(235, 245, 255)
GREEN = color.rgb(211, 250, 55)
GRAY = color.rgb(137, 150, 165)
RED = color.rgb(255, 130, 130)
PANEL = color.rgb(36, 47, 58)
TRACK = color.rgb(28, 36, 45)
TEMP_PEN = color.rgb(255, 160, 70)
HUMID_PEN = color.rgb(90, 170, 255)
BLACK = color.rgb(10, 10, 14)
BAND_PENS = (color.rgb(150, 210, 255), color.rgb(120, 170, 255), GREEN,
             color.rgb(255, 190, 60), color.rgb(255, 90, 70))
RANGES = (86400, 7 * 86400, 30 * 86400)
RANGE_LABELS = ("24 hours", "7 days", "30 days")

history = None
scanner = None
fault = ""
try:
    history = History()
    scanner = (Scanner(bluetooth.BLE(), time.ticks_ms, firmware_radio.radio)
               if bluetooth else None)
except (OSError, ValueError, RuntimeError) as error:
    fault = str(error)
    print("Govee Hub: startup:", error)

clock = Clock(time.ticks_ms, wifi, ntptime)
slot = 0
page = "live"
view = 0
candidate_index = 0
range_index = 0
field = 1
graph_data = None
graph_dirty = True
last_graph = 0
last_log_bucket = None
storage_fault = ""
retry_storage = 0
recording_enabled = True
scanner_retry = 0
scanner_fault = ""
last_ticks = time.ticks_ms()
elapsed = 0


def monotonic():
    global last_ticks, elapsed
    current = time.ticks_ms()
    elapsed += time.ticks_diff(current, last_ticks)
    last_ticks = current
    return elapsed


clock.ticks = monotonic


def report_error(operation, error):
    print("Govee Hub:", operation)
    sys.print_exception(error)
    if history:
        try:
            name = operation.replace(".", "_").replace("/", "_")
            with open(history.root + "/" + name + "_error.txt", "w") as dest:
                dest.write(operation + "\n")
                sys.print_exception(error, dest)
        except OSError as diagnostic_error:
            print("Govee Hub: could not save diagnostic:", diagnostic_error)


def device_for(index):
    key = history.config["sensors"][index] if history else None
    if key is None:
        return None
    return scanner.devices.get(key) if scanner else None


def candidates():
    if scanner is None:
        return []
    return sorted(key for key in scanner.devices
                  if key not in history.config["sensors"])


def text(value, x, y, pen=WHITE, size=1):
    screen.pen = pen
    screen.text(str(value), x, y, size)


def decimal(value):
    return "%.1f" % (value / 10)


def controls(now):
    global slot, page, candidate_index, range_index, field, graph_dirty, view
    if badge.pressed(BUTTON_BACK):
        page = "live"
    if page == "live":
        if badge.pressed(BUTTON_LEFT):
            view = (view - 1) % len(views.MODES)
        if badge.pressed(BUTTON_RIGHT):
            view = (view + 1) % len(views.MODES)
        if badge.pressed(BUTTON_UP):
            slot = (slot - 1) % 3
        if badge.pressed(BUTTON_DOWN):
            slot = (slot + 1) % 3
        if badge.pressed(BUTTON_SELECT):
            page = "graph" if history.config["sensors"][slot] else "pair"
            graph_dirty = True
        if badge.pressed(BUTTON_MENU):
            page = "pair"
        return
    if page == "pair":
        items = candidates()
        if items:
            if badge.pressed(BUTTON_UP):
                candidate_index -= 1
            if badge.pressed(BUTTON_DOWN):
                candidate_index += 1
            candidate_index %= len(items)
            if badge.pressed(BUTTON_SELECT):
                history.pair(slot, items[candidate_index])
                page = "live"
        return
    if badge.pressed(BUTTON_LEFT):
        range_index = (range_index - 1) % 3
        graph_dirty = True
    if badge.pressed(BUTTON_RIGHT):
        range_index = (range_index + 1) % 3
        graph_dirty = True
    if badge.pressed(BUTTON_SELECT):
        field = 2 if field == 1 else 1
        graph_dirty = True


def status(index, now):
    """Return (state, reading, fresh) for one sensor slot."""
    if history.config["sensors"][index] is None:
        return "unpaired", None, False
    device = device_for(index)
    if device is None or device["seen"] is None:
        return "waiting", None, False
    if device["reading"][3]:
        return "error", None, False
    return "ok", device["reading"], now - device["seen"] <= 60000


def note(state):
    return {"unpaired": "Not paired", "waiting": "Waiting",
            "error": "Sensor error"}[state]


def right(value, x, y, pen=WHITE, size=1):
    width, _ = screen.measure_text(str(value), font_size=size)
    text(value, x - width, y, pen, size)


def box(x, y, w, h, pen):
    screen.pen = pen
    screen.rectangle(x, y, w, h)


def outline(x, y, w, h, pen):
    box(x, y, w, 2, pen)
    box(x, y + h - 2, w, 2, pen)
    box(x, y, 2, h, pen)
    box(x + w - 2, y, 2, h, pen)


def disc(x, y, r, pen):
    screen.pen = pen
    screen.shape(shape.circle(x, y, r))


def live(now):
    text("GOVEE HUB", 12, 10, GREEN, 2)
    text("< %s %d/%d >" % (views.MODES[view], view + 1, len(views.MODES)),
         196, 14, GRAY)
    if view == 1:
        bars_view(now)
    elif view == 2:
        big_view(now)
    elif view == 3:
        scene_view(now)
    else:
        list_view(now)
    overflow = ("BLE queue overflow: %d" % scanner.dropped
                if scanner and scanner.dropped else "")
    text((storage_fault or scanner_fault or overflow or history.warning or
          clock.message)[:48], 12, 207,
         RED if storage_fault or scanner_fault or overflow else GRAY)
    text("L/R view  UP/DOWN room  SELECT history", 12, 225, GRAY)


def bars_view(now):
    rows = [status(index, now) for index in range(3)]
    readings = [reading for _, reading, _ in rows if reading]
    temps = views.scale([r[0] for r in readings], 10, 40, 10)
    humids = views.scale([r[1] for r in readings], 30, 100, 50, 0, 1000)
    if temps:
        text("Temp %d..%d C   Humidity %d..%d %%" % (
            temps[0] // 10, temps[1] // 10, humids[0] // 10, humids[1] // 10),
            12, 36, GRAY)
    else:
        text("Shared scale appears with the first reading", 12, 36, GRAY)
    for index, (state, reading, fresh) in enumerate(rows):
        y = 56 + index * 48
        if index == slot:
            box(6, y - 3, screen.width - 12, 45, PANEL)
        text("Sensor %d" % (index + 1), 12, y + 4,
             GREEN if index == slot else WHITE)
        text("LIVE" if fresh else ("STALE" if reading else note(state)),
             12, y + 22, WHITE if fresh else (RED if state == "error" else GRAY))
        for row, (value, axis, pen, unit) in enumerate((
                (reading and reading[0], temps, TEMP_PEN, " C"),
                (reading and reading[1], humids, HUMID_PEN, " %"))):
            by = y + 2 + row * 20
            box(90, by, 156, 14, TRACK)
            if reading:
                box(90, by, views.fill(value, axis[0], axis[1], 156), 14,
                    pen if fresh else GRAY)
                text(decimal(value) + unit, 252, by + 1, WHITE if fresh else GRAY)


def big_view(now):
    text("Temperature C; humidity and battery below", 12, 36, GRAY)
    for index in range(3):
        state, reading, fresh = status(index, now)
        y = 56 + index * 48
        if index == slot:
            box(6, y - 3, screen.width - 12, 45, PANEL)
        text("Sensor %d" % (index + 1), 12, y + 4,
             GREEN if index == slot else WHITE)
        if reading is None:
            right(note(state), 304, y + 12, RED if state == "error" else GRAY, 2)
            continue
        text("%s %%  %d%% batt" % (decimal(reading[1]), reading[2]), 12, y + 22,
             GRAY)
        if not fresh:
            text("STALE", 120, y + 4, RED)
        right(decimal(reading[0]), 304, y + 4,
              BAND_PENS[views.band(reading[0])] if fresh else GRAY, 3)


def scene_view(now):
    text("Each room's mood follows its temperature", 12, 36, GRAY)
    tick = badge.ticks // 120
    for index in range(3):
        state, reading, fresh = status(index, now)
        x, y, w, h = 6 + index * 104, 52, 100, 150
        if reading is None or not fresh:
            box(x, y, w, h, TRACK)
            text("Sensor %d" % (index + 1), x + 6, y + 6, WHITE)
            text("?", x + 40, y + 50, GRAY, 3)
            text("STALE" if reading else note(state), x + 6, y + h - 20,
                 RED if state == "error" else GRAY)
        else:
            scene(views.band(reading[0]), x, y, w, h, tick + index * 7)
            box(x, y, w, 20, BLACK)
            text("S%d %s C" % (index + 1, decimal(reading[0])), x + 5, y + 4,
                 WHITE)
            box(x, y + h - 20, w, 20, BLACK)
            text(views.BANDS[views.band(reading[0])][1], x + 5, y + h - 16,
                 BAND_PENS[views.band(reading[0])])
        if index == slot:
            outline(x, y, w, h, GREEN)


def scene(level, x, y, w, h, tick):
    skies = ((70, 90, 130), (120, 140, 165), (110, 190, 245), (255, 200, 120),
             (200, 70, 40))
    grounds = ((240, 248, 255), (110, 95, 80), (70, 170, 70), (240, 210, 140),
               (150, 60, 30))
    box(x, y, w, h, color.rgb(*skies[level]))
    gy = y + h - 42
    box(x, gy, w, 22, color.rgb(*grounds[level]))
    cx = x + w // 2
    bob = (tick // 4) % 2
    if level == 0:
        for flake in range(6):
            fx = x + 8 + (flake * 37) % (w - 16)
            fy = y + 22 + (tick * 2 + flake * 23) % (gy - y - 22)
            disc(fx, fy, 2, WHITE)
        disc(cx, gy - 10, 14, WHITE)
        disc(cx, gy - 32, 10, WHITE)
        disc(cx - 4, gy - 34, 2, BLACK)
        disc(cx + 4, gy - 34, 2, BLACK)
        box(cx, gy - 31, 9, 3, color.rgb(255, 140, 40))
        disc(cx, gy - 14, 2, BLACK)
        disc(cx, gy - 6, 2, BLACK)
        return
    if level == 1:
        for cloud in (x + 22, x + 70):
            disc(cloud, y + 34, 9, color.rgb(225, 230, 238))
            disc(cloud + 10, y + 32, 11, color.rgb(225, 230, 238))
    if level in (2, 3):
        sun = 13 if level == 3 else 10
        disc(x + w - 20, y + 38, sun + 3 + bob, color.rgb(255, 245, 160))
        disc(x + w - 20, y + 38, sun, color.rgb(255, 225, 60))
    if level == 4:
        for wave in range(3):
            wy = y + 32 + wave * 14
            shift = (tick + wave * 3) % 6
            box(x + 10 + shift, wy, 22, 2, color.rgb(255, 180, 90))
            box(x + 58 - shift, wy + 6, 22, 2, color.rgb(255, 180, 90))
    if level == 2:
        for stem in (x + 14, x + w - 16):
            box(stem, gy - 8, 2, 8, color.rgb(40, 120, 40))
            disc(stem + 1, gy - 10, 4, color.rgb(255, 120, 180))
    bodies = ((0, 0, 0), (130, 170, 255), (120, 220, 120), (255, 170, 60),
              (255, 90, 70))
    body = color.rgb(*bodies[level])
    by = gy - 18 - (bob if level != 4 else 0)
    if level == 4:
        screen.pen = body
        screen.shape(shape.ellipse(cx, gy - 2, 24, 6))
    disc(cx, by, 18, body)
    disc(cx - 6, by - 4, 3, WHITE)
    disc(cx + 6, by - 4, 3, WHITE)
    disc(cx - 6, by - 4, 1, BLACK)
    disc(cx + 6, by - 4, 1, BLACK)
    if level == 1:
        box(cx - 14, by - 20, 28, 9, color.rgb(200, 50, 60))
        disc(cx, by - 23, 4, WHITE)
        box(cx - 15, by + 9, 30, 5, color.rgb(200, 50, 60))
        box(cx + 6, by + 9, 5, 14, color.rgb(200, 50, 60))
        box(cx - 4, by + 4, 8, 2, BLACK)
    elif level == 2:
        box(cx - 6, by + 5, 12, 2, BLACK)
        box(cx - 8, by + 3, 2, 2, BLACK)
        box(cx + 6, by + 3, 2, 2, BLACK)
    elif level == 3:
        box(cx - 12, by - 8, 10, 6, BLACK)
        box(cx + 2, by - 8, 10, 6, BLACK)
        box(cx - 2, by - 7, 4, 2, BLACK)
        box(cx - 6, by + 5, 12, 2, BLACK)
    else:
        disc(cx - 4, by + 7, 3, BLACK)
        for drop in range(2):
            dy = by - 10 + (tick + drop * 5) % 12
            disc(cx + 18 + drop * 4, dy, 2, color.rgb(120, 200, 255))


def list_view(now):
    text("30 days / 5-minute samples / UTC", 12, 36, GRAY)
    for index in range(3):
        y = 59 + index * 48
        screen.pen = color.rgb(36, 47, 58) if index == slot else BG
        screen.rectangle(6, y - 3, screen.width - 12, 45)
        key = history.config["sensors"][index]
        text("%s Sensor %d" % (">" if index == slot else " ", index + 1),
             12, y, GREEN if index == slot else WHITE)
        device = device_for(index)
        if key is None:
            text("SELECT to choose thermometer", 12, y + 16, GRAY)
        elif device is None or device["seen"] is None:
            text("Waiting for " + key[-4:].upper(), 12, y + 16, GRAY)
        elif device["reading"][3]:
            text("Sensor reports invalid reading", 12, y + 16, RED)
        else:
            temp, humid, battery, _ = device["reading"]
            age = max(0, (now - device["seen"]) // 1000)
            text("%s C   %s %%   %d%% batt" % (
                decimal(temp), decimal(humid), battery), 12, y + 16,
                WHITE if age <= 60 else GRAY)
            text("%s / %ds ago" % ("LIVE" if age <= 60 else "STALE", age),
                 210, y, WHITE if age <= 60 else RED)


def pairing():
    text("PAIR SENSOR %d" % (slot + 1), 12, 12, GREEN, 2)
    if history.config["sensors"][slot]:
        text("This slot is already paired.", 12, 53)
        text("BACK returns without changing it.", 12, 76, GRAY)
        return
    text("Put ONE unpaired H5075 nearby.", 12, 43)
    text("Match the last 4 digits in Govee Home.", 12, 59, GRAY)
    items = candidates()
    for index, key in enumerate(items):
        device = scanner.devices[key]
        temp = device["reading"]
        label = "%s %s %s" % (
            ">" if index == candidate_index % max(1, len(items)) else " ",
            key[-4:].upper(), decimal(temp[0]) + " C" if temp else "waiting")
        text(label, 16, 87 + index * 14, GREEN if index ==
             candidate_index % len(items) else WHITE)
    if not items:
        text("Scanning... close Govee Home.", 12, 99, GRAY)
    text("UP/DOWN choose  SELECT pair  BACK cancel", 12, 225, GRAY)


def graph(now):
    global graph_data, graph_dirty, last_graph
    stamp = clock.now(now)
    if stamp is None:
        text("History needs WiFi time", 12, 35, GREEN)
        text(clock.message[:46], 12, 60, GRAY)
        text("BACK: live readings", 12, 225, GRAY)
        return
    if graph_dirty or now - last_graph >= 60000:
        graph_data = history.graph(slot, stamp, RANGES[range_index], field)
        graph_dirty = False
        last_graph = now
    values, low, high, total = graph_data
    text("Sensor %d / %s" % (slot + 1, RANGE_LABELS[range_index]), 12, 12, GREEN, 2)
    text("Temperature C" if field == 1 else "Humidity %", 12, 40)
    text("%d real samples; gaps are blank" % total, 12, 59, GRAY)
    if total:
        text("Min %s  Max %s" % (decimal(low), decimal(high)), 12, 80)
        screen.pen = GRAY
        screen.line(15, 197, 305, 197)
        text(("-24h", "-7d", "-30d")[range_index], 16, 198, GRAY)
        text("now", 282, 198, GRAY)
        span = max(10, high - low)
        for index, value in enumerate(values):
            if value is None:
                continue
            x = 16 + index * 288 // len(values)
            y = int(190 - (value - low) * 85 / span)
            screen.pen = GREEN
            screen.rectangle(x, y, 3, 3)
    else:
        text("No saved readings in this window.", 12, 112, GRAY)
    text("UTC: " + "%04d-%02d-%02d %02d:%02d" % time.gmtime(stamp)[:5],
         12, 207, GRAY)
    text("L/R range  SELECT temp/humidity  BACK", 12, 225, GRAY)


def update():
    global storage_fault, last_log_bucket, graph_dirty, fault, retry_storage
    global recording_enabled
    global scanner_retry, scanner_fault
    now = monotonic()
    screen.pen = BG
    screen.rectangle(screen.clip)
    if fault:
        text("Govee Hub needs attention", 12, 30, RED)
        text(fault[:46], 12, 62)
        text("HOME: return to menu", 12, 225, GRAY)
        return
    operation = "clock.poll"
    try:
        clock.poll(now)
        now = monotonic()
    except (OSError, ValueError, RuntimeError) as error:
        report_error(operation, error)
        clock.message = "Time unavailable; live readings only"
        clock.next_try = now + 60000
    if scanner and now >= scanner_retry:
        try:
            scanner.poll(now, history.config["sensors"])
            scanner_fault = ""
        except (OSError, ValueError, RuntimeError) as error:
            scanner_fault = "BLE scan failed; retry in 60s"
            report_error("scanner.poll", error)
            scanner_retry = now + 60000
    operation = "controls/pairing"
    try:
        operation = "controls/pairing"
        controls(now)
        stamp = clock.now(now)
        if stamp is not None and recording_enabled:
            if stamp < max(history.last):
                storage_fault = "Clock precedes history; recording paused"
            else:
                bucket = stamp // 300
                if bucket != last_log_bucket:
                    operation = "history.prune"
                    history.prune(stamp)
                    last_log_bucket = bucket
                for index in range(3):
                    device = device_for(index)
                    if (device and device["seen"] is not None
                            and now - device["seen"] <= 60000):
                        operation = "history.append"
                        if (not device["reading"][3] and
                                history.append(index, stamp, device["reading"])):
                            graph_dirty = True
                storage_fault = ""
    except (OSError, ValueError, RuntimeError) as error:
        storage_fault = operation + ": " + str(error)
        report_error(operation, error)
        retry_storage = now + 60000
        recording_enabled = False
    try:
        if page == "pair":
            pairing()
        elif page == "graph" and now >= retry_storage:
            graph(now)
        else:
            live(now)
    except (OSError, ValueError) as error:
        storage_fault = str(error)
        report_error("history.graph/display", error)
        retry_storage = now + 60000
        text("History read failed; BACK for live", 12, 190, RED)
        text(storage_fault[:46], 12, 210, RED)


def on_exit():
    try:
        if scanner:
            try:
                scanner.close()
            except OSError as error:
                report_error("scanner.close", error)
                raise
    finally:
        try:
            clock.close()
        except OSError as error:
            report_error("clock.close", error)
            raise


try:
    run(update)
finally:
    on_exit()
