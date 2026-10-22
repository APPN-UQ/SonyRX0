"""Raw PTP/MTP transactions through the stock Windows MTP driver (WPD), no libusb needed.

Uses the WPD "MTP extension" commands, which pass arbitrary (vendor) operation
codes straight to the device.
"""
import ctypes
from ctypes import POINTER, byref, c_ubyte, c_ulong, c_void_p, c_wchar_p, cast, pointer, wintypes

import comtypes
import comtypes.client as cc

_api = cc.GetModule("portabledeviceapi.dll")
_types = cc.GetModule("portabledevicetypes.dll")
_StdGUID = _api._tagpropertykey._fields_[0][1]  # the typelib's own GUID struct

GUID_DEVINTERFACE_WPD = comtypes.GUID("{6AC27878-A6FA-4155-BA85-F98F491D4F33}")
_COMMON = "{F0422A9C-5DC8-4440-B5BD-5DF28835658A}"
_MTPX = "{4D545058-1A2E-4106-A357-771E0819FC56}"  # WPD_CATEGORY_MTP_EXT_VENDOR_OPERATIONS
_CLIENT = "{204D9F0C-2292-4080-9F42-40664E70F859}"

VT_UI4 = 19


def _key(fmtid, pid):
    k = _api._tagpropertykey()
    g = comtypes.GUID(fmtid)
    ctypes.memmove(byref(k.fmtid), byref(g), ctypes.sizeof(g))
    k.pid = pid
    return k


def _guid(fmtid):
    g = comtypes.GUID(fmtid)
    return cast(pointer(g), POINTER(_StdGUID))


CMD_CATEGORY = _key(_COMMON, 1001)
CMD_ID = _key(_COMMON, 1002)
CMD_HRESULT = _key(_COMMON, 1003)

EXEC_NO_DATA = 12
EXEC_DATA_READ = 13
EXEC_DATA_WRITE = 14
READ_DATA = 15
WRITE_DATA = 16
END_TRANSFER = 17

OP_CODE = _key(_MTPX, 1001)
OP_PARAMS = _key(_MTPX, 1002)
RESP_CODE = _key(_MTPX, 1003)
RESP_PARAMS = _key(_MTPX, 1004)
XFER_CONTEXT = _key(_MTPX, 1006)
XFER_TOTAL = _key(_MTPX, 1007)
XFER_TO_READ = _key(_MTPX, 1008)
XFER_READ = _key(_MTPX, 1009)
XFER_TO_WRITE = _key(_MTPX, 1010)
XFER_DATA = _key(_MTPX, 1012)

CLIENT_NAME = _key(_CLIENT, 2)
CLIENT_MAJOR = _key(_CLIENT, 3)
CLIENT_MINOR = _key(_CLIENT, 4)
CLIENT_REVISION = _key(_CLIENT, 5)

_ole32 = ctypes.OleDLL("ole32")
_ole32.CoTaskMemFree.argtypes = [c_void_p]


class PTPError(RuntimeError):
    pass


def find_devices(vid="054c"):
    """Device-interface paths of connected WPD devices whose path contains the USB vendor id."""
    cfg = ctypes.WinDLL("cfgmgr32")
    size = c_ulong()
    guid = GUID_DEVINTERFACE_WPD
    if cfg.CM_Get_Device_Interface_List_SizeW(byref(size), byref(guid), None, 0):
        return []
    buf = ctypes.create_unicode_buffer(size.value)
    if cfg.CM_Get_Device_Interface_ListW(byref(guid), None, buf, size.value, 0):
        return []
    paths = [p for p in ctypes.wstring_at(ctypes.addressof(buf), size.value).split("\0") if p]
    return [p for p in paths if f"vid_{vid}" in p.lower()]


