# Not Triggerbot

A webcam-based quality-control tool for a conveyor line. It watches a
region of a USB camera's feed. When the color in that region mismatches
the expected color, it sends one keyboard or mouse action to the QC
software, without manual monitoring or key presses.

## About this version

This version reorganizes the existing behavior into focused modules. It
retains HSV color matching, edge-triggered firing with consecutive-frame
confirmation and a cooldown, click-to-record input capture, and the
numbered 1-7 setup flow. It also adds live performance readouts, a
latency self-test, and preview options that reduce processing overhead.
See **Performance** and **Camera latency** for details.

## Features

- HSV-based color-mismatch detection over a region you draw on the live preview
- Edge-triggered firing: one fire per flagged product, re-arms once the color leaves the region, plus a configurable cooldown as a second guard against rapid re-firing
- Consecutive-frame confirmation to ignore single-frame noise
- Click-to-record trigger action: press the key or mouse button your QC software expects once to bind it
- Live camera controls: resolution and frame-rate presets, brightness/contrast/saturation/exposure sliders applied without restarting the stream
- A preview you can run full, cheap (throttled and downscaled), or off entirely; detection runs at full speed in every mode
- Live, measured performance stats: achieved fps, processing time, dispatch time, and an estimated pipeline latency
- A latency self-test that measures real glass-to-detection delay on your actual camera, not a guess
- Config saved to `config.json`, debounced so dragging a slider doesn't hit disk every frame
- A rotating log file next to `config.json`, so a crash on the floor leaves a trace instead of just vanishing

## Install

