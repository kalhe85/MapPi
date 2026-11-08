# MapPi

MapPi is a companion computer server for a small DIY mapping drone. It runs on a
Raspberry Pi Zero 2 W next to an ArduPilot flight controller and handles:

- **Geotagged photos for mapping**: the flight controller triggers a USB or CSI
  camera over MAVLink; each photo is tagged with the position at the moment the
  frame was captured.
- **Mapping outputs**: `geo.txt` (ready for OpenDroneMap/WebODM) and
  `captures.csv` (position, attitude and timing of every photo) next to the photos.
- **Field access over Wi-Fi**: the Pi runs its own hotspot. From a phone or laptop
  you open the web interface, check the camera and download a whole flight as a zip.
- **Telemetry for QGroundControl**: MAVLink is routed to UDP, so a ground station on
  the hotspot connects directly.

It is a personal, weekend DIY project, used internally only.

> [!IMPORTANT]
> **Based on [Rpanion-server](https://github.com/stephendade/Rpanion-server)**
> by Stephen Dade, released under the GNU GPL v3. MapPi is an independent fork:
> it is developed separately and is not affiliated with or supported by the
> Rpanion-server project. Please do not report MapPi issues to the original project.
> See [Credits and license](#credits-and-license).

## Hardware

| Part | Used in this project |
|------|----------------------|
| Companion computer | Raspberry Pi Zero 2 W (Raspberry Pi OS Bookworm/Trixie) |
| Flight controller | ArduPilot board (e.g. Flywoo GOKU F405 HD 1-2S AIO V2) |
| Camera | USB (UVC, MJPEG) camera module or Raspberry Pi CSI camera |
| GNSS | Any GPS connected to the flight controller |

Other boards supported by Rpanion-server (Raspberry Pi 3/4/5, Jetson Orin,
Orange Pi Zero 3) should still work, but are not tested for MapPi.

## Features

Mapping-specific (MapPi):

- Photo capture triggered by ArduPilot (MAVLink camera, RC switch, mission, or
  distance-based triggering)
- Position corrected to the frame capture time using the GPS velocity
- EXIF: GPS position, altitude, capture time with milliseconds, image direction
- `geo.txt` and `captures.csv` per media folder
- Fixed exposure setting to reduce motion blur
- MJPEG frames from USB cameras saved without re-encoding (lighter on the Pi Zero)
- Zip download of all media or one flight folder from the Log Browser

Inherited from Rpanion-server:

- Flight controller telemetry routing to UDP outputs (e.g. QGroundControl)
- Wi-Fi hotspot and network configuration
- Video streaming (RTSP) and recording
- Flight log (tlog/bin) management, NTRIP, VPN, cloud upload

## Installing (Raspberry Pi Zero 2 W)

Install from source on Raspberry Pi OS:

```bash
git clone --recurse-submodules https://github.com/kalhe85/MapPi.git
cd MapPi/deploy
./RasPi3-4-5-deploy.sh        # system packages, Node.js, swap, serial port, NetworkManager (reboots)
```

After the reboot, check that `free -h` shows about 1 GB of swap (needed to build
on a Pi Zero 2 W), then build and install the package:

```bash
cd ~/MapPi
./deploy/devExtras.sh         # downloads the mavlink-router binary to ~/MapPi/mavlink-routerd
npm ci
npm run build
npm run package               # includes ./mavlink-routerd in the package
sudo dpkg -i rpanion-server_*.deb
```

If downloading mavlink-router fails (e.g. GitHub returns 504), build it from source
instead (10–20 minutes on a Pi Zero 2 W); it installs to `/usr/local/bin/mavlink-routerd`,
which MapPi also finds:

```bash
sudo apt install -y git meson ninja-build pkg-config gcc g++ systemd-dev
git clone --recurse-submodules https://github.com/mavlink-router/mavlink-router.git ~/mavlink-router
cd ~/mavlink-router
meson setup build .
ninja -C build
sudo ninja -C build install
```

The web interface is then at `http://<device_ip>:3001`. To set up the Wi-Fi
hotspot, see `deploy/wifi_access_point.sh` or the Network page.

> [!NOTE]
> Internal names are unchanged from Rpanion-server so the install scripts keep
> working: the package and systemd service are still called `rpanion-server`,
> and the config is in `/etc/rpanion-server`. Logs: `journalctl -u rpanion-server`.

## Mapping quick start

1. **Flight Controller page**: select the serial port to the flight controller and
   enable **datastream requests** (needed for geotagging).
2. **Video page**: choose **Photo** mode, the camera and resolution, a **Media
   Destination** per flight (e.g. `flight_01`) and a fixed exposure (e.g. 1000 µs
   in daylight).
3. **ArduPilot**: set up a MAVLink camera so the flight controller can trigger
   photos (e.g. by distance with `CAM1_TRIGG_DIST`), or trigger from QGroundControl
   or an RC switch.
4. Fly slowly (2–3 m/s) with 75–80% overlap, camera pointing straight down.
5. **Log Browser page**: download the flight folder as a zip and load the photos
   (with `geo.txt`) into WebODM.

## Default username and password

The default username is `admin` and password `admin`. Change it on the
"User Management" page. Access control does *not* apply to MAVLink or video
streams. Media files (`/media`, `/mediazip`) can be downloaded by anyone on the
network, so use a password on the Wi-Fi hotspot.

## Development

Follow the scripts in `deploy/` to set up the environment, then run
`deploy/devExtras.sh` to download `mavlink-router`.

```bash
npm run dev          # backend on :3001, frontend on :3000, no login checks
npm run testback     # backend tests
npm run testfront    # frontend tests
npm run lint
python3 -m unittest discover -s python -p 'test_*.py'   # mapping helpers
```

Only use `npm run dev` during development, as it skips the login checks.

## Credits and license

MapPi is a modified version of **Rpanion-server**, created by
**Stephen Dade** and its contributors:

- Source: https://github.com/stephendade/Rpanion-server
- Documentation: https://www.docs.rpanion.com/software/rpanion-server

Most of this code base (web interface, MAVLink routing, networking, video,
logging and the original photo/geotag feature) is their work. Thank you!

MapPi changes (from November 2026) by Sonph: mapping-focused photo capture,
geotag timing, `geo.txt`/`captures.csv`, zip download, fixed exposure and this
rebranding.

This program is free software: you can redistribute it and/or modify it under
the terms of the **GNU General Public License version 3**, as published by the
Free Software Foundation. It is distributed WITHOUT ANY WARRANTY. See
[LICENSE](LICENSE) for the full text.
