"""Sony "PC Remote" protocol (PTP vendor extension) for the RX0, over the WPD transport.

Protocol follows libgphoto2's Sony support: SDIO connect handshake, button
presses via SetControlDeviceB, and captured images fetched from the in-memory
object handle 0xFFFFC001.
"""
import struct
import time

from .wpd_mtp import PTPError, WpdMtp, find_devices

OK = 0x2001

# Operations
SDIO_CONNECT = 0x9201
SDIO_GET_EXT_DEVICE_INFO = 0x9202
SET_CONTROL_DEVICE_A = 0x9205  # set a setting
SET_CONTROL_DEVICE_B = 0x9207  # press a button
GET_ALL_DEVICE_PROP_DATA = 0x9209
GET_OBJECT_INFO = 0x1008
GET_OBJECT = 0x1009

# Properties / controls
PROP_HALF_PRESS = 0xD2C1
PROP_SHUTTER = 0xD2C2
PROP_OBJECT_IN_MEMORY = 0xD215
PROP_FOCUS_INDICATION = 0xD213
PRESS, RELEASE = 2, 1
CAPTURED_IMAGE_HANDLE = 0xFFFFC001

PROP_NAMES = {
    0x5007: "FNumber", 0x500A: "FocusMode", 0x500B: "ExposureMeteringMode", 0x500E: "ExposureProgramMode",
    0x5005: "WhiteBalance", 0x5010: "ExposureBiasCompensation", 0x5013: "StillCaptureMode",
    0x5004: "CompressionSetting", 0x5003: "ImageSize",
    0xD200: "DPCCompensation", 0xD203: "ImageSizeSony", 0xD20D: "ShutterSpeed", 0xD20E: "BatteryLevel",
    0xD215: "ObjectInMemory", 0xD21E: "ISO", 0xD21A: "PictureEffect", 0xD213: "FocusIndication",
    0xD222: "StillImageSaveDest",
}

# PTP datatype code -> struct format
_SIMPLE = {0x1: "b", 0x2: "B", 0x3: "h", 0x4: "H", 0x5: "i", 0x6: "I", 0x7: "q", 0x8: "Q"}

ISO_AUTO = 0xFFFFFF


def _shutter_encode(text):
    """'1/500' -> (1 << 16) | 500; '2' or '2/1' -> 2 s."""
    num, _, den = text.partition("/")
    return (int(num) << 16) | int(den or 1)


def _shutter_decode(v):
    num, den = v >> 16, v & 0xFFFF
    return f"{num}/{den}" if den != 1 else f"{num}s"


def _table(mapping):
    reverse = {v: k for k, v in mapping.items()}
    return (lambda s: mapping[s.lower()]), (lambda v: reverse.get(v, f"0x{v:X}"))


# Named settings: name -> (property code, encode(str) -> int, decode(int) -> str).
# Codes found by probing an RX0 in PC Remote mode; the readable ones in
# PROP_NAMES (e.g. 0xD20D ShutterSpeed) are read-only mirrors of these.
SETTINGS = {
    "mode": (0x500E, *_table({"auto": 0x8000, "superior-auto": 0x8001, "p": 2, "m": 1})),
    "shutter": (0xD229, _shutter_encode, _shutter_decode),
    "iso": (0xD226, lambda s: ISO_AUTO if s.lower() == "auto" else int(s),
            lambda v: "auto" if v == ISO_AUTO else str(v)),
    "ev": (0xD224, lambda s: round(float(s) * 1000), lambda v: f"{v / 1000:+.1f}"),
    "focus": (0x500A, *_table({"manual": 1, "af": 2})),
    "wb": (0x5005, *_table({"auto": 2, "daylight": 4, "shade": 0x8011, "cloudy": 0x8010,
                             "tungsten": 6, "kelvin": 0x8012})),
    "kelvin": (0xD20F, int, str),
    "size": (0xD203, *_table({"l": 1, "m": 2, "s": 3})),
}