Requires Python 3.12 on Windows (this targets DirectShow/UVC webcams
through OpenCV; it hasn't been tested on macOS or Linux).

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

If your machine has more than one Python install, use the full
interpreter path for both commands rather than plain `python`, or run
`run.bat` (included). It creates or updates a local virtual environment
and launches the app from it, avoiding the wrong interpreter.

## Run

```
python main.py
```

or double-click `run.bat`.

On first run there's no `config.json` yet, so the app starts from
defaults; `config.example.json` shows the shape of a filled-in config if
you want to hand-edit or template one. Work through the panels top to
bottom: pick the camera, set resolution/frame rate, dial in image
adjustments if needed, draw the region of interest on the preview,
sample the target color and set tolerance/threshold, record the trigger
action, then press Start.

## Camera latency

Frame timing can be calculated from the requested frame rate. End-to-end
latency also depends on the camera and driver, so it must be measured on
the hardware in use.

**Frame period and image size:**

| Mode | Frame period | Pixel count | vs. 1080p |
|---|---|---|---|
| 1080p @ 30 fps | 33.3 ms | 2.07 MP | 1x |
| 1080p @ 60 fps | 16.7 ms | 2.07 MP | 1x |
| 4K UHD @ 30 fps | 33.3 ms | 8.29 MP | 4x |

Frame period is `1000 / fps` and does not depend on resolution. At the
same frame rate, 1080p30 and 4K30 both have a 33.3 ms frame period. A 4K
frame contains four times as many pixels as a 1080p frame, so it requires
four times the raw USB data and full-frame processing. This app crops to
the region of interest before doing color calculations, which limits
the effect of resolution on detection time. The Performance panel shows
the measured processing time.

For a sense of scale, 24-bit uncompressed 1080p30 video requires about
1.5 Gbit/s, and 4K30 requires about 6 Gbit/s. Both exceed USB 2.0's
480 Mbit/s ceiling, and 4K exceeds USB 3.0's 5 Gbit/s ceiling.
Webcams typically compress video to MJPEG at these resolutions, so
these figures are raw bandwidth estimates, not actual camera bitrates.
The bitrate depends on the camera's sensor and encoder.

**End-to-end latency varies by camera:**

Sensor exposure and readout, USB transfer, and driver buffering add
delay beyond the frame period. A Python process reading frames through
OpenCV cannot measure these delays separately. They vary by camera, USB
generation, port, and driver. Manufacturer latency specifications may
cover only the sensor and can understate end-to-end latency. For example,
a public bug report for an 85 fps USB camera on Windows described frames
arriving in bursts: 31 ms, then 0 ms, then 1 ms, instead of a steady
interval of about 12 ms. Buffering above the sensor caused the uneven
timing.

The app reports two latency measurements:

1. **Performance & latency panel:** Reports live achieved fps (which may
  differ from the requested rate), detection processing time, action
  dispatch time, and an estimated pipeline latency. The estimate adds
  those measurements to the theoretical frame period. It excludes
  sensor, USB, and driver delay because the process cannot measure them.
2. **Latency self-test** (button in the same panel): Point the camera at
  the displayed swatch. The test flashes the configured target color,
  measures how long the detector takes to report a match, and repeats
  for several samples. This round-trip measurement includes sensor,
  USB, and driver delay. It uses the same basic approach as existing
  webcam latency test scripts, such as
  [perrytsao/Webcam-Latency-Measurement](https://github.com/perrytsao/Webcam-Latency-Measurement):
  display a known change, read it back through the camera, and measure
  the delay.

Run the self-test for each resolution and frame-rate combination to
compare results on the camera in use.

## Performance

A few decisions worth knowing about if you're reading the code:

- **Detection never waits on the UI.** The camera thread grabs a frame,
  crops to the region of interest, converts just that crop to HSV, and
  decides whether to fire before doing any preview processing.
  A slow UI tick can't slow down detection.
- **Preview is opt-in cost.** "Off" skips the resize/color-convert/copy
  entirely, with zero extra work per frame. "Cheap" throttles redraws to a
  fixed rate (independent of camera fps) and downscales before sending
  anything across threads. "Full" sends every frame, clamped to a
  sensible max width so a 4K frame doesn't force a full-resolution
  PhotoImage conversion every tick.
- **A single-slot handoff, not a queue**, passes frames from the camera
  thread to the UI thread. The UI always gets the newest frame; a slow
  UI tick drops old ones instead of building a backlog that would make
  the preview lag further and further behind.
- **`CAP_PROP_BUFFERSIZE` is set to the minimum the backend allows**,
  and MJPEG is requested explicitly, both to keep the driver from
  queuing up frames that are already stale by the time we read them.
  Whether a given backend honors either request is outside this app's
  control. These settings are best-effort, not a guarantee.
- **Image adjustments (brightness/contrast/saturation/exposure) apply
  live**, no restart. Resolution and frame rate restart the camera
  stream, because changing them reliably usually needs the stream
  reopened rather than just a property set on the fly.
- **Config saves are debounced** (500 ms after the last change) and
  written atomically (write to a temp file, then replace), so dragging a
  slider doesn't hammer the disk and a crash mid-write can't leave a
  corrupt `config.json`.

## Project structure

```
main.py                        entry point: python main.py
nottrigger/
  app.py                       bootstrap: logging, config, Tk root
  config.py                    AppConfig dataclass, tolerant load/save
  constants.py                 presets, timing constants
  detection.py                 HSV matching, hue wraparound, firing state machine
  camera.py                    device listing, capture thread, preview handoff
  actions.py                   pynput-based record/dispatch for the trigger action
  latency.py                   frame-period math, rolling stats, self-test recorder
  logging_setup.py             rotating file log + uncaught-exception hook
  ui/
    theme.py                   palette, fonts, ttk styles
    widgets.py                 tooltip, numbered section, slider, stat row, scroll frame
    preview.py                 canvas rendering, ROI drag, color sampling
    calibration.py             latency self-test window
    main_window.py             assembles everything
tests/                         pure-logic unit tests (no camera/display needed)
config.example.json            a filled-in example config
run.bat                        creates/updates a venv and launches the app
```

## Running the tests

```
pip install pytest
pytest
```

These cover the parts that don't need a camera or a display: HSV bounds
(including hue wraparound for colors like red, which sits right at the
wrap point), the firing state machine, frame-period/pixel-count math,
and config load/save with partial or corrupt files.

## Troubleshooting

- **Wrong Python runs when you type `python`.** If this machine has more
  than one Python install, use the full interpreter path, or use
  `run.bat`, which pins its own virtual environment.
- **Camera not in the list.** Click Refresh in the Camera panel. If it's
  still missing, another application may be using it. Close anything
  else using the camera, then refresh again.
- **A brightness/contrast/exposure slider seems to do nothing, or clips
  immediately.** These ranges are a reasonable general default, not
  guaranteed for every camera. Some report brightness/contrast on a
  0-255 scale, others use something else entirely, and exposure in
  particular is often a small negative range (driver-dependent). If a
  slider isn't behaving, that's most likely this camera using a
  different scale rather than anything wrong with the setting itself.
- **Detector won't fire.** Check the live "match" percentage in the
  Target color panel against your match threshold. If it never crosses
  the threshold, widen the tolerance sliders or re-sample the
  color with better lighting on the actual product.
