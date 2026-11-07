#!/usr/bin/env python3
"""Unit tests for mapping_utils. Run: python3 -m unittest discover -s python -p 'test_*.py'"""

import csv
import os
import tempfile
import unittest

import mapping_utils as mu


class PositionAtCaptureTest(unittest.TestCase):
    BASE = {"lat": 21.0, "lon": 105.8, "alt": 50.0, "relAlt": 30.0, "hdg": 90.0,
            "vx": 0.0, "vy": 3.0, "vz": -1.0, "timeMs": 1_000_000_000_000,
            "roll": 1.0, "pitch": -2.0, "yaw": 90.0}

    def test_no_fix(self):
        pos, _ = mu.position_at_capture({"lat": 0, "lon": 0}, 0)
        self.assertIsNone(pos)
        pos, _ = mu.position_at_capture(None, 0)
        self.assertIsNone(pos)

    def test_legacy_data_without_timestamp(self):
        pos, _ = mu.position_at_capture({"lat": 21.0, "lon": 105.8, "alt": 10}, 123.0)
        self.assertEqual((pos["lat"], pos["lon"], pos["alt"]), (21.0, 105.8, 10.0))
        self.assertIsNone(pos["age"])

    def test_stale(self):
        t = self.BASE["timeMs"] / 1000 + mu.STALE_POSITION_S + 0.1
        pos, reason = mu.position_at_capture(self.BASE, t)
        self.assertIsNone(pos)
        self.assertIn("stale", reason)

    def test_dead_reckoning_east_and_up(self):
        # 0.2 s after the sample, flying east at 3 m/s and climbing at 1 m/s
        t = self.BASE["timeMs"] / 1000 + 0.2
        pos, _ = mu.position_at_capture(self.BASE, t)
        self.assertAlmostEqual(pos["lat"], 21.0, places=9)
        east_m = (pos["lon"] - 105.8) * 3.141592653589793 / 180 * mu.EARTH_RADIUS_M * 0.9335804264972017
        self.assertAlmostEqual(east_m, 0.6, places=3)
        self.assertAlmostEqual(pos["alt"], 50.2, places=6)
        self.assertAlmostEqual(pos["relAlt"], 30.2, places=6)
        self.assertAlmostEqual(pos["age"], 0.2, places=6)

    def test_negative_age_moves_backwards(self):
        # sample newer than the capture (file overwritten by a later trigger)
        t = self.BASE["timeMs"] / 1000 - 0.1
        pos, _ = mu.position_at_capture(self.BASE, t)
        self.assertLess(pos["lon"], 105.8)

    def test_unknown_heading(self):
        data = dict(self.BASE, hdg=655.35)
        pos, _ = mu.position_at_capture(data, self.BASE["timeMs"] / 1000)
        self.assertIsNone(pos["hdg"])


class ToDegTest(unittest.TestCase):
    def test_no_60_seconds_rounding(self):
        (d, m, s), ref = mu.to_deg(-105.99999999, ["E", "W"])
        self.assertEqual(ref, "W")
        self.assertEqual(d, (106, 1))
        self.assertEqual(m, (0, 1))
        self.assertLess(s[0] / s[1], 60)

    def test_value(self):
        (d, m, s), ref = mu.to_deg(21.0285, ["N", "S"])
        self.assertEqual(ref, "N")
        val = d[0] + m[0] / 60 + s[0] / s[1] / 3600
        self.assertAlmostEqual(val, 21.0285, places=7)


class FilenameTest(unittest.TestCase):
    def test_unique_names_within_same_second(self):
        a = mu.media_filename(1_700_000_000.100, 1, "jpg")
        b = mu.media_filename(1_700_000_000.900, 2, "jpg")
        self.assertNotEqual(a, b)
        self.assertTrue(a.startswith("RPN") and a.endswith("_100_0001.jpg"))

    def test_unique_path(self):
        with tempfile.TemporaryDirectory() as d:
            p1 = mu.unique_path(d, "a.jpg")
            open(p1, "w").close()
            p2 = mu.unique_path(d, "a.jpg")
            self.assertNotEqual(p1, p2)
            self.assertTrue(p2.endswith("a_1.jpg"))


class JpegTest(unittest.TestCase):
    DHT = b"\xff\xc4\x00\x04\x00\x01"  # fake segment with a 2-byte payload

    def jpeg(self, with_dht):
        app0 = b"\xff\xe0\x00\x04AB"
        dqt = b"\xff\xdb\x00\x03\x01"
        sos = b"\xff\xda\x00\x02"
        body = (app0 + dqt + (self.DHT if with_dht else b"") + sos + b"\x12\x34\xff\xd9")
        return b"\xff\xd8" + body

    def test_insert_missing_dht_before_sos(self):
        out = mu.ensure_dht(self.jpeg(False), self.DHT)
        self.assertEqual(out, self.jpeg(True))

    def test_keep_existing_dht(self):
        src = self.jpeg(True)
        self.assertEqual(mu.ensure_dht(src, b"\xff\xc4\x00\x02"), src)

    def test_extract(self):
        self.assertEqual(mu.extract_dht(self.jpeg(True)), self.DHT)

    def test_trim_padding(self):
        self.assertEqual(mu.trim_after_eoi(self.jpeg(False) + b"\x00\x00"), self.jpeg(False))


class LogsTest(unittest.TestCase):
    def test_geo_txt_and_csv(self):
        pos = {"lat": 21.1, "lon": 105.9, "alt": 12.5, "relAlt": 10.0, "hdg": 45.0,
               "roll": 0.5, "pitch": -1.0, "yaw": 45.0, "age": 0.15}
        with tempfile.TemporaryDirectory() as d:
            mu.append_capture_logs(d, "a.jpg", pos, 1_700_000_000.25)
            mu.append_capture_logs(d, "b.jpg", None, 1_700_000_001.25)
            with open(os.path.join(d, mu.GEO_TXT)) as f:
                lines = f.read().splitlines()
            self.assertEqual(lines, ["EPSG:4326", "a.jpg 105.90000000 21.10000000 12.500"])
            with open(os.path.join(d, mu.CAPTURES_CSV)) as f:
                rows = list(csv.reader(f))
            self.assertEqual(rows[0], mu.CSV_HEADER)
            self.assertEqual(rows[1][0], "a.jpg")
            self.assertEqual(rows[1][1], "2023-11-14T22:13:20.250Z")
            self.assertEqual(rows[2][0], "b.jpg")
            self.assertEqual(rows[2][2], "")


@unittest.skipUnless(__import__("importlib").util.find_spec("piexif"), "piexif not installed")
class ExifTest(unittest.TestCase):
    def test_round_trip(self):
        import piexif
        pos = {"lat": -33.5, "lon": 151.25, "alt": 100.0, "hdg": None, "yaw": -90.0}
        exif = mu.build_exif(piexif, pos, 1_700_000_000.5)
        loaded = piexif.load(piexif.dump(exif))
        gps = loaded["GPS"]
        self.assertEqual(gps[piexif.GPSIFD.GPSLatitudeRef], b"S")
        self.assertEqual(gps[piexif.GPSIFD.GPSImgDirection], (27000, 100))
        self.assertEqual(loaded["Exif"][piexif.ExifIFD.SubSecTimeOriginal], b"500")


if __name__ == "__main__":
    unittest.main()
