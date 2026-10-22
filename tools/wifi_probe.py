"""Probe an RX0 II's Wi-Fi access point to see which remote-control interfaces it exposes.

Run this while the PC is joined to the camera's "DIRECT-xxxx:DSC-RX0M2" network.
Everything is written to wifi_probe.txt, so you can rejoin your normal Wi-Fi
afterwards and inspect the results.

    python tools/wifi_probe.py
"""
import json
import re
import socket
import subprocess
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "wifi_probe.txt"
# 10000/8080: Sony Camera Remote API (JSON-RPC); 15740: PTP/IP; 64321/60152: UPnP / Sony services.
PORTS = [80, 443, 5000, 8080, 10000, 15740, 52000, 60152, 61000, 64321]
SSDP_TARGETS = ["urn:schemas-sony-com:service:ScalarWebAPI:1", "ssdp:all"]

lines = []


def log(msg=""):
    print(msg)
    lines.append(str(msg))


def gateway_ip():
    out = subprocess.run(["ipconfig"], capture_output=True, text=True).stdout
    log("== ipconfig ==")
    log(out)
    gws = re.findall(r"Default Gateway[ .]*: ([\d.]+)", out)
    return gws[0] if gws else None


def ssdp(target, timeout=4):
    msg = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\n"
           f'MAN: "ssdp:discover"\r\nMX: 2\r\nST: {target}\r\n\r\n').encode()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.settimeout(timeout)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    s.sendto(msg, ("239.255.255.250", 1900))
    replies = []
    try:
        while True:
            data, addr = s.recvfrom(65535)
            replies.append((addr, data.decode(errors="replace")))
    except socket.timeout:
        pass
    return replies


def http_get(url, timeout=4):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read().decode(errors="replace")


def json_rpc(url, method, params=None, version="1.0"):
    body = json.dumps({"method": method, "params": params or [], "id": 1, "version": version}).encode()
    req = urllib.request.Request(url, body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=4) as r:
        return r.read().decode(errors="replace")


def main():
    gw = gateway_ip()
    log(f"Gateway (camera?): {gw}")

    locations = set()
    for target in SSDP_TARGETS:
        log(f"\n== SSDP {target} ==")
        for addr, text in ssdp(target):
            log(f"-- from {addr}\n{text}")
            m = re.search(r"(?im)^location:\s*(\S+)", text)
            if m:
                locations.add(m.group(1))

    api_urls = set()
    for loc in sorted(locations):
        log(f"\n== Device description {loc} ==")
        try:
            xml = http_get(loc)
            log(xml)
            for base in re.findall(r"<av:X_ScalarWebAPI_ActionList_URL>(.*?)</", xml):
                for svc in re.findall(r"<av:X_ScalarWebAPI_ServiceType>(.*?)</", xml):
                    api_urls.add(f"{base.rstrip('/')}/{svc}")
        except Exception as e:  # noqa: BLE001 - record and keep probing
            log(f"failed: {e}")

    hosts = sorted({gw} | {re.sub(r"^https?://([^:/]+).*", r"\1", u) for u in locations} - {None})
    for host in hosts:
        log(f"\n== TCP ports on {host} ==")
        for port in PORTS:
            s = socket.socket()
            s.settimeout(1.5)
            state = "OPEN" if s.connect_ex((host, port)) == 0 else "closed"
            s.close()
            log(f"{port:>6}: {state}")
            if state == "OPEN" and port in (8080, 10000):
                api_urls.add(f"http://{host}:{port}/sony/camera")

    for url in sorted(api_urls):
        log(f"\n== JSON-RPC {url} ==")
        for method in ("getVersions", "getAvailableApiList", "getApplicationInfo"):
            try:
                log(f"{method}: {json_rpc(url, method)}")
            except Exception as e:  # noqa: BLE001
                log(f"{method}: failed: {e}")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nSaved {OUT}")


if __name__ == "__main__":
    main()