class WpdMtp:
    def __init__(self, device_path):
        comtypes.CoInitialize()
        info = cc.CreateObject(_types.PortableDeviceValues, interface=_api.IPortableDeviceValues)
        info.SetStringValue(CLIENT_NAME, "rx0-remote")
        info.SetUnsignedIntegerValue(CLIENT_MAJOR, 1)
        info.SetUnsignedIntegerValue(CLIENT_MINOR, 0)
        info.SetUnsignedIntegerValue(CLIENT_REVISION, 0)
        self.dev = cc.CreateObject(_api.PortableDevice, interface=_api.IPortableDevice)
        self.dev.Open(device_path, info)

    def close(self):
        self.dev.Close()

    # --- plumbing -------------------------------------------------------
    def _values(self, cmd_pid):
        v = cc.CreateObject(_types.PortableDeviceValues, interface=_api.IPortableDeviceValues)
        v.SetGuidValue(CMD_CATEGORY, _guid(_MTPX))
        v.SetUnsignedIntegerValue(CMD_ID, cmd_pid)
        return v

    def _send(self, values):
        res = self.dev.SendCommand(0, values)
        hr = res.GetErrorValue(CMD_HRESULT)
        if hr < 0:
            raise PTPError(f"WPD command failed, HRESULT 0x{hr & 0xFFFFFFFF:08X}")
        return res

    def _with_op(self, cmd_pid, op, params):
        v = self._values(cmd_pid)
        v.SetUnsignedIntegerValue(OP_CODE, op)
        coll = cc.CreateObject(_types.PortableDevicePropVariantCollection,
                               interface=_api.IPortableDevicePropVariantCollection)
        for p in params:
            pv = _api.tag_inner_PROPVARIANT()
            pv.vt = VT_UI4
            ctypes.cast(ctypes.addressof(pv) + 8, POINTER(c_ulong))[0] = p
            coll.Add(byref(pv))
        v.SetIPortableDevicePropVariantCollectionValue(OP_PARAMS, coll)
        return v

    @staticmethod
    def _response(res):
        code = res.GetUnsignedIntegerValue(RESP_CODE)
        params = []
        try:
            coll = res.GetIPortableDevicePropVariantCollectionValue(RESP_PARAMS)
            count = c_ulong()
            coll.GetCount(byref(count))  # typelib marks the count as [in], so pass it explicitly
            for i in range(count.value):
                pv = _api.tag_inner_PROPVARIANT()
                coll.GetAt(i, byref(pv))
                params.append(ctypes.cast(ctypes.addressof(pv) + 8, POINTER(c_ulong))[0])
        except comtypes.COMError:
            pass
        return code, params

    def _end(self, ctx):
        v = self._values(END_TRANSFER)
        v.SetStringValue(XFER_CONTEXT, ctx)
        return self._response(self._send(v))

    # --- public: one PTP transaction ----------------------------------
    def command(self, op, params=()):
        return self._response(self._send(self._with_op(EXEC_NO_DATA, op, params)))

    def read(self, op, params=(), chunk=1 << 20):
        res = self._send(self._with_op(EXEC_DATA_READ, op, params))
        ctx = res.GetStringValue(XFER_CONTEXT)
        total = res.GetUnsignedLargeIntegerValue(XFER_TOTAL)
        out = bytearray()
        while len(out) < total:
            n = min(chunk, total - len(out))
            v = self._values(READ_DATA)
            v.SetStringValue(XFER_CONTEXT, ctx)
            v.SetUnsignedIntegerValue(XFER_TO_READ, n)
            buf = (c_ubyte * n)()
            v.SetBufferValue(XFER_DATA, buf, n)
            r = self._send(v)
            got = r.GetUnsignedIntegerValue(XFER_READ)
            ptr, size = r.GetBufferValue(XFER_DATA)
            out += ctypes.string_at(ptr, min(got, size))
            _ole32.CoTaskMemFree(ptr)
            if got == 0:
                break
        code, rparams = self._end(ctx)
        return code, rparams, bytes(out)

    def write(self, op, params, data):
        v = self._with_op(EXEC_DATA_WRITE, op, params)
        v.SetUnsignedLargeIntegerValue(XFER_TOTAL, len(data))
        ctx = self._send(v).GetStringValue(XFER_CONTEXT)
        w = self._values(WRITE_DATA)
        w.SetStringValue(XFER_CONTEXT, ctx)
        w.SetUnsignedIntegerValue(XFER_TO_WRITE, len(data))
        buf = (c_ubyte * len(data)).from_buffer_copy(data)
        w.SetBufferValue(XFER_DATA, buf, len(data))
        self._send(w)
        return self._end(ctx)
