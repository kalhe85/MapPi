#!/usr/bin/env python3
# -*- coding:utf-8 vi:ts=4:noexpandtab
"""
Helpers for mapping / photogrammetry still capture.

Kept free of camera-library imports so they can be unit tested on any machine:
    python3 -m unittest python/test_mapping_utils.py

- Position at capture time: the flight controller position is sampled when the
  capture is triggered, but the frame is grabbed some time later (buffer
  flushes, camera latency). The position is dead-reckoned to the actual capture
  time using the GLOBAL_POSITION_INT velocity.
- Unique, millisecond-resolution file names so rapid triggers never overwrite
  each other.
- Raw UVC MJPEG frames often omit the Huffman tables (DHT); they are added back
  so the saved file is a standard JPEG.
- geo.txt (OpenDroneMap/WebODM) and captures.csv sidecar logs.
"""

import csv
import math
import os
import time
from datetime import datetime, timezone

EARTH_RADIUS_M = 6378137.0

# Ignore positions older than this, relative to the capture time
STALE_POSITION_S = 3.0
# Only dead-reckon over gaps up to this long
MAX_EXTRAPOLATION_S = 1.0

GEO_TXT = "geo.txt"
CAPTURES_CSV = "captures.csv"
CSV_HEADER = ["filename", "capture_time_utc", "lat", "lon", "alt_msl_m", "rel_alt_m",
              "heading_deg", "roll_deg", "pitch_deg", "yaw_deg", "position_age_s"]


def _num(data, key):
    """Return data[key] as float, or None if missing/invalid."""
    try:
        val = data.get(key)
        if val is None:
            return None
        val = float(val)
        if math.isnan(val):
            return None
        return val
    except (TypeError, ValueError):
        return None


def position_at_capture(data, capture_time):
    """
    Estimate the vehicle position at capture_time (seconds since epoch).

    data: dict written by Rpanion-server when the capture was triggered:
        lat, lon (deg), alt (MSL m), relAlt (m), hdg (deg),
        vx, vy, vz (m/s, north/east/down), timeMs (epoch ms when received),
        roll, pitch, yaw (deg, optional)

    Returns (position_dict, reason). position_dict is None if the data is
    unusable; reason explains why.
    """
    if not data:
        return None, "no position data"

    lat = _num(data, "lat")
    lon = _num(data, "lon")
    if lat is None or lon is None or (lat == 0 and lon == 0):
        return None, "no GPS fix (lat/lon are 0)"

    pos = {
        "lat": lat,
        "lon": lon,
        "alt": _num(data, "alt") or 0.0,
        "relAlt": _num(data, "relAlt"),
        "hdg": _num(data, "hdg"),
        "roll": _num(data, "roll"),
        "pitch": _num(data, "pitch"),
        "yaw": _num(data, "yaw"),
        "age": None,
    }

    # 655.35 deg means "heading unknown" in GLOBAL_POSITION_INT
    if pos["hdg"] is not None and not 0 <= pos["hdg"] <= 360:
        pos["hdg"] = None

    time_ms = _num(data, "timeMs")
    if time_ms is None:
        # Older server versions do not send a timestamp: use as-is
        return pos, "no timestamp, position not time-corrected"

    age = capture_time - time_ms / 1000.0
    pos["age"] = age
    if abs(age) > STALE_POSITION_S:
        return None, f"position is stale ({age:.2f}s old)"

    vx, vy, vz = _num(data, "vx"), _num(data, "vy"), _num(data, "vz")
    if vx is None or vy is None or abs(age) > MAX_EXTRAPOLATION_S:
        return pos, "position not time-corrected"

    north = vx * age
    east = vy * age
    pos["lat"] = lat + math.degrees(north / EARTH_RADIUS_M)
    pos["lon"] = lon + math.degrees(east / (EARTH_RADIUS_M * math.cos(math.radians(lat))))
    if vz is not None:
        # vz is positive down
        pos["alt"] -= vz * age
        if pos["relAlt"] is not None:
            pos["relAlt"] -= vz * age
    return pos, f"position corrected by {age * 1000:.0f} ms"


def to_deg(value, loc):
    """Converts decimal coordinates to EXIF-ready rational (deg, min, sec)"""
    loc_value = loc[1] if value < 0 else loc[0]
    # work in 1/10000 of an arc-second to avoid rounding to 60 seconds
    total = round(abs(value) * 3600 * 10000)
    deg, rem = divmod(total, 3600 * 10000)
    minutes, sec = divmod(rem, 60 * 10000)
    return ((int(deg), 1), (int(minutes), 1), (int(sec), 10000)), loc_value


def split_ms(t):
    """Split epoch seconds into (whole seconds, milliseconds) without float rounding errors."""
    total_ms = round(t * 1000)
    return total_ms // 1000, total_ms % 1000


def media_filename(capture_time, seq, ext):
    """RPNYYYYmmdd_HHMMSS_mmm_NNNN.ext (local time, as before plus ms and sequence)"""
    sec, ms = split_ms(capture_time)
    stamp = time.strftime("RPN%Y%m%d_%H%M%S", time.localtime(sec))
    return f"{stamp}_{ms:03d}_{seq:04d}.{ext}"


def unique_path(directory, filename):
    """Return a path in directory that does not exist yet."""
    path = os.path.join(directory, filename)
    base, ext = os.path.splitext(path)
    i = 1
    while os.path.exists(path):
        path = f"{base}_{i}{ext}"
        i += 1
    return path