class _Reader:
    def __init__(self, data):
        self.d, self.i = data, 0

    def take(self, fmt):
        v = struct.unpack_from("<" + fmt, self.d, self.i)
        self.i += struct.calcsize("<" + fmt)
        return v[0] if len(v) == 1 else v

    def string(self):
        n = self.take("B")
        s = self.d[self.i:self.i + 2 * n].decode("utf-16-le").rstrip("\0")
        self.i += 2 * n
        return s

    def value(self, dtype):
        if dtype == 0xFFFF:
            return self.string()
        if dtype in _SIMPLE:
            return self.take(_SIMPLE[dtype])
        if dtype & 0x4000 and (dtype & 0xFF) in _SIMPLE:
            n = self.take("I")
            return [self.take(_SIMPLE[dtype & 0xFF]) for _ in range(n)]
        raise ValueError(f"unsupported datatype 0x{dtype:04X}")


def parse_all_props(data):
    """Parse GetAllDevicePropData: {code: dict(type, current, writable, enabled, form)}."""
    r = _Reader(data)
    count = r.take("I")
    r.take("I")  # reserved
    props = {}
    for _ in range(count):
        try:
            code, dtype, getset, enabled = r.take("H"), r.take("H"), r.take("B"), r.take("B")
            r.value(dtype)  # factory default
            current = r.value(dtype)
            form_flag = r.take("B")
            form = None
            if form_flag == 1:
                form = ("range", r.value(dtype), r.value(dtype), r.value(dtype))
            elif form_flag == 2:
                form = ("enum", [r.value(dtype) for _ in range(r.take("H"))])
        except (struct.error, ValueError):
            break  # stop cleanly on anything unexpected; earlier props are still valid
        props[code] = {"type": dtype, "current": current, "writable": getset == 1,
                       "enabled": enabled, "form": form}
    return props


