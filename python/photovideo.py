#!/usr/bin/env python3
# -*- coding:utf-8 vi:ts=4:noexpandtab

import argparse
import inspect
import time, signal, os, sys, shutil, traceback
import json

def is_jetson():
    """Returns True if running on an NVIDIA Jetson device."""
    return os.path.exists("/etc/nv_tegra_release")

def is_usb_camera(camera_path="/dev/video0"):
    """
    Automated hardware probe. Returns True if the video device 
    is a USB / UVC device, and False if it is a Jetson CSI/MIPI sensor.
    """
    video_device = camera_path
    if not os.path.exists(video_device):
        return False
        
    try:
        # Query sysfs to find the subsystem driver path for this video node
        real_path = os.path.realpath(f"/sys/class/video4linux/{os.path.basename(camera_path)}/device")
        
        # Check if the kernel device path maps back to a USB bus
        if "usb" in real_path.lower():
            return True
            
        # Fallback check: Read V4L2 device information directly
        # Similar to parsing 'v4l2-ctl --list-devices' metadata
        with open(f"/sys/class/video4linux/{os.path.basename(camera_path)}/name", "r") as f:
            device_name = f.read().lower()
            if "usb" in device_name or "uvc" in device_name:
                return True
    except Exception:
        pass
        
    return False

# Get Jetson pipeline string for GStreamer
def get_jetson_pipeline(camera_path="/dev/video0", width=1920, height=1080, framerate=10):
    camera_index = os.path.basename(camera_path)[-1]
    return (
        f"nvarguscamerasrc sensor-id={camera_index} ! "
        f"video/x-raw(memory:NVMM), width={width}, height={height}, framerate={framerate}/1 ! "
        f"nvvidconv ! video/x-raw, format=BGRx ! videoconvert ! "
        f"video/x-raw, format=BGR ! appsink drop=true"
    )

# Parse command line arguments
home_dir = os.path.expanduser("~")

parser = argparse.ArgumentParser(description="Camera control server")
parser.add_argument("-d", "--destination", dest="mediaPath",
                    help="Save captured image to PATH. Default: ~/Rpanion-server/media/",
                    metavar="PATH",
                    default=f"{home_dir}/Rpanion-server/media/"
                    )
parser.add_argument("-m", "--mode", choices=['photo', 'video'],
                    dest="captureMode",
                    help="Capture mode options: photo [default], video", metavar="MODE",
                    default='photo'
                    )
parser.add_argument("--device", dest="captureDeviceId",
                    help="Unique device ID. If it starts with 'CSI', Picamera2 is used. Otherwise, it's treated as a V4L2 device path.",
                    default=None
                    )
parser.add_argument("--width", metavar="W", type=int, dest="vidWidth",
                    help="Image width", default=1920
                    )
parser.add_argument("--height", metavar="H", type=int, dest="vidHeight",
                    help="Image height", default=1080
                    )
parser.add_argument("--fps", metavar="FPS", type=int, dest="vidFps",
                    help="Video framerate", default=30
                    )
parser.add_argument("--format", dest="vidFormat",
                    help="Video format (e.g., YUV420, MJPEG, RGB888). Default: YUV420",
                    default="YUV420"
                    )
parser.add_argument("--rotation", metavar="DEG", type=int, choices=[0, 90, 180, 270],
                    dest="imageRotation", help="Image rotation", default=0)
parser.add_argument("-b", "--bitrate", metavar = "N",
                    type = int, dest="vidBitrate",
                    help="Video bitrate in bits per second. Default: 10000000",
                    default=10000000
                    )
parser.add_argument("-f", "--min-disk-space", metavar = "N",
                type = int, dest="minFreeSpace",
                help="Minimum free disk space (in MB) required to save files. Default: 1000 MB",
                default=1000
                )
parser.add_argument("--exposure-us", metavar="US", type=int, dest="exposureUs",
                    help="Photo mode: fixed exposure time in microseconds (reduces motion blur). 0 = auto exposure [default]",
                    default=0
                    )
