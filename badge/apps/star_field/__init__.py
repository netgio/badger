import math
import os
import random

APP_DIR = "/system/apps/star_field"
os.chdir(APP_DIR)

badge.mode(HIRES | VSYNC)
screen.antialias = image.X2
PX = screen.width / 160

GRAVITY = 16384.0
TILT_SMOOTH_MS = 150
TILT_RECENTRE_MS = 4000
TILT_GAIN = 2.2
TURN_SPEED = 90.0 * PX  # screen pixels per second at full lean
BUTTON_LEAN = 0.8

FOV = 70.0 * PX
FAR = 40.0
NEAR = 0.6
CRUISE = 9.0           # depth units per second
WARP = 30.0
STAR_COUNT = 110
SAUCER_COUNT = 2
GALAXY_GAP_MS = (6000, 14000)

SPACE = color.rgb(4, 4, 16)
SAUCER_BODY = color.rgb(150, 160, 180)
SAUCER_RIM = color.rgb(90, 96, 115)
DOME = color.rgb(140, 210, 255, 90)
MONA = color.rgb(20, 20, 28)
MONA_FACE = color.rgb(255, 214, 180)
BEAM_LIGHTS = (color.rgb(255, 80, 80), color.rgb(255, 220, 60), color.rgb(80, 255, 120))
STAR_TINTS = ((255, 255, 255), (190, 210, 255), (255, 235, 190), (255, 200, 200))
GALAXY_TINTS = ((200, 150, 255), (120, 200, 255), (255, 160, 200))

cx = screen.width / 2
cy = screen.height / 2


class Tilt:
    def __init__(self):
        self.x = 0.0
        self.y = 0.0
        self.rest = None

    def recentre(self):
        self.rest = None

    def update(self, dt):
        ax, ay, az = badge.imu()[:3]
        if self.rest is None:
            self.rest = (ax, az)
        smooth = 1 - math.exp(-dt / TILT_SMOOTH_MS)
        recentre = 1 - math.exp(-dt / TILT_RECENTRE_MS)
        self.rest = (self.rest[0] + (ax - self.rest[0]) * recentre,
                     self.rest[1] + (az - self.rest[1]) * recentre)
        tx = clamp(-(ax - self.rest[0]) / GRAVITY * TILT_GAIN, 1.0)
        ty = clamp((az - self.rest[1]) / GRAVITY * TILT_GAIN, 1.0)
        self.x += (tx - self.x) * smooth
        self.y += (ty - self.y) * smooth


def clamp(value, limit):
    return max(-limit, min(limit, value))


def project(x, y, z):
    scale = FOV / z
    return cx + x * scale, cy + y * scale, scale


def spread(z):
    # World half-extent that just covers the screen at depth z.
    return (cx / FOV) * z * 1.3, (cy / FOV) * z * 1.3


def steer_world(obj, sx, sy, dt):
    # Turning the ship slides everything across the screen by the same amount.
    obj.x -= sx * TURN_SPEED * dt * obj.z / FOV
    obj.y -= sy * TURN_SPEED * dt * obj.z / FOV


class Star:
    def __init__(self):
        self.spawn(random.uniform(NEAR, FAR))

    def spawn(self, z=FAR):
        self.z = z
        hx, hy = spread(z)
        self.x = random.uniform(-hx, hx)
        self.y = random.uniform(-hy, hy)
        self.tint = random.choice(STAR_TINTS)
        self.twinkle = random.uniform(0, 6.28)

    def update(self, speed, sx, sy, dt):
        self.z -= speed * dt
        if self.z < NEAR:
            self.spawn(FAR - random.uniform(0, FAR * 0.2))
            return
        steer_world(self, sx, sy, dt)
        # Wrap around the edges of the view so turning keeps the field even.
        hx, hy = spread(self.z)
        if self.x > hx:
            self.x -= 2 * hx
        elif self.x < -hx:
            self.x += 2 * hx
        if self.y > hy:
            self.y -= 2 * hy
        elif self.y < -hy:
            self.y += 2 * hy

    def draw(self, speed, warp):
        x, y, _ = project(self.x, self.y, self.z)
        near = 1 - self.z / FAR
        flicker = 0.75 + 0.25 * math.sin(badge.ticks / 180 + self.twinkle)
        level = (0.35 + 0.65 * near) * (1 if warp > 0.1 else flicker)
        r, g, b = self.tint
        screen.pen = color.rgb(int(r * level), int(g * level), int(b * level))
        size = (0.5 + near * 1.6) * PX
        if warp > 0.05:
            tx, ty, _ = project(self.x, self.y, self.z + speed * 0.06 * warp)
            screen.shape(shape.line(tx, ty, x, y, size))
        screen.shape(shape.circle(x, y, size))


