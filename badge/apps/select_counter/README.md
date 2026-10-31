# SELECT Counter

SELECT Counter counts how many times you press SELECT.

## Usage

Open **SELECT Counter** from the badge menu. The number starts at zero.
Press SELECT to add one. Holding SELECT counts once, not repeatedly;
release it before pressing again. The physical SELECT button and SELECT
touch pad use the same logical action, including when the badge is inverted.

Press HOME to return to the menu. Reopening the app or restarting the badge
starts a new count at zero; the count is not saved.

## Requirements

GitHub Universe 2026 badge firmware. No extra hardware, Wi-Fi connection,
credentials, or Python packages are needed on the badge.

## Try and install

In the GitHub Copilot app, open the Badge Simulator for `select_counter`.
Try SELECT there first. When you are happy with it, connect your badge with
a data-capable USB-C cable and press the canvas **Deploy** button.
Deployment must use USB Disk Mode because `/system` is read-only over serial.

For manual installation and debugging, follow the repository's
[USB serial guide](../../../hardware/USB_SERIAL.md). Copy only this app's
folder into `apps/` on the BADGER volume, not the RP2350 bootloader drive.
If `select_counter` already exists on your badge, do not overwrite it without
reviewing the existing version and keeping a backup. Eject BADGER safely,
restart to the menu, and confirm the app's icon and controls on the badge.
