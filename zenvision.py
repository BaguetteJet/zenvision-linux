#!/usr/bin/env python3
"""zenvision-linux — userspace driver for the ASUS ZenVision lid OLED.

Target: ASUS Zenbook 14X OLED Space Edition (UX5401ZAS), the 3.5" 256x64
monochrome PMOLED in the lid, exposed over USB as a Nuvoton M480 device
(VID:PID 0b05:8835).

The display protocol was reverse-engineered for interoperability; see
PROTOCOL.md for the full description.

Usage:
    sudo ./zenvision.py image   picture.png [--bright 0x4f] [--sweep]
    sudo ./zenvision.py image   --white                   # white test pattern
    sudo ./zenvision.py off                              # clear (all black)
    sudo ./zenvision.py anim    frames_dir/ [--fps 20]    # play a frame folder
    sudo ./zenvision.py status                           # query the content engine
    sudo ./zenvision.py time                             # set current time
    sudo ./zenvision.py theme 4 [--speed 2]              # built-in theme 1-4
    sudo ./zenvision.py clock 1 [--battery] [--speed 2]  # built-in clock layout 1-2
    sudo ./zenvision.py speed 2                          # built-in content speed 1-3
    sudo ./zenvision.py bootanim on                      # lid-close boot animation
    sudo ./zenvision.py bright 0x4f                      # panel brightness 0-255

Requires: pyusb, pillow, and raw USB access (run as root or install the
provided udev rule). See README.md.
"""
import argparse
import glob
import sys
import time

import usb.core
import usb.util
from PIL import Image

VID, PID = 0x0B05, 0x8835
IFACE = 0
EP_CMD = 0x03    # interrupt OUT — control commands (512 bytes)
EP_BULK = 0x07   # bulk OUT      — framebuffer (8704 bytes)
EP_RESP = 0x82   # interrupt IN  — engine-state replies (512 bytes)
W, H = 256, 64   # panel resolution
FRAME_BYTES = 8704

# 35 01 <val> — the three brightness levels the MyASUS app exposes
BRIGHTNESS = {1: 0x0F, 2: 0x4F, 3: 0xBC}

# F1 03 replies (EP 0x82, ASCII)
ENGINE_STATES = {"01": "clock layout", "02": "built-in theme", "07": "custom image"}


def encode(img):
    """PIL image -> 8704-byte ZenVision framebuffer (4bpp grayscale + framing).

    See PROTOCOL.md. Three stages: per-pixel 4-bit gray (row-major), 4bpp pack
    with the panel's pair-swap, then split into 17 x 512-byte packets each
    carrying a 1-byte index header.
    """
    img = img.convert("L")
    if img.size != (W, H):
        img = img.resize((W, H), Image.LANCZOS)
    px = img.load()

    # 1) 4-bit gray per pixel, row-major
    n = bytearray(W * H)
    i = 0
    for y in range(H):
        for x in range(W):
            n[i] = px[x, y] >> 4
            i += 1

    # 2) pack to 4bpp (8192 bytes), with the 2-byte swap per 4-pixel group
    data = bytearray(8192)
    for k in range(4096):
        s = 4 * k
        data[2 * k] = n[s + 2] | (n[s + 3] << 4)
        data[2 * k + 1] = n[s] | (n[s + 1] << 4)

    # 3) frame into 17 x 512-byte packets (byte0 = packet index)
    out = bytearray(FRAME_BYTES)
    pos = d = 0
    while pos < FRAME_BYTES and d <= 0x1FFF:
        bp = pos & 0x1FF
        if bp == 0:
            out[pos] = (pos >> 9) & 0xFF
        elif bp == 1:
            if (pos >> 9) == 16:
                out[pos] = 1
        elif bp >= 4:
            out[pos] = data[d]
            d += 1
        pos += 1
    return bytes(out)


def _cmd(*head):
    b = bytearray(512)
    b[: len(head)] = bytes(head)
    return bytes(b)