class Galaxy:
    def __init__(self):
        self.active = False
        self.next_ms = badge.ticks + random.randint(2000, 5000)

    def spawn(self):
        self.active = True
        self.z = FAR
        hx, hy = spread(FAR)
        self.x = random.uniform(-hx * 0.6, hx * 0.6)
        self.y = random.uniform(-hy * 0.6, hy * 0.6)
        self.radius = random.uniform(2.5, 4.0)
        self.tilt = random.uniform(0.35, 0.8)
        self.angle = random.uniform(0, 6.28)
        self.spin = random.choice((-1, 1)) * random.uniform(0.3, 0.6)
        self.tint = random.choice(GALAXY_TINTS)
        self.arms = random.choice((2, 3))
        self.dots = [(random.random() ** 0.8, random.uniform(-0.18, 0.18)) for _ in range(60)]

    def update(self, speed, sx, sy, dt):
        if not self.active:
            if badge.ticks >= self.next_ms:
                self.spawn()
            return
        self.z -= speed * 0.6 * dt
        steer_world(self, sx, sy, dt)
        self.angle += self.spin * dt
        x, y, scale = project(self.x, self.y, self.z)
        r = self.radius * scale
        if self.z < NEAR * 2 or x + r < 0 or x - r > screen.width or y + r < 0 or y - r > screen.height:
            self.active = False
            self.next_ms = badge.ticks + random.randint(*GALAXY_GAP_MS)

    def draw(self):
        if not self.active:
            return
        x, y, scale = project(self.x, self.y, self.z)
        r = self.radius * scale
        fade = min(1.0, (FAR - self.z) / 8 + 0.2)
        tr, tg, tb = self.tint
        screen.pen = color.rgb(tr, tg, tb, int(40 * fade))
        screen.shape(shape.ellipse(x, y, r * 0.55, r * 0.55 * self.tilt))
        dot = max(0.5, r / 30)
        for i, (t, jitter) in enumerate(self.dots):
            arm = (i % self.arms) * 6.283 / self.arms
            a = self.angle + arm + t * 3.6 + jitter
            d = r * (0.08 + t * 0.92)
            brightness = (1 - t * 0.6) * fade
            screen.pen = color.rgb(int(tr + (255 - tr) * (1 - t)), int(tg + (255 - tg) * (1 - t)),
                                   int(tb + (255 - tb) * (1 - t)), int(255 * brightness))
            screen.shape(shape.circle(x + math.cos(a) * d, y + math.sin(a) * d * self.tilt, dot))
        screen.pen = color.rgb(255, 250, 230, int(230 * fade))
        screen.shape(shape.circle(x, y, max(0.8, r * 0.08)))


