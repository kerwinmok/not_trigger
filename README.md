# Not Triggerbot

A webcam-based quality-control tool for a conveyor line. It watches a
region of a USB camera's feed, and when the color in that region
mismatches what it's supposed to be, it fires a single keyboard or mouse
action into whatever QC software is running - no manual watching, no
manual key presses.

## About this version

This is a restructured rewrite: the same behavior as before (HSV color
matching, edge-triggered firing with consecutive-frame confirmation and
a cooldown, click-to-record input capture, a numbered 1-7 setup flow),
split across focused modules instead of one large script, plus three
things that didn't exist before - live performance readouts, a latency
self-test, and a preview you can turn off or cheapen for speed. See
**Performance** and **Camera latency** below for the reasoning.

## Features

- HSV-based color-mismatch detection over a region you draw on the live preview
- Edge-triggered firing: one fire per flagged product, re-arms once the color leaves the region, plus a configurable cooldown as a second guard against rapid re-firing
- Consecutive-frame confirmation to ignore single-frame noise
- Click-to-record trigger action - press the key or mouse button your QC software expects, once, and it's bound
- Live camera controls: resolution and frame-rate presets, brightness/contrast/saturation/exposure sliders applied without restarting the stream
- A preview you can run full, cheap (throttled and downscaled), or off entirely - detection runs at full speed regardless
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
`run.bat` (included), which creates/updates a local virtual environment
and launches the app from it - that sidesteps picking up the wrong
interpreter entirely.

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

You asked for real numbers on 1080p30 vs. 4K30 vs. 1080p60, so here's
what's actually knowable, split by how certain it is.

**Exact, from arithmetic alone - no camera needed to check this:**

| Mode | Frame period | Pixel count | vs. 1080p |
|---|---|---|---|
| 1080p @ 30 fps | 33.3 ms | 2.07 MP | 1x |
| 1080p @ 60 fps | 16.7 ms | 2.07 MP | 1x |
| 4K UHD @ 30 fps | 33.3 ms | 8.29 MP | 4x |

Frame period is `1000 / fps`, full stop - it doesn't know or care about
resolution. That's why 1080p30 and 4K30 land on the identical 33.3 ms:
same fps, same period. 4K's cost shows up elsewhere - it's exactly 4x
the pixels of 1080p (both dimensions double), which means 4x the raw
data crossing USB per frame and 4x the work for anything that processes
the full frame. This app crops to the region of interest before doing
any color math, so that second cost is mostly avoided - detection time
barely changes with resolution here, which you can watch happen live in
the Performance panel.

For a sense of scale: uncompressed 1080p30 video would need roughly 1
Gbit/s, and uncompressed 4K30 roughly 4 Gbit/s - well past USB 2.0's 480
Mbit/s ceiling either way, and 4K pushing close to USB 3.0's 5 Gbit/s
ceiling too. In practice your webcam compresses to MJPEG at these
resolutions rather than sending that much raw data, so treat those
numbers as "why compression and bandwidth limits matter here," not as
your camera's actual bitrate - that depends on the specific sensor and
encoder and isn't something this app (or anyone, without the datasheet
and a bus analyzer) can state in general.

**Real, but camera-specific - this is where an honest answer has to stop
guessing and go measure:**

Sensor exposure/readout, the USB transfer itself, and driver-level
buffering all add delay on top of the frame period, and none of it is
visible to a Python process reading frames with OpenCV. It varies by
camera, by USB generation and port, and by driver - which is also why a
manufacturer's advertised latency spec (often sensor-only, measured in a
lab) routinely understates what you'll see end to end. One real example,
from a public bug report about an 85 fps USB camera on Windows: instead
of a steady ~12 ms between frames, it arrived in bursts - 31 ms, then
0 ms, then 1 ms, repeating - because of buffering in the stack above the
sensor. Whether your camera behaves like that or arrives on a metronome
is genuinely not predictable from here.

So rather than hand you a single invented number, the app gives you two
real ones:

1. **Performance & latency panel** - live, while running: measured
   achieved fps (which can differ from what you requested), measured
   detection processing time, measured action-dispatch time, and those
   three summed with the theoretical frame period into an "estimated
   pipeline latency." This is accurate for what it covers, and it says
   so: it does *not* include sensor/USB/driver delay, because this
   process can't see that part.
2. **Latency self-test** (button in that same panel) - point the camera
   at the on-screen swatch it shows you, and it flashes your configured
   target color, times how long the running detector takes to report a
   match, and repeats for several samples. That number *does* include
   sensor, USB, and driver delay, because it's a real round trip through
   the actual hardware instead of only this process's own clock. This is
   the same basic idea as longstanding webcam-latency test scripts (e.g.
   [perrytsao/Webcam-Latency-Measurement](https://github.com/perrytsao/Webcam-Latency-Measurement)) -
   show a known instant on screen, read it back through the camera,
   measure the gap.

Run the self-test once at each resolution/frame-rate combo you're
considering and you'll have a real comparison for your actual camera,
instead of three numbers nobody could vouch for.

## Performance

A few decisions worth knowing about if you're reading the code:

- **Detection never waits on the UI.** The camera thread grabs a frame,
  crops to the region of interest, converts just that crop to HSV, and
  decides whether to fire - all before it does anything preview-related.
  A slow UI tick can't slow down detection.
- **Preview is opt-in cost.** "Off" skips the resize/color-convert/copy
  entirely - zero extra work per frame. "Cheap" throttles redraws to a
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
  Whether a given backend honors either of these is out of this app's
  control - it's a best-effort request, not a guarantee.
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
  still missing, another application may be holding it open - close
  anything else using the camera and refresh again.
- **A brightness/contrast/exposure slider seems to do nothing, or clips
  immediately.** These ranges are a reasonable general default, not
  guaranteed for every camera - some report brightness/contrast on a
  0-255 scale, others use something else entirely, and exposure in
  particular is often a small negative range (driver-dependent). If a
  slider isn't behaving, that's most likely this camera using a
  different scale rather than anything wrong with the setting itself.
- **Detector won't fire.** Check the live "match" percentage in the
  Target color panel against your match threshold - if it's never
  crossing the threshold, widen the tolerance sliders or re-sample the
  color with better lighting on the actual product.
