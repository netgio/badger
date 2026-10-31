"""H5075 advertisements; matches Bluetooth-Devices/govee-ble's decoder."""


def fields(payload):
    offset = 0
    while offset < len(payload):
        length = payload[offset]
        if not length or offset + length >= len(payload):
            return
        yield payload[offset + 1], payload[offset + 2:offset + length + 1]
        offset += length + 1


def decode(payload):
    name = None
    reading = None
    for kind, value in fields(payload):
        if kind in (8, 9) and b"H5075" in value:
            name = value.decode("utf-8")
        if kind == 255 and len(value) == 8 and value[:2] == b"\x88\xec":
            data = value[2:]
            packed = (data[1] << 16) | (data[2] << 8) | data[3]
            magnitude = packed & 0x7fffff
            temperature = magnitude // 1000
            if packed & 0x800000:
                temperature = -temperature
            humidity = magnitude % 1000
            battery = data[4] & 0x7f
            error = bool(data[4] & 0x80)
            if not (-400 <= temperature <= 1000 and humidity <= 1000
                    and battery <= 100):
                error = True
            reading = (temperature, humidity, battery, error)
    return name, reading


class Scanner:
    def __init__(self, ble, ticks, manager=None):
        if manager and manager.in_session:
            raise RuntimeError("Bluetooth session already in use")
        if not manager and ble.active():
            raise RuntimeError("Bluetooth already in use")
        self.ble = ble
        self.manager = manager
        self.ticks = ticks
        self.queue = []
        self.devices = {}
        self.dropped = 0
        self.owned = False
        self.next_scan = 0
        try:
            if manager:
                self.owned = True
                manager.open(self.irq)
                self.ble = manager.ble()
            else:
                ble.active(True)
                self.owned = True
                ble.irq(self.irq)
        except (OSError, RuntimeError):
            self.close()
            raise

    def irq(self, event, data):
        if event == 5:
            if len(self.queue) >= 64:
                self.dropped += 1
                return
            self.queue.append((data[0], bytes(data[1]), bytes(data[4])))

    def poll(self, now, saved=()):
        if now >= self.next_scan:
            self.next_scan = now + 11000
            self.ble.gap_scan(10000, 100000, 50000, True)
        # Drain a bounded snapshot; new IRQ events wait for the next frame.
        pending = self.queue
        self.queue = []
        for addr_type, address, payload in pending:
            key = "%d:%s" % (addr_type, address.hex())
            name, reading = decode(payload)
            if key not in self.devices:
                if name is None and key not in saved:
                    continue
                if len(self.devices) >= 8:
                    continue
                self.devices[key] = {"name": name or "H5075", "seen": None,
                                     "reading": None}
            device = self.devices[key]
            if name:
                device["name"] = name
            if reading is not None:
                device["reading"] = reading
                device["seen"] = now

    def close(self):
        if self.owned and self.manager:
            try:
                self.manager.close()
            finally:
                self.owned = False
            return
        if self.owned:
            try:
                if self.ble.active():
                    try:
                        self.ble.gap_scan(None)
                    except OSError as error:
                        if error.args[0] != 19 or self.ble.active():
                            raise
                        print("Govee Hub: firmware already released Bluetooth")
            finally:
                try:
                    self.ble.irq(None)
                finally:
                    try:
                        if self.ble.active():
                            self.ble.active(False)
                    finally:
                        self.owned = False
