# Wayland Scroll Lock bridge

Makes the `zmk-hid-trackball-interface` automouse layer work on Wayland.

## The problem

The trackball automouse mechanism signals "the ball is moving" by having the
Ploopy Nano's custom firmware tap **Scroll Lock**. The host is expected to
broadcast that Scroll Lock indicator-LED state to every connected HID keyboard,
including the ZMK dongle, which then activates the automouse layer.

That broadcast happens on **Windows** and under **X11**, but most **Wayland**
compositors do not mirror lock-LED state across separate keyboard devices, so the
dongle never sees the indicator and the automouse layer never activates — even
though the trackball firmware and the dongle firmware are both working correctly.

You can confirm this is your situation with `evtest`: pick the
`PloopyCo Trackball Nano` keyboard device, move the ball, and you'll see
`KEY_SCROLLLOCK` events — but the dongle's serial console stays silent.

## What the bridge does

`ploopy-dongle-bridge.py` reads the Ploopy's Scroll Lock taps directly and, while
movement is happening, forces the Scroll Lock LED **on the dongle's keyboard
device** via evdev. The kernel turns that into a USB HID output report to the
dongle — exactly the indicator ZMK's module reacts to. A short idle timeout after
the last tap clears it again. Compositor-independent; no firmware changes.

It also grabs the Ploopy keyboard interface so the movement Scroll Lock taps don't
churn the host's own Scroll Lock state.

## Install (Arch / systemd)

1. Install the dependency:

   ```bash
   sudo pacman -S python-evdev
   ```

2. Copy the script into place:

   ```bash
   sudo mkdir -p /opt/ploopy-dongle-bridge
   sudo cp ploopy-dongle-bridge.py /opt/ploopy-dongle-bridge/
   sudo chmod +x /opt/ploopy-dongle-bridge/ploopy-dongle-bridge.py
   ```

3. Install and enable the service:

   ```bash
   sudo cp ploopy-dongle-bridge.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now ploopy-dongle-bridge.service
   ```

4. Watch it run:

   ```bash
   systemctl status ploopy-dongle-bridge.service
   journalctl -u ploopy-dongle-bridge.service -f
   ```

   You should see a line like
   `connected: Ploopy=/dev/input/event22 (...) -> dongle=/dev/input/event2 (...)`.

5. Move the trackball. The automouse (MOUSE) layer should now activate on your
   Wayland session. If you flashed the dongle with `CONFIG_ZMK_USB_LOGGING=y`, you
   can also watch `sudo screen /dev/ttyACM0 115200` and see `mouse layer activated`.

## Quick manual test (before installing the service)

```bash
sudo VERBOSE=1 python3 ploopy-dongle-bridge.py
```

Move the ball; you should see `movement started -> dongle Scroll Lock ON` /
`... OFF` lines, and the automouse layer should activate. Ctrl-C to stop.

## Configuration

Set these as environment variables (or `Environment=` lines in the service unit):

| Variable       | Default                        | Meaning                                                        |
| -------------- | ------------------------------ | -------------------------------------------------------------- |
| `PLOOPY_NAME`  | `PloopyCo Trackball Nano`      | Exact evdev name of the Ploopy keyboard interface.             |
| `DONGLE_NAME`  | `ZMK Project TOTEM Keyboard`   | Exact evdev name of the ZMK dongle keyboard interface.         |
| `IDLE_TIMEOUT` | `0.25`                         | Seconds without a tap before movement is considered stopped.   |
| `NO_GRAB`      | unset                          | Set to `1` to not grab the Ploopy device.                      |
| `VERBOSE`      | unset                          | Set to `1` for per-event logging.                              |

If the device names differ on your machine, find them with:

```bash
for d in /dev/input/event*; do
  echo "$d -> $(cat /sys/class/input/$(basename "$d")/device/name 2>/dev/null)"
done
```

## Notes / tuning

- Total delay before the layer drops after you stop moving is roughly
  `IDLE_TIMEOUT` (this bridge) + `automouse-layer-timeout-ms` (the ZMK config,
  currently 600 ms in `config/totem.keymap`).
- If the LED never seems to take effect and you get a write error in the log,
  the service isn't running with enough privilege — it needs write access to the
  dongle's `/dev/input/event*` node (running as root, as the unit does, is
  simplest).
- This is only needed on Wayland. On X11 / Windows the OS already broadcasts the
  indicator and the bridge is unnecessary.
