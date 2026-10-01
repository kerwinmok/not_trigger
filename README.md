# Not Triggerbot

A small Windows app that watches part of a webcam image for a color change and sends a keyboard or mouse action when it detects one.

## Download

[Download NotTriggerbot.exe](https://github.com/kerwinmok/not_trigger/releases/latest/download/NotTriggerbot.exe), then open it. The app does not need to be installed. It saves its settings and log beside the executable. Close the window to stop the app.

## Setup

1. Choose your camera and set its resolution and frame rate. Click **Apply**.
2. Click **Start** to open the camera preview.
3. Draw a box around the part of the image to watch.
4. Select **Sample color**, then click the product color in the preview.
5. Record the key or mouse button used by your QC software.

The preview shows an estimated latency while the app is running. The estimate uses the camera's frame timing and the app's detection and action time. It does not measure the camera's full sensor-to-screen delay.

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
- On first launch, Windows may ask you to confirm that you want to run the downloaded app.