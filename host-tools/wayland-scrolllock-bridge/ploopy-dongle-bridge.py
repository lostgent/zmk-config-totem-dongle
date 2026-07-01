#!/usr/bin/env python3
"""
Ploopy Nano -> ZMK dongle Scroll Lock bridge.

Why this exists
---------------
The zmk-hid-trackball-interface automouse mechanism works like this: the Ploopy
Nano's custom (lkbm) QMK firmware taps Scroll Lock on the host whenever the ball
moves. The host is supposed to broadcast that Scroll Lock indicator-LED state to
*all* connected HID keyboards, including the ZMK dongle. ZMK's module watches the
Scroll Lock indicator and activates/deactivates the automouse layer accordingly.

That broadcast happens automatically on Windows and under X11, but most Wayland
compositors do NOT mirror lock-LED state across separate keyboard devices, so the
dongle never sees the indicator and the automouse layer never activates.

This bridge closes that gap without touching the firmware or the compositor:
  * It reads the Ploopy keyboard interface directly and detects the Scroll Lock
    taps that signal movement.
  * While movement is happening it forces the Scroll Lock LED *on* the dongle's
    keyboard device via evdev. The kernel turns that into a USB HID output report
    to the dongle, which is exactly the indicator ZMK's module reacts to.
  * A short idle timeout after the last tap clears the LED again, so ZMK's own
    automouse-layer-timeout-ms grace period can deactivate the layer.

It grabs the Ploopy keyboard interface (EVIOCGRAB) so the movement Scroll Lock
taps do not also churn the host's own Scroll Lock state.

Requirements
------------
  * python-evdev  (Arch:  sudo pacman -S python-evdev)
  * Read/write access to /dev/input/event*  (run as root, or a user in the
    `input` group). The provided systemd unit runs it as root.

Configuration (environment variables, all optional):
  PLOOPY_NAME   exact evdev name of the Ploopy keyboard interface
                (default "PloopyCo Trackball Nano")
  DONGLE_NAME   exact evdev name of the ZMK dongle keyboard interface
                (default "ZMK Project TOTEM Keyboard")
  IDLE_TIMEOUT  seconds without a Scroll Lock tap before movement is considered
                stopped (default 0.25)
  NO_GRAB       set to "1" to NOT grab the Ploopy device (default: grab)
  VERBOSE       set to "1" for per-event logging
"""

import os
import select
import sys
import time

try:
    import evdev
    from evdev import ecodes
except ImportError:
    sys.stderr.write(
        "error: python-evdev is not installed.\n"
        "       Arch:  sudo pacman -S python-evdev\n"
        "       pip:   pip install evdev\n"
    )
    sys.exit(1)

PLOOPY_NAME = os.environ.get("PLOOPY_NAME", "PloopyCo Trackball Nano")
DONGLE_NAME = os.environ.get("DONGLE_NAME", "ZMK Project TOTEM Keyboard")
IDLE_TIMEOUT = float(os.environ.get("IDLE_TIMEOUT", "0.25"))
GRAB = os.environ.get("NO_GRAB", "0") != "1"
VERBOSE = os.environ.get("VERBOSE", "0") == "1"

RETRY_SECONDS = 2.0


def log(msg):
    sys.stderr.write(f"[ploopy-bridge] {msg}\n")
    sys.stderr.flush()


def vlog(msg):
    if VERBOSE:
        log(msg)


def find_ploopy():
    """The Ploopy keyboard interface: exact name match AND can emit Scroll Lock.

    (The trackball also exposes Mouse / System Control / Consumer Control
    interfaces with different names; only the plain keyboard interface taps
    Scroll Lock, so we match on the capability to be safe.)
    """
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
        except OSError:
            continue
        keys = dev.capabilities().get(ecodes.EV_KEY, [])
        if dev.name == PLOOPY_NAME and ecodes.KEY_SCROLLLOCK in keys:
            return dev
        dev.close()
    return None


def find_dongle():
    """The ZMK dongle keyboard interface, by exact name."""
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
        except OSError:
            continue
        if dev.name == DONGLE_NAME:
            return dev
        dev.close()
    return None


def set_dongle_scrolllock(dongle, on):
    """Force the Scroll Lock LED state on the dongle keyboard device.

    Writing EV_LED to the evdev node makes the kernel send a USB HID output
    report to the dongle, which ZMK receives as a hid-indicators change.
    """
    dongle.write(ecodes.EV_LED, ecodes.LED_SCROLLL, 1 if on else 0)
    dongle.syn()


def run_once():
    ploopy = find_ploopy()
    if ploopy is None:
        log(f"waiting for Ploopy keyboard interface ({PLOOPY_NAME!r})...")
        return False

    dongle = find_dongle()
    if dongle is None:
        log(f"waiting for dongle keyboard interface ({DONGLE_NAME!r})...")
        ploopy.close()
        return False

    log(f"connected: Ploopy={ploopy.path} ({ploopy.name}) -> "
        f"dongle={dongle.path} ({dongle.name})")

    # Make sure we start from a known-off state.
    try:
        set_dongle_scrolllock(dongle, False)
    except OSError as exc:
        log(f"error: could not write Scroll Lock LED to dongle: {exc}")
        log("       (is the dongle device writable? try running as root)")
        ploopy.close()
        dongle.close()
        return False

    if GRAB:
        try:
            ploopy.grab()
            vlog("grabbed Ploopy keyboard interface")
        except OSError as exc:
            log(f"warning: could not grab Ploopy ({exc}); continuing without grab")

    led_on = False
    last_tap = 0.0
    try:
        while True:
            timeout = None
            if led_on:
                timeout = max(0.0, IDLE_TIMEOUT - (time.monotonic() - last_tap))

            readable, _, _ = select.select([ploopy.fd], [], [], timeout)

            if readable:
                try:
                    for event in ploopy.read():
                        if (event.type == ecodes.EV_KEY
                                and event.code == ecodes.KEY_SCROLLLOCK):
                            last_tap = time.monotonic()
                            if not led_on:
                                set_dongle_scrolllock(dongle, True)
                                led_on = True
                                vlog("movement started -> dongle Scroll Lock ON")
                except BlockingIOError:
                    pass
                except OSError as exc:
                    log(f"Ploopy read error ({exc}); reconnecting")
                    break

            if led_on and (time.monotonic() - last_tap) >= IDLE_TIMEOUT:
                try:
                    set_dongle_scrolllock(dongle, False)
                except OSError as exc:
                    log(f"dongle write error ({exc}); reconnecting")
                    break
                led_on = False
                vlog("movement stopped -> dongle Scroll Lock OFF")
    finally:
        try:
            if GRAB:
                ploopy.ungrab()
        except OSError:
            pass
        try:
            set_dongle_scrolllock(dongle, False)
        except OSError:
            pass
        ploopy.close()
        dongle.close()

    return True


def main():
    log(f"starting (idle_timeout={IDLE_TIMEOUT}s, grab={GRAB}, verbose={VERBOSE})")
    while True:
        try:
            run_once()
        except KeyboardInterrupt:
            log("interrupted, exiting")
            return
        except Exception as exc:  # keep the daemon alive across surprises
            log(f"unexpected error: {exc!r}")
        time.sleep(RETRY_SECONDS)


if __name__ == "__main__":
    main()