class Saucer:
    def __init__(self, delay=0):
        self.active = False
        self.next_ms = badge.ticks + delay + random.randint(1500, 5000)

    def spawn(self):
        self.active = True
        self.z = random.uniform(FAR * 0.18, FAR * 0.35)
        hx, hy = spread(self.z)
        self.dir = random.choice((-1, 1))
        self.x = -self.dir * hx
        self.y = random.uniform(-hy * 0.6, hy * 0.6)
        self.vx = self.dir * random.uniform(0.7, 1.2) * hx / 3
        self.phase = random.uniform(0, 6.28)
        self.lights = random.randint(0, 2)

    def update(self, speed, sx, sy, dt):
        if not self.active:
            if badge.ticks >= self.next_ms:
                self.spawn()
            return
        self.x += self.vx * dt
        self.z -= speed * 0.15 * dt
        steer_world(self, sx, sy, dt)
        self.phase += dt * 3
        x, y, scale = project(self.x, self.y, self.z)
        w = 1.6 * scale
        if self.z < NEAR * 4 or x + w < -10 or x - w > screen.width + 10 or y < -20 or y > screen.height + 20:
            self.active = False
            self.next_ms = badge.ticks + random.randint(3000, 9000)

    def draw(self):
        if not self.active:
            return
        x, y, scale = project(self.x, self.y, self.z)
        y += math.sin(self.phase) * scale * 0.15
        w = 1.2 * scale
        h = w * 0.3
        # Tractor-beam glow underneath.
        if (badge.ticks // 900) % 3 == 0:
            screen.pen = color.rgb(120, 255, 180, 50)
            screen.triangle(vec2(x - w * 0.25, y), vec2(x + w * 0.25, y), vec2(x, y + w * 1.4))
        screen.pen = DOME
        screen.shape(shape.circle(x, y - w * 0.3, w * 0.42))
        # Mona in the dome.
        head = w * 0.22
        hy = y - w * 0.36
        screen.pen = MONA
        screen.triangle(vec2(x - head * 0.95, hy - head * 0.2), vec2(x - head * 0.55, hy - head * 1.25),
                        vec2(x - head * 0.15, hy - head * 0.7))
        screen.triangle(vec2(x + head * 0.95, hy - head * 0.2), vec2(x + head * 0.55, hy - head * 1.25),
                        vec2(x + head * 0.15, hy - head * 0.7))
        screen.shape(shape.circle(x, hy, head))
        screen.pen = MONA_FACE
        screen.shape(shape.ellipse(x, hy + head * 0.25, head * 0.7, head * 0.45))
        screen.pen = MONA
        eye = max(0.5, head * 0.12)
        screen.shape(shape.circle(x - head * 0.3, hy + head * 0.2, eye))
        screen.shape(shape.circle(x + head * 0.3, hy + head * 0.2, eye))
        # Hull.
        screen.pen = SAUCER_RIM
        screen.shape(shape.ellipse(x, y + h * 0.25, w, h))
        screen.pen = SAUCER_BODY
        screen.shape(shape.ellipse(x, y, w, h * 0.8))
        light = max(0.6, w * 0.08)
        step = (badge.ticks // 200) % 3
        for i in range(5):
            screen.pen = BEAM_LIGHTS[(i + step + self.lights) % 3]
            screen.shape(shape.circle(x + (i - 2) * w * 0.38, y + h * 0.15, light))


tilt = Tilt()
stars = [Star() for _ in range(STAR_COUNT)]
galaxy = Galaxy()
saucers = [Saucer(i * 4000) for i in range(SAUCER_COUNT)]
speed = CRUISE


def update():
    global speed
    dt_ms = min(badge.ticks_delta, 100)
    dt = dt_ms / 1000

    if badge.pressed(BUTTON_BACK):
        tilt.recentre()
    tilt.update(dt_ms)
    sx, sy = tilt.x, tilt.y
    if badge.held(BUTTON_LEFT):
        sx -= BUTTON_LEAN
    if badge.held(BUTTON_RIGHT):
        sx += BUTTON_LEAN
    if badge.held(BUTTON_UP):
        sy -= BUTTON_LEAN
    if badge.held(BUTTON_DOWN):
        sy += BUTTON_LEAN
    sx, sy = clamp(sx, 1.0), clamp(sy, 1.0)

    target = WARP if badge.held(BUTTON_SELECT) else CRUISE
    speed += (target - speed) * (1 - math.exp(-dt / 0.4))
    warp = (speed - CRUISE) / (WARP - CRUISE)

    for star in stars:
        star.update(speed, sx, sy, dt)
    galaxy.update(speed, sx, sy, dt)
    for saucer in saucers:
        saucer.update(speed, sx, sy, dt)

    screen.pen = SPACE
    screen.shape(shape.rectangle(0, 0, screen.width, screen.height))
    galaxy.draw()
    for star in stars:
        star.draw(speed, warp)
    for saucer in sorted(saucers, key=lambda s: -s.z if s.active else 0):
        saucer.draw()


run(update)
