"""NTP-confirmed session time; never trust a reset RTC or persist secrets."""

import time


class Clock:
    def __init__(self, ticks, wifi=None, ntp=None):
        self.ticks = ticks
        self.wifi = wifi
        self.ntp = ntp
        self.anchor = None
        self.anchor_ticks = 0
        self.next_try = 0
        self.message = "WiFi time needed"

    def now(self, ticks):
        if self.anchor is None:
            return None
        return self.anchor + (ticks - self.anchor_ticks) // 1000

    def poll(self, ticks):
        if ticks < self.next_try or self.anchor is not None:
            return
        if self.wifi is None or self.ntp is None:
            self.message = "Simulator: no real time"
            self.next_try = ticks + 60000
            return
        try:
            if not self.wifi.connect():
                _, status = self.wifi.status()
                self.message = "WiFi: " + status
                self.next_try = ticks + 1000
                return
            self.ntp.timeout = 3
            self.ntp.settime()
            if time.gmtime()[0] < 2025:
                raise ValueError("NTP returned invalid date")
            self.anchor = int(time.time())
            self.anchor_ticks = self.ticks()
            self.message = "UTC synced; 5min logs"
        except (OSError, ValueError) as error:
            self.message = "Time sync failed; retrying"
            print("Govee Hub: time sync:", error)
            self.next_try = ticks + 60000

    def close(self):
        if self.wifi is not None:
            self.wifi.disconnect()
