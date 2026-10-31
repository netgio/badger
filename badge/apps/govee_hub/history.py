"""Checksummed, bounded daily logs stored only under /state."""

import json
import os
import struct
import sys

INTERVAL = 300
DAYS = 30
DAY = 86400
RECORD = "<IhHB"
SIZE = 10
RESERVE = 128 * 1024


def crc8(data):
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ (0x07 if crc & 0x80 else 0)) & 255
    return crc


def encode(timestamp, temperature, humidity, battery):
    body = struct.pack(RECORD, timestamp, temperature, humidity, battery)
    return body + bytes((crc8(body),))


def unpack(data):
    if len(data) != SIZE or crc8(data[:-1]) != data[-1]:
        return None
    stamp, temp, humid, battery = struct.unpack(RECORD, data[:-1])
    if not (-400 <= temp <= 1000 and humid <= 1000 and battery <= 100):
        return None
    return stamp, temp, humid, battery


def exists(path):
    try:
        os.stat(path)
        return True
    except OSError as error:
        # The WASM simulator exposes Emscripten ENOENT (44), not errno.ENOENT.
        missing = (2, 44) if sys.platform == "webassembly" else (2,)
        if error.args[0] not in missing:
            raise
        return False


def sync():
    if hasattr(os, "sync"):
        os.sync()


class History:
    def __init__(self, root="/state/govee_hub"):
        self.root = root
        if not exists(root):
            os.mkdir(root)
        self.warning = ""
        self.last = [0, 0, 0]
        self.config = {"sensors": [None, None, None]}
        self.recover()
        config = root + "/config.json"
        if exists(config):
            with open(config) as source:
                loaded = json.load(source)
            sensors = loaded.get("sensors")
            if (not isinstance(sensors, list) or len(sensors) != 3
                    or any(key is not None and not self.valid_key(key)
                           for key in sensors)
                    or len(set(key for key in sensors if key)) !=
                       len([key for key in sensors if key])):
                raise ValueError("Invalid sensor configuration")
            self.config = {"sensors": sensors}
        for slot, day, path in self.logs():
            valid_end = 0
            previous = 0
            with open(path, "rb") as source:
                while True:
                    data = source.read(SIZE)
                    if not data:
                        break
                    record = unpack(data)
                    if (record is None or record[0] // DAY != day
                            or record[0] <= previous
                            or (previous and record[0] // INTERVAL ==
                                previous // INTERVAL)):
                        self.warning = "Recovered interrupted/corrupt log"
                        break
                    previous = record[0]
                    self.last[slot] = max(self.last[slot], previous)
                    valid_end += SIZE
            if os.stat(path)[6] != valid_end:
                self.repair(path, valid_end)

    @staticmethod
    def valid_key(key):
        return (isinstance(key, str) and len(key) == 14 and key[:2] in ("0:", "1:")
                and all(c in "0123456789abcdef" for c in key[2:]))

    def recover(self):
        # A .bak is committed old data; .tmp is never a committed replacement.
        for name in os.listdir(self.root):
            if name.endswith(".bak"):
                original = self.root + "/" + name[:-4]
                backup = original + ".bak"
                if not exists(original):
                    os.rename(backup, original)
                else:
                    os.remove(backup)
            elif name.endswith(".tmp"):
                os.remove(self.root + "/" + name)

    def replace(self, path):
        sync()
        if exists(path):
            os.rename(path, path + ".bak")
        os.rename(path + ".tmp", path)
        sync()
        if exists(path + ".bak"):
            os.remove(path + ".bak")

    def repair(self, path, length):
        with open(path, "rb") as source, open(path + ".tmp", "wb") as dest:
            while length:
                block = source.read(min(length, 512))
                dest.write(block)
                length -= len(block)
        self.replace(path)

    def pair(self, slot, key):
        if not self.valid_key(key):
            raise ValueError("Invalid sensor address")
        if self.config["sensors"][slot] is not None:
            raise ValueError("Slot already paired")
        if key in self.config["sensors"]:
            raise ValueError("Sensor already paired")
        updated = {"sensors": list(self.config["sensors"])}
        updated["sensors"][slot] = key
        path = self.root + "/config.json"
        with open(path + ".tmp", "w") as dest:
            json.dump(updated, dest)
        self.replace(path)
        self.config = updated

    def logs(self):
        for name in sorted(os.listdir(self.root)):
            parts = name.split("-")
            if len(parts) != 2 or not parts[1].endswith(".bin"):
                continue
            if parts[0] not in ("0", "1", "2") or not parts[1][:-4].isdigit():
                continue
            yield int(parts[0]), int(parts[1][:-4]), self.root + "/" + name

    def prune(self, now):
        cutoff = now - DAYS * DAY
        for _, day, path in self.logs():
            if (day + 1) * DAY <= cutoff:
                os.remove(path)

    def append(self, slot, now, reading):
        if (self.last[slot] and now // INTERVAL <= self.last[slot] // INTERVAL):
            return False
        temp, humid, battery, error = reading
        if error:
            return False
        self.prune(now)
        stats = os.statvfs(self.root)
        if stats[0] * stats[4] < RESERVE + SIZE:
            raise OSError("History paused: storage reserve reached")
        path = "%s/%d-%d.bin" % (self.root, slot, now // DAY)
        with open(path, "ab") as dest:
            dest.write(encode(now, temp, humid, battery))
            dest.flush()
        sync()
        self.last[slot] = now
        return True

    def graph(self, slot, now, duration, field, bins=80):
        start = max(now - duration, now - DAYS * DAY)
        sums = [0] * bins
        counts = [0] * bins
        low = high = None
        total = 0
        for sensor, day, path in self.logs():
            if sensor != slot or (day + 1) * DAY <= start or day * DAY > now:
                continue
            with open(path, "rb") as source:
                while True:
                    data = source.read(SIZE)
                    if not data:
                        break
                    record = unpack(data)
                    if record is None:
                        raise ValueError("Corrupt history record")
                    stamp = record[0]
                    if not start <= stamp <= now:
                        continue
                    value = record[field]
                    index = min(bins - 1, (stamp - start) * bins // duration)
                    sums[index] += value
                    counts[index] += 1
                    total += 1
                    low = value if low is None else min(low, value)
                    high = value if high is None else max(high, value)
        values = [sums[i] / counts[i] if counts[i] else None for i in range(bins)]
        return values, low, high, total