parser.add_argument("--raw-mjpeg", choices=['auto', 'off'], dest="rawMjpeg",
                    help="Photo mode, V4L2 cameras: save the camera's MJPEG frames directly instead of "
                         "decoding and re-encoding them (faster, no extra quality loss). Default: auto",
                    default='auto'
                    )
args = parser.parse_args()

mediaPath = args.mediaPath
captureMode = args.captureMode
minFreeSpace = args.minFreeSpace * 10**6

# Try importing piexif for geotagging, if photo mode was requested
if captureMode == "photo":
    try:
        import piexif
        HAS_PIEXIF = True
    except ImportError:
        HAS_PIEXIF = False
        print("Warning: 'piexif' module not found. Geotagging will be disabled. Install with: pip3 install piexif", flush=True)

# Set up signals before importing heavy libraries (cv2, picamera2)
# to prevent the process from being killed if a signal arrives during import.

GOT_SIGUSR1 = False
GOT_SIGTERM = False
VIDEO_ACTIVE = False

def handle_sigusr1(signum, stack):
    global GOT_SIGUSR1
    GOT_SIGUSR1 = True

def handle_sigterm(signum, stack):
    global GOT_SIGTERM
    GOT_SIGTERM = True

# Register the signal handler functions with the actual signals
signal.signal(signal.SIGTERM, handle_sigterm)
signal.signal(signal.SIGUSR1, handle_sigusr1)

# Notify Node.js immediately that we are alive (prevents timeout)
print(f"PID is : {os.getpid()}", flush=True)

# Load the heavy libraries
HAS_PICAMERA = False

# Attempt to load Picamera2
print("Loading camera libraries. This may take a while.", flush=True)
try:
    from picamera2 import Picamera2
    print("Loaded Picamera2", flush=True)
    # Only try importing H264Encoder if video mode was requested
    if captureMode == "video":
        from picamera2.encoders import H264Encoder
        print("Loaded the H264 Encoder", flush=True)
    from libcamera import Transform
    
    HAS_PICAMERA = True

except (ImportError, OSError):
    HAS_PICAMERA = False
    print("Picamera2 not found. Will attempt to fall back to V4L2.", flush=True)

try:
    import cv2
    import numpy
    print("Loaded OpenCV-Python", flush=True)
    print("Finished loading camera libraries.", flush=True)
except ImportError:
    print("Critical Error: OpenCV-Python (cv2) not found. Exiting.", file=sys.stderr)
    sys.exit(1)

import mapping_utils

GPS_FILE = '/tmp/rpanion_gps.json'
PHOTO_SEQ = 0

# --- Geotagging ---
def read_trigger_position():
    """Read the position that Rpanion-server wrote when the capture was triggered."""
    if not os.path.exists(GPS_FILE):
        return None
    try:
        with open(GPS_FILE, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"Could not read {GPS_FILE}: {e}", file=sys.stderr)
        return None

def geotag_image(filepath, capture_time):
    """Add EXIF time/GPS tags and append to geo.txt/captures.csv. Returns the position used."""
    pos, reason = mapping_utils.position_at_capture(read_trigger_position(), capture_time)
    if pos:
        print(f"Geotag: {reason}", flush=True)
    else:
        print(f"Warning: not geotagging - {reason}. Check that \"Enable datastream requests\" "
              "is enabled on the Flight Controller page.", flush=True)

    if HAS_PIEXIF:
        try:
            try:
                existing = piexif.load(filepath)
            except Exception:
                existing = None
            exif_dict = mapping_utils.build_exif(piexif, pos, capture_time, existing)
            piexif.insert(piexif.dump(exif_dict), filepath)
            if pos:
                print(f"Geotagged image with Lat: {pos['lat']:.7f}, Lon: {pos['lon']:.7f}, Alt: {pos['alt']:.1f}", flush=True)
        except Exception as e:
            print(f"Error Geotagging: {e}", file=sys.stderr)
    else:
        print("EXIF library not available. Skipping EXIF geotag.", flush=True)

    try:
        mapping_utils.append_capture_logs(os.path.dirname(filepath), os.path.basename(filepath), pos, capture_time)
    except Exception as e:
        print(f"Error writing geo.txt/captures.csv: {e}", file=sys.stderr)
    return pos

