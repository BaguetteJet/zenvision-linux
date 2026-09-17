# zenvision-linux

> 🟢 **The first open-source Linux driver for the ASUS ZenVision lid OLED** — the
> protocol was reverse-engineered from scratch (Ghidra on MyASUS). Want live
> applets and audio-reactive visualisers on top? See the companion app
> **[zenvision-studio](https://github.com/tarpediem/zenvision-studio)**.

Userspace Linux driver for the **ASUS ZenVision** lid OLED — the 3.5", 256×64
monochrome screen embedded in the lid of the **ASUS Zenbook 14X OLED Space
Edition (UX5401ZAS)**.

ASUS only ships software for this screen on Windows (inside MyASUS). This project
reverse-engineers the USB protocol and lets you drive the panel from Linux:
show images, play animations, or display whatever you like.

> Status: **working** on UX5401ZAS. Other ASUS lid-OLED models may use a similar
> protocol — reports and PRs welcome.

![demo](docs/demo.gif)

## How it works

The lid screen is a Nuvoton M480 USB device (`0b05:8835`). It is **not** a DRM
display — you don't get a `/dev/fb`; instead you push a 256×64, 4-bit-grayscale
framebuffer to a bulk endpoint after a small command handshake. Full details in
[PROTOCOL.md](PROTOCOL.md).

## Requirements

- Python 3.9+
- [`pyusb`](https://pypi.org/project/pyusb/) and [`Pillow`](https://pypi.org/project/Pillow/)
- `libusb-1.0`
- Raw USB access (root, or the provided udev rule)

```bash
python -m venv .venv && . .venv/bin/activate
pip install pyusb pillow
```

### Arch Linux (AUR)

```bash
yay -S zenvision-linux-git        # installs the `zenvision` CLI + the udev rule
```

## Usage

```bash
# Static image (auto-resized to 256x64, converted to grayscale)
sudo ./zenvision.py image picture.png
sudo ./zenvision.py image picture.png --sweep   # + burn-in-protection sweep

# White test pattern / clear
sudo ./zenvision.py image --white
sudo ./zenvision.py off

# Play a folder of frames as a smooth animation
sudo ./zenvision.py anim frames/ --fps 20

# Ask the panel what it's showing (clock / theme / custom image)
sudo ./zenvision.py status

# Built-in content (the panel runs these on its own)
sudo ./zenvision.py theme 4 --speed 2
sudo ./zenvision.py clock 1 --battery
sudo ./zenvision.py time              # sync the panel clock to the PC
sudo ./zenvision.py speed 2           # built-in content speed (1-3)
sudo ./zenvision.py bootanim on       # lid-close boot animation
sudo ./zenvision.py bright 0x4f       # panel brightness (0-255)
```

Brightness: `--bright N` is a raw byte 0–255 (decimal or `0x` hex); the MyASUS
defaults are `0x0f` dim, `0x4f` mid, `0xbc` bright. Default is `0x4f`. Set it
standalone with `zenvision.py bright N`.

### Generate the demo animation

`examples/spark_demo.py` renders a generic rotating-starburst animation into a
`frames/` folder you can feed to `anim`:

```bash
pip install pillow
python examples/spark_demo.py --out frames --w 256 --h 64
sudo ./zenvision.py anim frames/ --fps 20
```

Want a logo? Render any monochrome 256×64 frames into a folder and point `anim` at
it. (Tip: `rsvg-convert` an SVG, or `ffmpeg -i clip.gif frames/%03d.png`.)

## Running without root (udev)

Copy the rule so your user can access the device:

```bash
sudo cp udev/70-zenvision.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
```

The `70-` prefix matters: the rule must sort **before** `73-seat-late.rules` so the
`uaccess` tag is applied — otherwise the ACL is never granted. `uaccess` gives the
logged-in user access (the right mechanism on systemd; no `plugdev` group needed).
Then run without `sudo`.

## Notes & safety

- Displaying static elements for an extended amount of time on the OLED display will cause permanent pixel degradation.
- The panel is firmware-powered and survives a re-plug; bad experiments are
  recoverable by replaying the settings sequence in PROTOCOL.md (a reboot also
  clears it). Sending malformed control reports on the HID interface can soft-reset
  the MCU (it re-enumerates cleanly) — this driver only uses the vendor interface.
- This is an independent, unofficial project. Not affiliated with or endorsed by ASUS.
- No ASUS firmware, binaries, or decompiled code are included or required.

## Contributing

If you have another ASUS model with a lid OLED, please open an issue with:
`lsusb`, your model number, and whether the framing here works. The protocol doc
is written to make porting straightforward.

## Protocol research

**Version 2** protocol notes are based on
[zenvision-protocol-research](https://github.com/BaguetteJet/zenvision-protocol-research) by [BaguetteJet](https://github.com/BaguetteJet). USB captures of the MyASUS app on Windows; the raw capture records live there. Version 2 adds:

- the `30 05` built-in content commands — clock layouts, themes, power /
  battery icon;
- the `30 06` content-mode selector — custom image / stream / news ticker.
  The original "begin → apply" image flow is really content mode 1 plus a
  bulk transfer: **no commit/apply command exists**, the frame is shown by
  the transfer itself;
- `31 02` screen sweep, `32 02` boot animation, `33 01` speed, `35 01`
  brightness, `40 09` panel clock;
- the `F1 03` engine-state query — the `0x82` endpoint replies to **every**
  command with the current engine state (`01` clock / `02` theme / `07`
  image), previously assumed to always return zeros.

## License

[MIT](LICENSE).