class RX0:
    def __init__(self, device_path=None):
        if device_path is None:
            found = find_devices()
            if not found:
                raise RuntimeError("No Sony camera found. Is it on, connected, and set to USB Connection: PC Remote?")
            device_path = found[0]
        self.t = WpdMtp(device_path)
        self._connect()

    def close(self):
        self.t.close()

    def _check(self, code, what):
        if code != OK:
            raise PTPError(f"{what} failed with response 0x{code:04X}")

    def _connect(self):
        for phase in (1, 2):
            code, _, _ = self.t.read(SDIO_CONNECT, (phase, 0, 0))
            self._check(code, f"SDIO connect phase {phase}")
        code, _, _ = self.t.read(SDIO_GET_EXT_DEVICE_INFO, (0xC8,))
        self._check(code, "GetExtDeviceInfo")
        code, _, _ = self.t.read(SDIO_CONNECT, (3, 0, 0))
        self._check(code, "SDIO connect phase 3")

    def props(self):
        code, _, data = self.t.read(GET_ALL_DEVICE_PROP_DATA)
        self._check(code, "GetAllDevicePropData")
        return parse_all_props(data)

    def _button(self, prop, value):
        code, _ = self.t.write(SET_CONTROL_DEVICE_B, (prop,), struct.pack("<H", value))
        self._check(code, f"button 0x{prop:04X}={value}")

    def set_prop(self, prop, value, fmt):
        code, _ = self.t.write(SET_CONTROL_DEVICE_A, (prop,), struct.pack("<" + fmt, value))
        self._check(code, f"set 0x{prop:04X}")

    def get_settings(self):
        props = self.props()
        return {name: decode(props[code]["current"])
                for name, (code, _, decode) in SETTINGS.items() if code in props}

    def apply(self, name, text, timeout=3.0):
        """Set a named setting and wait until the camera reports it (changes lag ~0.5 s)."""
        code, encode, decode = SETTINGS[name]
        value = encode(text)
        prop = self.props().get(code)
        if prop is None:
            raise PTPError(f"{name}: camera does not offer property 0x{code:04X}")
        if prop["current"] == value:
            return  # re-sending (especially the mode) makes the camera reset other settings
        if prop["form"] and prop["form"][0] == "enum" and value not in prop["form"][1]:
            allowed = ", ".join(decode(v) for v in prop["form"][1])
            raise ValueError(f"{name}={text} not allowed in the current mode. Allowed: {allowed}")
        # The camera occasionally ignores a write (e.g. the first after connecting), so resend once.
        for _attempt in range(2):
            self.set_prop(code, value, _SIMPLE[prop["type"]])
            t0 = time.time()
            while time.time() - t0 < timeout / 2:
                time.sleep(0.2)
                if self.props()[code]["current"] == value:
                    if name == "mode":
                        time.sleep(1.5)  # the camera keeps rewriting exposure settings after a mode change
                    return
        raise PTPError(f"{name}={text} was sent but the camera still reports "
                       f"{decode(self.props()[code]['current'])}")

    def focus_lock(self, settle=2.0):
        """Autofocus on whatever is in front of the camera, then switch to manual to hold it."""
        self.apply("focus", "af")
        self._button(PROP_HALF_PRESS, PRESS)
        seen = []
        t0 = time.time()
        while time.time() - t0 < settle:
            time.sleep(0.2)
            seen.append(self.props()[PROP_FOCUS_INDICATION]["current"])
        self._button(PROP_HALF_PRESS, RELEASE)
        # The camera ignores a focus-mode change for a moment after the button is released.
        for _ in range(5):
            time.sleep(1.0)
            try:
                self.apply("focus", "manual")
                break
            except PTPError:
                continue
        else:
            raise PTPError("Could not switch back to manual focus")
        return seen

    def trigger_prepare(self, focus_wait=0.3):
        """Half-press and let focus/metering settle."""
        self._button(PROP_HALF_PRESS, PRESS)
        time.sleep(focus_wait)

    def trigger_release(self):
        """Full-press then let go of the button entirely."""
        self._button(PROP_SHUTTER, PRESS)
        self._button(PROP_SHUTTER, RELEASE)
        self._button(PROP_HALF_PRESS, RELEASE)

    def trigger(self, focus_wait=0.3):
        """Half-press, full-press, release: same as pressing the shutter button."""
        self.trigger_prepare(focus_wait)
        self.trigger_release()

    def pending_images(self):
        obj = self.props().get(PROP_OBJECT_IN_MEMORY, {}).get("current", 0)
        return obj - 0x8000 if obj and obj >= 0x8000 else 0

    def wait_for_image(self, timeout=15.0):
        t0 = time.time()
        while time.time() - t0 < timeout:
            if n := self.pending_images():
                return n
            time.sleep(0.2)
        value = self.props().get(PROP_OBJECT_IN_MEMORY, {}).get("current")
        raise TimeoutError(f"Camera did not report a captured image (0xD215 = {value!r})")

    def discard_pending(self):
        """Drop images left in camera memory from earlier shots so the next capture is fresh."""
        dropped = 0
        while self.pending_images() and dropped < 20:
            self.download_image()
            dropped += 1
            time.sleep(0.2)
        return dropped

    def download_image(self):
        """Fetch the captured image from camera memory. Returns (filename, bytes)."""
        code, _, info = self.t.read(GET_OBJECT_INFO, (CAPTURED_IMAGE_HANDLE,))
        self._check(code, "GetObjectInfo")
        r = _Reader(info)
        r.i = 52  # fixed-size ObjectInfo fields precede the filename
        name = r.string() or "capture.jpg"
        code, _, data = self.t.read(GET_OBJECT, (CAPTURED_IMAGE_HANDLE,))
        self._check(code, "GetObject")
        return name, data

    def capture(self):
        """Trigger and download every resulting file (JPEG, plus RAW if enabled)."""
        self.trigger()
        files = []
        pending = self.wait_for_image()
        while pending:
            files.append(self.download_image())
            time.sleep(0.2)  # let the camera update its pending count
            pending = self.pending_images()
        return files