def next_photo_path(capture_time, ext="jpg"):
    """Millisecond + sequence file name, so rapid triggers never overwrite each other."""
    global PHOTO_SEQ
    PHOTO_SEQ += 1
    return mapping_utils.unique_path(mediaPath, mapping_utils.media_filename(capture_time, PHOTO_SEQ, ext))

# Main script setup
picam2_still = None
picam2_vid = None
v4l2_cam = None
v4l2_writer = None
encoder = None
use_picamera = False
RAW_MJPEG = False   # V4L2 photo mode: frames are the camera's own JPEG bytes
JPEG_DHT = b""      # standard Huffman tables, for MJPEG frames that omit them
V4L2_FLUSH_FRAMES = 5  # stale frames to drop before a photo (1 if the driver honours BUFFERSIZE=1)

# Create media directory if it doesn't exist
try:
    os.makedirs(mediaPath, exist_ok=True)
    print(f"Media storage directory '{mediaPath}' is ready.")
except Exception as e:
    sys.exit(f"An error occurred creating directory: {e}")

# Determine which backend to use
# If the device path starts with '/dev/video', it is a USB/V4L2 camera.
# Everything else (None, 'CSI', '/base/soc/...') is treated as a Libcamera/Picamera2 device.

is_explicit_v4l2 = args.captureDeviceId is not None and args.captureDeviceId.startswith('/dev/video')

if not is_explicit_v4l2 and HAS_PICAMERA:
    print(f"Device {args.captureDeviceId} specified (or None). Defaulting to Picamera2 backend.", flush=True)
    use_picamera = True
    try:
        if not Picamera2.global_camera_info():
             sys.exit("Picamera2 backend selected, but no libcamera cameras found.")
    except Exception as e:
        sys.exit(f"Could not query for Picamera2 cameras: {e}")
else:
    print(f"Device {args.captureDeviceId} specified. Using V4L2/OpenCV backend.", flush=True)
    use_picamera = False

    # If no device ID was provided, default to /dev/video0
    if args.captureDeviceId is None:
        args.captureDevicePath = "/dev/video0"
        print("No device ID provided. Defaulting to /dev/video0")
    else:
        args.captureDevicePath = args.captureDeviceId