def _segments(jpeg):
    """Yield (offset, marker_byte, segment_length) of JPEG header segments, up to SOS."""
    if len(jpeg) < 4 or jpeg[0:2] != b"\xff\xd8":
        return
    i = 2
    while i + 4 <= len(jpeg):
        if jpeg[i] != 0xFF:
            return
        marker = jpeg[i + 1]
        if marker == 0xFF:  # fill byte
            i += 1
            continue
        if marker == 0xDA:  # start of scan - header ends here
            yield i, marker, 0
            return
        length = int.from_bytes(jpeg[i + 2:i + 4], "big")
        yield i, marker, length
        i += 2 + length


def extract_dht(jpeg):
    """Return all DHT segments (markers included) from a JPEG, concatenated."""
    out = b""
    for offset, marker, length in _segments(jpeg):
        if marker == 0xC4:
            out += jpeg[offset:offset + 2 + length]
    return out


def ensure_dht(jpeg, dht):
    """Insert the DHT segment(s) before SOS if the JPEG has none (common for UVC MJPEG)."""
    sos = None
    for offset, marker, _ in _segments(jpeg):
        if marker == 0xC4:
            return jpeg
        if marker == 0xDA:
            sos = offset
    if sos is None or not dht:
        return jpeg
    return jpeg[:sos] + dht + jpeg[sos:]


def trim_after_eoi(jpeg):
    """Some UVC drivers pad the buffer after the End Of Image marker."""
    end = jpeg.rfind(b"\xff\xd9")
    return jpeg[:end + 2] if end > 0 else jpeg


def build_exif(piexif, pos, capture_time, existing=None):
    """Return an EXIF dict with capture time and (if pos) GPS tags."""
    exif = existing or {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}
    for ifd in ("0th", "Exif", "GPS", "1st"):
        exif.setdefault(ifd, {})

    sec, ms = split_ms(capture_time)
    local = datetime.fromtimestamp(sec)
    exif["Exif"][piexif.ExifIFD.DateTimeOriginal] = local.strftime("%Y:%m:%d %H:%M:%S")
    exif["Exif"][piexif.ExifIFD.SubSecTimeOriginal] = f"{ms:03d}"

    if pos:
        lat_deg, lat_ref = to_deg(pos["lat"], ["N", "S"])
        lon_deg, lon_ref = to_deg(pos["lon"], ["E", "W"])
        alt = pos.get("alt") or 0.0
        utc = datetime.fromtimestamp(sec, tz=timezone.utc)
        gps = {
            piexif.GPSIFD.GPSVersionID: (2, 3, 0, 0),
            piexif.GPSIFD.GPSLatitudeRef: lat_ref,
            piexif.GPSIFD.GPSLatitude: lat_deg,
            piexif.GPSIFD.GPSLongitudeRef: lon_ref,
            piexif.GPSIFD.GPSLongitude: lon_deg,
            piexif.GPSIFD.GPSAltitudeRef: 0 if alt >= 0 else 1,
            piexif.GPSIFD.GPSAltitude: (int(abs(alt) * 100), 100),
            piexif.GPSIFD.GPSDateStamp: utc.strftime("%Y:%m:%d"),
            piexif.GPSIFD.GPSTimeStamp: ((utc.hour, 1), (utc.minute, 1),
                                         (utc.second * 1000 + ms, 1000)),
        }
        heading = pos.get("hdg")
        if heading is None and pos.get("yaw") is not None:
            heading = pos["yaw"] % 360
        if heading is not None:
            gps[piexif.GPSIFD.GPSImgDirectionRef] = "T"
            gps[piexif.GPSIFD.GPSImgDirection] = (int(heading * 100), 100)
        exif["GPS"] = gps
    return exif


def _fmt(val, digits):
    return "" if val is None else f"{val:.{digits}f}"


def append_capture_logs(directory, filename, pos, capture_time):
    """Append to captures.csv (every photo) and geo.txt (photos with a position)."""
    csv_path = os.path.join(directory, CAPTURES_CSV)
    new_csv = not os.path.exists(csv_path)
    sec, ms = split_ms(capture_time)
    utc = datetime.fromtimestamp(sec, tz=timezone.utc)
    p = pos or {}
    with open(csv_path, "a", newline="") as f:
        writer = csv.writer(f)
        if new_csv:
            writer.writerow(CSV_HEADER)
        writer.writerow([
            filename,
            utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ms:03d}Z",
            _fmt(p.get("lat"), 8), _fmt(p.get("lon"), 8),
            _fmt(p.get("alt"), 3), _fmt(p.get("relAlt"), 3),
            _fmt(p.get("hdg"), 2), _fmt(p.get("roll"), 2),
            _fmt(p.get("pitch"), 2), _fmt(p.get("yaw"), 2),
            _fmt(p.get("age"), 3),
        ])

    if pos:
        # OpenDroneMap geo.txt: first line is the projection, then
        # "image_name geo_x geo_y geo_z" (x = longitude, y = latitude)
        geo_path = os.path.join(directory, GEO_TXT)
        new_geo = not os.path.exists(geo_path)
        with open(geo_path, "a") as f:
            if new_geo:
                f.write("EPSG:4326\n")
            f.write(f"{filename} {pos['lon']:.8f} {pos['lat']:.8f} {pos.get('alt') or 0.0:.3f}\n")
