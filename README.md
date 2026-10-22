# sonyrx0

Control Sony RX0 cameras over USB from Windows (no Sony SDK, no driver changes),
capture synchronised stereo pairs, calibrate the pair and compute depth maps.

Generic camera/stereo toolkit: application code (e.g. seed phenotyping) lives in
its own repo and installs this package.

## Install

```
conda activate rx0stereo            # or any Python 3.10+ env
pip install -e .                    # from this folder
```

## Cameras

Set each camera to MENU → Setup → USB Connection → **PC Remote** and use a
data-capable micro-USB cable (many are charge-only).

```
rx0-settings                                         # show settings of all connected cameras
rx0-settings --mode m --shutter 1/250 --iso 800      # set ALL cameras identically
rx0-settings --focus-lock                            # AF on the subject now, then lock (MF)
rx0-capture --out captures                           # one camera: fire + download
rx0-stereo --out data/scene                          # both: fire together, save left/ right/
rx0-stereo --out data/calib -n 30 --board --beep     # calibration session with feedback
rx0-stereo --swap                                    # fix left/right (remembered in ./rig.json)
```

Settings: `--mode auto|superior-auto|p|m`, `--shutter 1/4..1/32000` (M),
`--iso auto|80..12800`, `--ev ±3`, `--focus manual|af`,
`--wb auto|daylight|shade|cloudy|tungsten|kelvin` (+ `--kelvin`), `--size l|m|s`.

How it works: in PC Remote mode Windows binds the camera to its stock MTP driver.
`camera/wpd_mtp.py` sends raw PTP operations through WPD's MTP-extension
commands, and `camera/sony.py` speaks Sony's PTP vendor extension (SDIO
handshake, SetControlDevice A/B, in-memory capture object 0xFFFFC001), as in
libgphoto2. Sony's Camera Remote SDK does not support the original RX0.

Notes: shots are software-triggered (~0.1 ms apart on the PC side; the cameras'
own latency adds to that), so keep subjects still. The camera holds one capture
in memory at a time; `rx0-stereo` clears leftovers on connect. Settings changes
take ~0.5 s and are verified by reading back.

## Calibration

1. Board: `rx0-board --screen 2360x1640 --out board.png` (tablet, full screen) or
   `rx0-board --square-mm 24` (print at 100%). Measure across all 11 squares of a
   row with black squares at both ends; divide by 11.
2. Lock focus at the working distance, fix exposure, then shoot ~30 pairs with the
   board (or the rig) still at the moment of the beep, covering the whole frame,
   tilted and at varied distances.
3. `rx0-calibrate --left data/calib/left --right data/calib/right --square-mm 14.9 --out calib.npz`
   Pairs where something moved between exposures are dropped automatically.
   Aim for stereo RMS < 1 px (< 0.5 px is good).

Refocusing or knocking a camera invalidates the calibration.

## Depth

```
rx0-depth --calib calib.npz --left data/scene/left --right data/scene/right \
    --min-depth 250 --max-depth 1000 --out out/scene --ply
```

Writes rectified check image, colourised disparity, depth (`.npy` float mm,
16-bit PNG) and optional coloured point cloud. Defaults are tuned for
measurement: no WLS smoothing (it blurs thin edges; `--wls` for nicer pictures)
and flying-pixel removal at depth edges (`--edge-jump 0` disables).

From Python:

```python
from sonyrx0.stereo.depth import StereoRig, compute_depth
rig = StereoRig("calib.npz", scale=0.5)
rect_l, rect_r, disp, xyz = compute_depth(rig, img_l, img_r, min_depth=250, max_depth=800)
```

## Other

- `trigger/`: Arduino + optocoupler box to fire the shared hardware shutter cable from the PC.
- `tools/wifi_probe.py`: probe a camera's Wi-Fi AP for remote-control services.
- `tests/synthetic_rig.py`: synthetic calibrate→depth regression test (prints PASS/FAIL).