if captureMode == "photo":
    if use_picamera:
        picam2_still = Picamera2()
        config = picam2_still.create_still_configuration({"size":(args.vidWidth, args.vidHeight)}, transform=Transform(rotation=args.imageRotation))
        picam2_still.configure(config)
        picam2_still.start()
        if args.exposureUs > 0:
            picam2_still.set_controls({"AeEnable": False, "ExposureTime": args.exposureUs})
            print(f"Manual exposure: {args.exposureUs} us", flush=True)
        time.sleep(2)
        print(f"Camera is ready in Picamera2 photo mode. Capturing {args.vidWidth}x{args.vidHeight}, {args.imageRotation}° rotation")
    elif is_jetson() and not is_usb_camera(args.captureDevicePath):
        v4l2_cam = cv2.VideoCapture(get_jetson_pipeline(args.captureDevicePath, args.vidWidth, args.vidHeight), cv2.CAP_GSTREAMER)
        if not v4l2_cam.isOpened():
            sys.exit(f"V4L2 camera at {args.captureDevicePath} failed to open.")
        time.sleep(2)
        print(f"Camera is ready in Jetson V4L2 Argus photo mode. Capturing {args.vidWidth}x{args.vidHeight}")
    else:
        v4l2_cam = cv2.VideoCapture(args.captureDevicePath, cv2.CAP_V4L2)
        if not v4l2_cam.isOpened():
            sys.exit(f"V4L2 camera at {args.captureDevicePath} failed to open.")
        mjpg = cv2.VideoWriter_fourcc(*'MJPG')
        if args.rawMjpeg == 'auto':
            # Most UVC cameras only reach full resolution in MJPEG mode
            v4l2_cam.set(cv2.CAP_PROP_FOURCC, mjpg)
        v4l2_cam.set(cv2.CAP_PROP_FRAME_WIDTH, args.vidWidth)
        v4l2_cam.set(cv2.CAP_PROP_FRAME_HEIGHT, args.vidHeight)
        # Fewer queued frames = fresher frame when the trigger arrives
        if v4l2_cam.set(cv2.CAP_PROP_BUFFERSIZE, 1) and int(v4l2_cam.get(cv2.CAP_PROP_BUFFERSIZE)) == 1:
            V4L2_FLUSH_FRAMES = 1
        else:
            V4L2_FLUSH_FRAMES = 5
        if args.rawMjpeg == 'auto' and int(v4l2_cam.get(cv2.CAP_PROP_FOURCC)) == mjpg:
            v4l2_cam.set(cv2.CAP_PROP_CONVERT_RGB, 0)
            RAW_MJPEG = True
            # Reference Huffman tables (OpenCV/libjpeg writes the standard ones),
            # added to camera frames that omit them
            _, ref = cv2.imencode('.jpg', numpy.random.randint(0, 255, (16, 16, 3), dtype=numpy.uint8))
            JPEG_DHT = mapping_utils.extract_dht(ref.tobytes())
            print("Saving the camera's MJPEG frames directly (no re-encoding)", flush=True)
        if args.exposureUs > 0:
            v4l2_cam.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1)  # 1 = V4L2_EXPOSURE_MANUAL
            # V4L2 exposure_time_absolute is in units of 100 us
            v4l2_cam.set(cv2.CAP_PROP_EXPOSURE, max(1, round(args.exposureUs / 100)))
            print(f"Manual exposure: {args.exposureUs} us (camera reports {v4l2_cam.get(cv2.CAP_PROP_EXPOSURE)})", flush=True)
        time.sleep(2)
        print(f"Camera is ready in V4L2 photo mode. Capturing "
              f"{int(v4l2_cam.get(cv2.CAP_PROP_FRAME_WIDTH))}x{int(v4l2_cam.get(cv2.CAP_PROP_FRAME_HEIGHT))}")

