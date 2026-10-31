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
    global slot, page, candidate_index, range_index, field, graph_dirty
    if badge.pressed(BUTTON_BACK):
        page = "live"
    if page == "live":
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


def live(now):
    text("GOVEE HUB", 12, 10, GREEN, 2)
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
    overflow = ("BLE queue overflow: %d" % scanner.dropped
                if scanner and scanner.dropped else "")
    text((storage_fault or scanner_fault or overflow or history.warning or
          clock.message)[:48], 12, 207,
         RED if storage_fault or scanner_fault or overflow else GRAY)
    text("UP/DOWN room  SELECT history  HOME exit", 12, 225, GRAY)


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