class ZenVision:
    """Thin driver over the vendor interface (iface 0)."""

    def __init__(self):
        self.dev = usb.core.find(idVendor=VID, idProduct=PID)
        if self.dev is None:
            raise RuntimeError("ZenVision (0b05:8835) not found")
        try:
            if self.dev.is_kernel_driver_active(IFACE):
                self.dev.detach_kernel_driver(IFACE)
        except Exception:
            pass
        usb.util.claim_interface(self.dev, IFACE)

    def _c(self, data):
        self.dev.write(EP_CMD, data, timeout=3000)

    def _b(self, data):
        self.dev.write(EP_BULK, data, timeout=3000)

    def engine_state(self):
        """F1 03 — query the content engine. Returns '01' (clock), '02'
        (theme), '07' (image), or None if the panel didn't reply."""
        try:
            self.dev.read(EP_RESP, 512, timeout=200)  # drain stale reply
        except usb.core.USBError:
            pass
        self._c(_cmd(0xF1, 0x03))
        try:
            r = bytes(self.dev.read(EP_RESP, 512, timeout=2000))
            return r[:2].decode("ascii")
        except (usb.core.USBError, UnicodeDecodeError):
            return None

    def set_content_mode(self, mode):
        """30 06 05 00 00 00 00 <mode> — 1 custom image, 2 custom stream,
        3 news ticker."""
        self._c(_cmd(0x30, 0x06, 0x05, 0x00, 0x00, 0x00, 0x00, mode))

    def set_brightness(self, value):
        """35 01 <val> — panel brightness, raw byte 0-255 (MyASUS levels:
        0x0F dim, 0x4F mid, 0xBC bright)."""
        self._c(_cmd(0x35, 0x01, value))

    def set_clock(self, layout):
        """30 05 01 <layout> — show a built-in clock layout (1-2)."""
        self._c(_cmd(0x30, 0x05, 0x01, layout))

    def set_theme(self, theme):
        """30 05 02 00 <theme> — play a built-in theme (1-4)."""
        self._c(_cmd(0x30, 0x05, 0x02, 0x00, theme))

    def set_battery(self, on):
        """30 05 04 00 00 00 <val> — battery icon on/off (clock layouts)."""
        self._c(_cmd(0x30, 0x05, 0x04, 0x00, 0x00, 0x00, 0x03 if on else 0x01))

    def set_screen_sweep(self, on):
        """31 02 <a> <b> — burn-in-protection sweep over static content."""
        self._c(_cmd(0x31, 0x02, 0x02 if on else 0x00, 0x03 if on else 0x04))

    def set_boot_animation(self, on):
        """32 02 <a> <b> — lid-close boot animation on/off."""
        self._c(_cmd(0x32, 0x02, 0x02 if on else 0x00, 0x02 if on else 0x00))

    def set_speed(self, speed):
        """33 01 <speed> — built-in content speed (1 slow .. 3 fast)."""
        self._c(_cmd(0x33, 0x01, speed))

    def set_time(self, t=None, use_24h=True):
        """40 09 ... — set the panel clock."""
        t = t or time.localtime()
        weekday = (t.tm_wday + 1) % 7  # device: Sunday = 0, Python: Monday = 0
        self._c(_cmd(0x40, 0x09, *t.tm_year.to_bytes(2, "little"), t.tm_mon,
                     t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec,
                     int(use_24h), weekday))

    def show_image(self, fb, bright=BRIGHTNESS[2], sweep=False):
        """Display a single static framebuffer (content mode 1, then pixels).

        No commit/apply command exists — the frame is shown by the content-mode
        command plus the bulk transfer itself. Optionally enables the screen
        sweep (burn-in protection), off by default.
        """
        self.set_content_mode(1)
        self.set_brightness(bright)
        self._b(fb)
        self.set_screen_sweep(sweep)

    def stream_begin(self, bright=BRIGHTNESS[2]):
        """Enter streaming mode (content mode 2): frames pushed bulk-only."""
        self.set_content_mode(2)
        self.set_brightness(bright)

    def stream_frame(self, fb):
        self._b(fb)

    def close(self):
        try:
            usb.util.release_interface(self.dev, IFACE)
        except Exception:
            pass


def _load_frames(folder):
    files = sorted(glob.glob(folder.rstrip("/") + "/*.png") +
                   glob.glob(folder.rstrip("/") + "/*.gif") +
                   glob.glob(folder.rstrip("/") + "/*.jpg"))
    if not files:
        sys.exit("no image frames found in " + folder)
    return [encode(Image.open(f)) for f in files]


def _level(arg, lo, hi):
    v = int(arg, 0)
    if not lo <= v <= hi:
        raise argparse.ArgumentTypeError("must be %d-%d" % (lo, hi))
    return v