elif captureMode == "video":
    if use_picamera:

        # Sanitize the format codes: Convert GStreamer MIME types (from frontend) to Picamera2 pixel formats
        # Picamera2 generally needs 'YUV420' as the main stream format to feed the H264 encoder.
        if args.vidFormat and (args.vidFormat.startswith("video/") or "h264" in args.vidFormat):
            print(f"Mapping format '{args.vidFormat}' to 'YUV420' for Picamera2", flush=True)
            args.vidFormat = "YUV420"
            
        # FrameRate alone only requests a nominal rate - auto-exposure can still
        # stretch each frame's actual duration in low light, so the real
        # capture rate can drift below what was requested. Pinning
        # FrameDurationLimits to a fixed value prevents that drift.
        video_controls = {"FrameRate": args.vidFps}
        if args.vidFps > 0:
            frame_duration_us = int(1_000_000 / args.vidFps)
            video_controls["FrameDurationLimits"] = (frame_duration_us, frame_duration_us)

        picam2_vid = Picamera2()
        video_config = picam2_vid.create_video_configuration(
            main={
                "size": (args.vidWidth, args.vidHeight),
                "format": args.vidFormat},
                controls=video_controls,
                transform=Transform(rotation=args.imageRotation)
            )
        picam2_vid.configure(video_config)
        # The recording is a raw elementary .h264 stream (no container/
        # timestamps), so a player has no way to know the real playback rate
        # unless it's embedded in-band. enable_sps_framerate writes the actual
        # (now-fixed) framerate into the stream's SPS/VUI metadata.
        #
        # H264Encoder() picks its actual implementation based on hardware:
        # boards with a hardware H264 block (e.g. Pi4/CM4) get the hardware
        # encoder, but boards without one (e.g. Pi5/CM5) transparently get
        # LibavH264Encoder instead - a different class that doesn't accept
        # enable_sps_framerate. Only pass it if this build's encoder actually
        # supports it, rather than assuming a specific backend.
        encoder_kwargs = {"bitrate": args.vidBitrate, "framerate": args.vidFps}
        if "enable_sps_framerate" in inspect.signature(H264Encoder.__init__).parameters:
            encoder_kwargs["enable_sps_framerate"] = True
        else:
            print("H264 encoder backend doesn't support enable_sps_framerate - recorded video's "
                  "framerate won't be embedded in-band (playback speed may still be affected on "
                  "players that can't infer it from FrameDurationLimits alone).", flush=True)
        encoder = H264Encoder(**encoder_kwargs)
        picam2_vid.start()
        print(f"Camera is ready in Picamera2 video mode. Capturing {args.vidWidth}x{args.vidHeight} {args.vidFormat} @ {args.vidFps} fps, {args.imageRotation}° rotation")
    elif is_jetson() and not is_usb_camera(args.captureDevicePath):
        v4l2_cam = cv2.VideoCapture(get_jetson_pipeline(args.captureDevicePath, args.vidWidth, args.vidHeight), cv2.CAP_GSTREAMER)
        if not v4l2_cam.isOpened():
            sys.exit(f"V4L2 camera at {args.captureDevicePath} failed to open.")
        time.sleep(2)
        print(f"Camera is ready in Jetson V4L2 Argus video mode. Capturing {args.vidWidth}x{args.vidHeight}")
    else:
        v4l2_cam = cv2.VideoCapture(args.captureDevicePath, cv2.CAP_V4L2)
        if not v4l2_cam.isOpened():
            sys.exit(f"V4L2 camera at {args.captureDevicePath} failed to open.")

        v4l2_cam.set(cv2.CAP_PROP_FRAME_WIDTH, args.vidWidth)
        v4l2_cam.set(cv2.CAP_PROP_FRAME_HEIGHT, args.vidHeight)
        v4l2_cam.set(cv2.CAP_PROP_FPS, args.vidFps)

        # Check if the actual width, height, and FPS were written correctly
        actual_v4l2_width = int(v4l2_cam.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_v4l2_height = int(v4l2_cam.get(cv2.CAP_PROP_FRAME_HEIGHT))
        actual_v4l2_fps = int(v4l2_cam.get(cv2.CAP_PROP_FPS))

        print(f"Camera is ready in V4L2 video mode. Capturing {actual_v4l2_width}x{actual_v4l2_height} {args.vidFormat} @ {actual_v4l2_fps} fps")

def graceful_exit():
    global VIDEO_ACTIVE
    print("Gracefully exiting...")
    if VIDEO_ACTIVE:
        startstop_video()
        time.sleep(0.2)
    if use_picamera:
        if picam2_still: picam2_still.stop()
        if picam2_vid: picam2_vid.stop()
    elif v4l2_cam:
        v4l2_cam.release()
    sys.exit(0)

def startstop_video():
    global VIDEO_ACTIVE, v4l2_writer
    if use_picamera:
        if VIDEO_ACTIVE:
            picam2_vid.stop_recording()
            VIDEO_ACTIVE = False
            print("Picamera2 recording stopped.")
        else:
            filepath = os.path.join(mediaPath, time.strftime("RPN%Y%m%d_%H%M%S.h264"))
            picam2_vid.start_recording(encoder, filepath)
            VIDEO_ACTIVE = True
            print(f"Picamera2 recording started to {filepath}")
    else: # V4L2
        if VIDEO_ACTIVE:
            VIDEO_ACTIVE = False
            if v4l2_writer:
                v4l2_writer.release()
                v4l2_writer = None
                print("V4L2 recording stopped.")
        else:
            for _ in range(5): v4l2_cam.read() # Flush buffer
            filepath = os.path.join(mediaPath, time.strftime("RPN%Y%m%d_%H%M%S.avi"))
            fourcc = cv2.VideoWriter_fourcc(*'MJPG')
            v4l2_writer = cv2.VideoWriter(filepath, fourcc, actual_v4l2_fps, (actual_v4l2_width, actual_v4l2_height))
            if v4l2_writer.isOpened():
                VIDEO_ACTIVE = True
                print(f"V4L2 recording started to {filepath}")
            else:
                print(f"Error: Could not open VideoWriter for {filepath}", file=sys.stderr)

def save_v4l2_frame(filepath, frame):
    """Save a V4L2 frame. Returns False if it could not be saved."""
    global RAW_MJPEG
    if RAW_MJPEG:
        data = frame.tobytes() if frame is not None else b""
        if data[:2] == b"\xff\xd8":
            data = mapping_utils.ensure_dht(mapping_utils.trim_after_eoi(data), JPEG_DHT)
            with open(filepath, 'wb') as f:
                f.write(data)
            return True
        # Not a JPEG (camera changed format?) - fall back to decoded frames
        print("Frame is not MJPEG, switching to decoded capture.", file=sys.stderr, flush=True)
        RAW_MJPEG = False
        v4l2_cam.set(cv2.CAP_PROP_CONVERT_RGB, 1)
        return False
    return cv2.imwrite(filepath, frame, [cv2.IMWRITE_JPEG_QUALITY, 95])

def capture_photo():
    if use_picamera:
        # still configuration is already running, so the next frame is used
        capture_time = time.time()
        filepath = next_photo_path(capture_time)
        print(f"Capturing photo to {filepath}")
        picam2_still.capture_file(filepath)
    else:
        # Drop queued (stale) frames, then take a fresh one. grab() doesn't decode.
        for _ in range(V4L2_FLUSH_FRAMES):
            v4l2_cam.grab()
        ret, frame = v4l2_cam.read()
        capture_time = time.time()
        if not ret:
            print("Failed to capture frame from V4L2 camera.", file=sys.stderr)
            return
        filepath = next_photo_path(capture_time)
        print(f"Capturing photo to {filepath}")
        if not save_v4l2_frame(filepath, frame):
            # save_v4l2_frame may have switched from raw MJPEG to decoded frames: retry once
            ret, frame = v4l2_cam.read()
            capture_time = time.time()
            if not (ret and save_v4l2_frame(filepath, frame)):
                print("Failed to save frame from V4L2 camera.", file=sys.stderr)
                return
    print("Photo captured.")
    geotag_image(filepath, capture_time)

if __name__ == '__main__':
    try:
        while True:
            if GOT_SIGTERM:
                GOT_SIGTERM = False
                graceful_exit()

            if GOT_SIGUSR1:
                GOT_SIGUSR1 = False

                freeDiskSpace = shutil.disk_usage(mediaPath).free
                if freeDiskSpace < minFreeSpace and (captureMode == 'photo' or (captureMode == 'video' and not VIDEO_ACTIVE)):
                    print(f"Free disk space is {(int)(freeDiskSpace / 10**6)} MB, which is less than the minimum of {(int)(minFreeSpace / 10**6)} MB. Action aborted.", flush=True)
                else:
                    if captureMode == 'photo':
                        capture_photo()
                    elif captureMode == 'video':
                        print("Toggling video recording.")
                        startstop_video()

            # Main loop with two states: paused and waiting for a signal, or actively recording V4L2 video
            if use_picamera or not VIDEO_ACTIVE:
                #print("... Paused, waiting for signal ...", file=sys.stderr)
                time.sleep(0.1)
            else:
                if v4l2_cam and v4l2_writer:
                    ret, frame = v4l2_cam.read()
                    if ret:
                        v4l2_writer.write(frame)
                    else:
                        print("V4L2 frame read failed during recording. Stopping.", file=sys.stderr)
                        startstop_video()
                time.sleep(0.1)

    except KeyboardInterrupt:
        graceful_exit()
    except Exception as e:
        print(f"!!! PYTHON ERROR: Unhandled exception: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        graceful_exit()
