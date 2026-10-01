# Not Triggerbot

A small Windows app that watches a webcam region for a color and sends a keyboard or mouse action when it matches.

## Download

[Download NotTriggerbot.exe](https://github.com/kerwinmok/not_trigger/releases/latest/download/NotTriggerbot.exe), then open it. The app does not need to be installed. It saves its settings and log beside the executable. Close the window to stop the app.

## Setup

1. Choose your camera and click **Start**. It opens at the camera's default resolution and frame rate.
2. Choose a region shape. Drag a rectangle, click a point, or set a circle radius and click its center.
3. Select **Sample color**, then click the color to watch in the preview.
4. Click **Record action**, then press the key or mouse button to send. A left click is saved as **Mouse: LMB**.

The latency estimate uses recent camera frame timing and the app's detection and action time. It does not include the camera's full sensor-to-screen delay. **Preview** turns off preview processing while detection continues. **Low CPU preview** updates a smaller preview less often. The FPS override is optional; leave it blank to use the camera default. Resolution always stays at the camera default.

Type a number into any slider's value field for a precise setting, or drag the slider.

Recorded mouse actions are sent through Windows as system mouse input at the current pointer position. They are not DOM clicks, though Windows marks software-generated input as injected.

## Run From Source

Install Python 3.12, download the repository, and double-click `run.bat`. It creates a local environment, installs the dependencies, and starts the app.

Run tests with:

```powershell
py -3.12 -m pip install pytest
py -3.12 -m pytest
```

## Troubleshooting

- If the camera is missing, click **Refresh** and close other apps that may be using it.
- If the trigger does not fire, check that the region and sampled color are correct, then lower the match threshold or adjust the color tolerances.
- To test a recorded action in another app, leave that app active and put the pointer where the click should land before the trigger fires.
- On first launch, Windows may ask you to confirm that you want to run the downloaded app.