def main():
    ap = argparse.ArgumentParser(description="Drive the ASUS ZenVision lid OLED from Linux.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("image", help="show a single image")
    pi.add_argument("path", nargs="?")
    pi.add_argument("--white", action="store_true", help="white test pattern")
    pi.add_argument("--bright", type=lambda x: _level(x, 0, 255), default=BRIGHTNESS[2])
    pi.add_argument("--sweep", action="store_true",
                    help="enable the burn-in-protection sweep over the image (default off)")
    pi.add_argument("--hold", type=float, default=0.0, help="keep the channel open N seconds")

    sub.add_parser("off", help="clear the panel (black)")

    pa = sub.add_parser("anim", help="play a folder of frames")
    pa.add_argument("dir")
    pa.add_argument("--fps", type=float, default=20.0)
    pa.add_argument("--bright", type=lambda x: _level(x, 0, 255), default=BRIGHTNESS[2])
    pa.add_argument("--dur", type=float, default=0.0, help="seconds (0 = loop forever)")

    sub.add_parser("status", help="query the content engine (clock/theme/image)")

    pt = sub.add_parser("time", help="sync the panel clock to the current time")
    pt.add_argument("--12h", dest="h12", action="store_true",
                    help="12-hour format (default 24h)")

    pt = sub.add_parser("theme", help="play a built-in theme (1-4)")
    pt.add_argument("theme", type=lambda x: _level(x, 1, 4))
    pt.add_argument("--speed", type=lambda x: _level(x, 1, 3), default=2)
    pt.add_argument("--bright", type=lambda x: _level(x, 0, 255), default=BRIGHTNESS[2])

    pc = sub.add_parser("clock", help="show a built-in clock layout (1-2)")
    pc.add_argument("layout", type=lambda x: _level(x, 1, 2))
    pc.add_argument("--12h", dest="h12", action="store_true", help="12-hour format (default 24h)")
    pc.add_argument("--battery", action="store_true", help="show the battery icon")
    pc.add_argument("--speed", type=lambda x: _level(x, 1, 3), default=2)
    pc.add_argument("--bright", type=lambda x: _level(x, 0, 255), default=BRIGHTNESS[2])

    ps = sub.add_parser("speed", help="set the built-in content speed (1-3)")
    ps.add_argument("speed", type=lambda x: _level(x, 1, 3))

    pb = sub.add_parser("bootanim", help="toggle the lid-close boot animation")
    pb.add_argument("state", choices=["on", "off"])

    pb = sub.add_parser("bright", help="set the panel brightness (raw byte 0-255)")
    pb.add_argument("value", type=lambda x: _level(x, 0, 255))

    args = ap.parse_args()
    zv = ZenVision()
    try:
        if args.cmd == "image":
            if args.white:
                fb = encode(Image.new("L", (W, H), 255))
            elif args.path:
                fb = encode(Image.open(args.path))
            else:
                sys.exit("give an image path or --white")
            zv.show_image(fb, args.bright, args.sweep)
            if args.hold:
                time.sleep(args.hold)

        elif args.cmd == "off":
            zv.show_image(encode(Image.new("L", (W, H), 0)))

        elif args.cmd == "anim":
            frames = _load_frames(args.dir)
            zv.stream_begin(args.bright)
            print("streaming %d frames @ %.0f fps (Ctrl-C to stop)" % (len(frames), args.fps))
            delay = 1.0 / args.fps
            end = (time.time() + args.dur) if args.dur > 0 else None
            try:
                while end is None or time.time() < end:
                    for fb in frames:
                        zv.stream_frame(fb)
                        time.sleep(delay)
            except KeyboardInterrupt:
                pass

        elif args.cmd == "status":
            state = zv.engine_state()
            if state is None:
                sys.exit("no reply from the panel")
            print(ENGINE_STATES.get(state, "unknown engine state '%s'" % state))

        elif args.cmd == "theme":
            zv.set_brightness(args.bright)
            zv.set_speed(args.speed)
            zv.set_theme(args.theme)

        elif args.cmd == "time":
            zv.set_time(use_24h=not args.h12)

        elif args.cmd == "clock":
            zv.set_brightness(args.bright)
            zv.set_battery(args.battery)
            zv.set_screen_sweep(args.layout == 2)  # MyASUS pairs layout 2 with sweep on
            zv.set_clock(args.layout)
            zv.set_speed(args.speed)
            zv.set_time(use_24h=not args.h12)

        elif args.cmd == "speed":
            zv.set_speed(args.speed)

        elif args.cmd == "bootanim":
            zv.set_boot_animation(args.state == "on")

        elif args.cmd == "bright":
            zv.set_brightness(args.value)

    finally:
        zv.close()


if __name__ == "__main__":
    main()
