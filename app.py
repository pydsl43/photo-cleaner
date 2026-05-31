#!/usr/bin/env python3
"""Photo Cleaner - Smart similar photo detection and cleanup tool."""

import os
import sys
import json
import base64
import io
import threading
import webbrowser
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from flask import Flask, render_template, request, jsonify, send_file
from PIL import Image, ImageOps
from PIL.ExifTags import TAGS, GPSTAGS
import imagehash

# ─── PyInstaller resource path fix ────────────────────────────────────
def resource_path(relative_path):
    """Get absolute path to resource, works for dev and PyInstaller."""
    try:
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)

# Point Flask to the correct templates folder
app = Flask(__name__,
    template_folder=resource_path("templates"),
    static_folder=None)

# ─── Configuration ─────────────────────────────────────────────────────────────

SCAN_DIR = os.path.expanduser("~")
THRESHOLD = 0.85  # similarity threshold (0-1)
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif', '.webp', '.heic', '.heif',
                     '.arw', '.srf', '.sr2', '.srw',
                     '.crw', '.cr2', '.cr3',
                     '.nef', '.nrw',
                     '.raf',
                     '.orf',
                     '.rw2', '.raw', '.rwl',
                     '.pef', '.ptx',
                     '.smp',
                     '.kdc', '.dcr', '.k25',
                     '.mrw', '.mdc',
                     '.dng',
                     '.iiq', '.mos', '.mef',
                     '.3fr', '.fff',
                     '.x3f',
                     '.gpr',
                     '.cdng', '.mlv'}
HASH_SIZE = 16  # perceptual hash size (larger = more detail)

# ─── State ─────────────────────────────────────────────────────────────────────

scan_state = {
    "running": False,
    "progress": {"current": 0, "total": 0},
    "results": None,
    "error": None,
}

# ─── RAW image support ───────────────────────────────────────────────────────

RAW_EXTENSIONS = {'.arw', '.srf', '.sr2', '.srw',
                   '.crw', '.cr2', '.cr3',
                   '.nef', '.nrw',
                   '.raf',
                   '.orf',
                   '.rw2', '.raw', '.rwl',
                   '.pef', '.ptx',
                   '.smp',
                   '.kdc', '.dcr', '.k25',
                   '.mrw', '.mdc',
                   '.dng',
                   '.iiq', '.mos', '.mef',
                   '.3fr', '.fff',
                   '.x3f',
                   '.gpr',
                   '.cdng', '.mlv'}

def open_image_safe(image_path):
    """Open an image file, supporting both regular images and RAW formats."""
    ext = Path(image_path).suffix.lower()
    if ext in RAW_EXTENSIONS:
        return open_raw_image(image_path)
    return Image.open(image_path)


def open_raw_image(image_path):
    """Open a RAW photo using rawpy, fall back to Pillow if unavailable."""
    try:
        import rawpy
        with rawpy.imread(image_path) as raw:
            rgb = raw.postprocess(
                use_camera_wb=True,
                half_size=True,
                no_auto_bright=False,
                output_bps=8,
            )
            return Image.fromarray(rgb)
    except ImportError:
        # rawpy not installed, try Pillow as fallback
        return Image.open(image_path)
    except Exception:
        # RAW decode failed, try Pillow as last resort
        try:
            return Image.open(image_path)
        except Exception:
            raise


# ─── Image Hashing ─────────────────────────────────────────────────────────────

def compute_phash(image_path):
    """Compute perceptual hash for an image file (supports RAW formats)."""
    try:
        img = open_image_safe(image_path)
        img = ImageOps.exif_transpose(img)
        if img is None:
            img = open_image_safe(image_path)
        img = img.convert("RGB")
        return imagehash.phash(img, hash_size=HASH_SIZE)
    except Exception as e:
        print(f"Warning: Could not process {image_path}: {e}", file=sys.stderr)
        return None


def hamming_similarity(hash1, hash2, hash_size=HASH_SIZE):
    """Convert Hamming distance to a similarity score (0-1)."""
    max_dist = hash_size * hash_size
    dist = hash1 - hash2
    return 1.0 - (dist / max_dist)


# ─── Scanning Logic ────────────────────────────────────────────────────────────

def find_images(root_dir):
    """Recursively find all image files in a directory."""
    images = []
    root = Path(root_dir).expanduser().resolve()
    if not root.exists():
        return images

    for entry in root.rglob("*"):
        if entry.suffix.lower() in IMAGE_EXTENSIONS:
            images.append(str(entry))
    return images


# ─── Keep Strategy ────────────────────────────────────────────────────────────

def score_sharpness(image_path):
    """Estimate image sharpness using Laplacian variance. Higher = sharper.
       Supports RAW formats."""
    try:
        img = open_image_safe(image_path)
        img = img.convert("L")  # Grayscale
        import numpy as np
        from scipy import ndimage
        laplacian = ndimage.laplace(np.array(img, dtype=float))
        return float(np.var(laplacian))
    except Exception:
        return 0.0


def score_exif_richness(image_path):
    """Count EXIF fields as a proxy for information richness. Supports RAW."""
    try:
        img = open_image_safe(image_path)
        exif = img.getexif()
        count = len([k for k in exif.keys() if k != 0x927C and k != 0x9286])
        # Also count GPS sub-IFD fields
        try:
            gps = exif.get_ifd(0x8825)
            if gps:
                count += len(gps)
        except Exception:
            pass
        return count
    except Exception:
        return 0


def pick_keep_index(group_paths, strategy, hashes):
    """Given a group of similar photos, pick the index to keep.

    Strategies:
      - "largest":    keep the largest file
      - "sharpest":   keep the sharpest (highest Laplacian variance)
      - "richest":    keep the one with most EXIF fields
    """
    if strategy == "sharpest":
        scores = [score_sharpness(p) for p in group_paths]
        return scores.index(max(scores))

    if strategy == "richest":
        scores = [score_exif_richness(p) for p in group_paths]
        return scores.index(max(scores))

    # Default: "largest" - keep the largest file
    sizes = [os.path.getsize(p) if os.path.exists(p) else 0 for p in group_paths]
    return sizes.index(max(sizes))


def is_raw_jpg_pair(path_a, path_b):
    """Check if two paths are RAW+JPG pair of the same photo (same stem, different ext)."""
    p1 = Path(path_a)
    p2 = Path(path_b)
    return (p1.stem == p2.stem and
            p1.suffix.lower() in RAW_EXTENSIONS and
            p2.suffix.lower() not in RAW_EXTENSIONS,
            p1.stem == p2.stem and
            p2.suffix.lower() in RAW_EXTENSIONS and
            p1.suffix.lower() not in RAW_EXTENSIONS)


def cluster_similar(hashes, threshold, strategy="largest"):
    """Group similar images based on hash similarity.

    Automatically excludes RAW+JPG pairs of the same filename stem.
    Returns list of groups, where each group is a dict:
      {"files": [path, ...], "keep_index": int}
    """
    used = set()
    groups = []
    raw_jpg_pairs = []

    all_paths = list(hashes.keys())

    # First pass: identify RAW+JPG pairs
    for i, path_a in enumerate(all_paths):
        if i in used:
            continue
        for j in range(i + 1, len(all_paths)):
            if j in used:
                continue
            path_b = all_paths[j]
            ab_is_pair, ba_is_pair = is_raw_jpg_pair(path_a, path_b)
            if ab_is_pair:
                raw_jpg_pairs.append((path_a, path_b))
                used.add(i)
                used.add(j)
                break
            elif ba_is_pair:
                raw_jpg_pairs.append((path_b, path_a))
                used.add(i)
                used.add(j)
                break

    # Second pass: cluster remaining (non-paired) images
    for i, path_a in enumerate(all_paths):
        if i in used:
            continue
        group = [path_a]
        used.add(i)

        for j in range(i + 1, len(all_paths)):
            if j in used:
                continue
            path_b = all_paths[j]
            if hashes[path_a] is not None and hashes[path_b] is not None:
                sim = hamming_similarity(hashes[path_a], hashes[path_b])
                if sim >= threshold:
                    group.append(path_b)
                    used.add(j)

        if len(group) > 1:
            keep_idx = pick_keep_index(group, strategy, hashes)
            groups.append({"files": group, "keep_index": keep_idx})

    return groups, raw_jpg_pairs


def scan_directory(root_dir, threshold, strategy="largest", progress_callback=None):
    """Full scan pipeline: find images → hash → cluster."""
    all_images = find_images(root_dir)

    if not all_images:
        return {"groups": [], "total": 0, "similar_groups": 0, "similar_count": 0}

    total = len(all_images)
    if progress_callback:
        progress_callback(0, total, "hashing")

    hashes = {}
    done = 0
    with ThreadPoolExecutor(max_workers=8) as executor:
        future_map = {executor.submit(compute_phash, p): p for p in all_images}
        for future in as_completed(future_map):
            path = future_map[future]
            try:
                h = future.result()
                if h is not None:
                    hashes[path] = h
            except Exception:
                pass
            done += 1
            if progress_callback and done % 20 == 0:
                progress_callback(done, total, "hashing")

    if progress_callback:
        progress_callback(total, total, "clustering")

    groups, raw_jpg_pairs = cluster_similar(hashes, threshold, strategy)

    similar_count = sum(len(g["files"]) for g in groups)
    return {
        "groups": groups,
        "raw_jpg_pairs": [{"raw": r, "jpg": j} for r, j in raw_jpg_pairs],
        "total": total,
        "hashed": len(hashes),
        "similar_groups": len(groups),
        "similar_count": similar_count,
        "strategy": strategy,
    }


# ─── EXIF & Organize ──────────────────────────────────────────────────────────

def read_exif_date(image_path):
    """Extract DateTimeOriginal from EXIF, fall back to file modification time.
       Supports RAW formats through Pillow's EXIF parser (works for most RAW files)."""
    try:
        img = open_image_safe(image_path)
        exif = img.getexif()
        if exif:
            dt_str = exif.get(0x9003)  # DateTimeOriginal
            if dt_str:
                return dt_str.strip()
            dt_str = exif.get(0x9004)  # DateTimeDigitized
            if dt_str:
                return dt_str.strip()
            dt_str = exif.get(0x0132)  # DateTime
            if dt_str:
                return dt_str.strip()
    except Exception:
        pass
    # Fallback to file mtime
    try:
        mtime = os.path.getmtime(image_path)
        import datetime
        return datetime.datetime.fromtimestamp(mtime).strftime("%Y:%m:%d %H:%M:%S")
    except Exception:
        return None


def dms_to_decimal(dms, ref):
    """Convert EXIF GPS DMS to decimal degrees."""
    if not dms:
        return None
    try:
        degrees = float(dms[0])
        minutes = float(dms[1])
        seconds = float(dms[2])
        decimal = degrees + minutes / 60.0 + seconds / 3600.0
        if ref in ('S', 'W'):
            decimal = -decimal
        return round(decimal, 6)
    except (TypeError, ValueError, IndexError):
        return None


def read_exif_gps(image_path):
    """Extract GPS coordinates from EXIF (supports RAW formats)."""
    try:
        img = open_image_safe(image_path)
        exif = img.getexif()
        if not exif:
            return None

        gps_info = exif.get_ifd(0x8825)
        if not gps_info:
            return None

        lat = dms_to_decimal(gps_info.get(2), gps_info.get(1))
        lon = dms_to_decimal(gps_info.get(4), gps_info.get(3))
        if lat is not None and lon is not None:
            return {"lat": lat, "lon": lon}
    except Exception:
        pass
    return None


def parse_exif_date(dt_str):
    """Parse EXIF date string to (year, month, day) tuple."""
    import datetime
    if not dt_str:
        return None
    # Common formats: "2024:08:15 14:30:00" or "2024-08-15T14:30:00"
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d",
                "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.datetime.strptime(dt_str.strip(), fmt)
            return (dt.year, dt.month, dt.day)
        except ValueError:
            continue
    return None


def build_date_bucket(date_tuple):
    """Build a hierarchical folder name from date tuple."""
    if not date_tuple:
        return "未知日期"
    y, m, d = date_tuple
    return f"{y:04d}年{m:02d}月{d:02d}日"


def build_date_bucket_group(date_tuple):
    """Build a group name for same-day grouping."""
    return build_date_bucket(date_tuple)


class OrganizePlanner:
    """Scans images and plans an organize structure without executing moves."""

    def __init__(self, root_dir):
        self.root_dir = root_dir
        self.images = find_images(root_dir)

    def plan_by_date(self):
        """Group images by shooting date."""
        groups = {}
        errors = []

        for idx, path in enumerate(self.images):
            try:
                dt_str = read_exif_date(path)
                date_tuple = parse_exif_date(dt_str)
                bucket = build_date_bucket_group(date_tuple)
                if bucket not in groups:
                    groups[bucket] = []
                groups[bucket].append(path)
            except Exception as e:
                errors.append({"path": path, "error": str(e)})

        # Sort groups by date descending
        sorted_groups = sorted(groups.items(), key=lambda x: x[0], reverse=True)
        return {
            "groups": [{"name": name, "files": files} for name, files in sorted_groups],
            "total": len(self.images),
            "errors": errors,
        }

    def plan_by_location(self):
        """Group images by GPS location (rounded to ~1km precision)."""
        groups = {}  # (lat_rounded, lon_rounded) -> {name, files}
        errors = []

        for idx, path in enumerate(self.images):
            try:
                gps = read_exif_gps(path)
                if gps is None:
                    key = "__no_gps__"
                    if key not in groups:
                        groups[key] = {"name": "无定位信息", "files": []}
                    groups[key]["files"].append(path)
                    continue

                # Round to 3 decimal places (~111m precision) for clustering
                lat_r = round(gps["lat"], 3)
                lon_r = round(gps["lon"], 3)
                key = (lat_r, lon_r)

                if key not in groups:
                    direction_lat = "N" if lat_r >= 0 else "S"
                    direction_lon = "E" if lon_r >= 0 else "W"
                    groups[key] = {
                        "name": f"{abs(lat_r):.3f}°{direction_lat}, {abs(lon_r):.3f}°{direction_lon}",
                        "lat": lat_r,
                        "lon": lon_r,
                        "files": [],
                    }
                groups[key]["files"].append(path)
            except Exception as e:
                errors.append({"path": path, "error": str(e)})

        sorted_groups = sorted(
            [g for k, g in groups.items() if k != "__no_gps__" and len(g["files"]) > 0],
            key=lambda g: g.get("lat", 0),
            reverse=True,
        )
        no_gps = groups.get("__no_gps__")
        if no_gps:
            sorted_groups.append(no_gps)

        return {
            "groups": sorted_groups,
            "total": len(self.images),
            "errors": errors,
        }


@app.route("/api/organize/preview", methods=["POST"])
def organize_preview():
    """Preview organize structure without moving files."""
    data = request.get_json(silent=True) or {}
    scan_dir = data.get("dir", SCAN_DIR)
    mode = data.get("mode", "date")  # "date" or "location"

    planner = OrganizePlanner(scan_dir)

    if mode == "location":
        result = planner.plan_by_location()
    else:
        result = planner.plan_by_date()

    return jsonify(result)


@app.route("/api/organize/execute", methods=["POST"])
def organize_execute():
    """Execute organizing: copy/move files into categorized folders."""
    data = request.get_json(silent=True) or {}
    scan_dir = data.get("dir", SCAN_DIR)
    mode = data.get("mode", "date")
    action = data.get("action", "copy")  # "copy" or "move"

    output_dir = data.get("output_dir", "")
    if not output_dir:
        output_dir = os.path.join(scan_dir, "_归类整理")

    planner = OrganizePlanner(scan_dir)

    if mode == "location":
        plan = planner.plan_by_location()
    else:
        plan = planner.plan_by_date()

    import shutil

    organized = []
    skipped = []
    errors = []

    for group in plan["groups"]:
        group_name = group["name"]
        # Sanitize folder name
        safe_name = "".join(c if c.isalnum() or c in " -_.,°NSEW()" else "_" for c in group_name)
        target_dir = os.path.join(output_dir, safe_name)
        os.makedirs(target_dir, exist_ok=True)

        for file_path in group["files"]:
            try:
                filename = os.path.basename(file_path)
                dest = os.path.join(target_dir, filename)

                # Handle filename conflicts
                base, ext = os.path.splitext(filename)
                counter = 1
                while os.path.exists(dest):
                    dest = os.path.join(target_dir, f"{base}_{counter}{ext}")
                    counter += 1

                if action == "move":
                    shutil.move(file_path, dest)
                else:
                    shutil.copy2(file_path, dest)

                organized.append({"source": file_path, "dest": dest, "group": group_name})
            except Exception as e:
                errors.append({"path": file_path, "error": str(e)})

    return jsonify({
        "organized": organized,
        "skipped": skipped,
        "errors": errors,
        "output_dir": output_dir,
        "total_organized": len(organized),
    })


# ─── API Routes ────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/scan", methods=["POST"])
def start_scan():
    global scan_state

    if scan_state["running"]:
        return jsonify({"error": "A scan is already in progress"}), 409

    data = request.get_json(silent=True) or {}
    scan_dir = data.get("dir", SCAN_DIR)
    threshold = data.get("threshold", THRESHOLD)
    strategy = data.get("strategy", "largest")

    scan_state["running"] = True
    scan_state["progress"] = {"current": 0, "total": 0}
    scan_state["results"] = None
    scan_state["error"] = None

    def progress_callback(current, total, phase):
        scan_state["progress"] = {"current": current, "total": total, "phase": phase}

    def run_scan():
        try:
            result = scan_directory(scan_dir, threshold, strategy, progress_callback)
            scan_state["results"] = result
            scan_state["error"] = None
        except Exception as e:
            scan_state["error"] = str(e)
            scan_state["results"] = None
        finally:
            scan_state["running"] = False
            scan_state["progress"] = {"current": 0, "total": 0}

    thread = threading.Thread(target=run_scan, daemon=True)
    thread.start()

    return jsonify({"status": "started"})


@app.route("/api/scan/status")
def scan_status():
    global scan_state
    return jsonify({
        "running": scan_state["running"],
        "progress": scan_state["progress"],
        "results": scan_state["results"],
        "error": scan_state["error"],
    })


@app.route("/api/thumbnail")
def thumbnail():
    """Serve a resized thumbnail of an image for the web UI (supports RAW)."""
    path = request.args.get("path", "")
    size = request.args.get("size", 300, type=int)

    if not path or not os.path.isfile(path):
        return "", 404

    try:
        img = open_image_safe(path)
        img = ImageOps.exif_transpose(img)
        if img is None:
            img = open_image_safe(path)
        img.thumbnail((size, size), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        buf.seek(0)
        return send_file(buf, mimetype="image/jpeg")
    except Exception:
        return "", 500


@app.route("/api/delete", methods=["POST"])
def delete_images():
    """Delete specified image files."""
    data = request.get_json(silent=True) or {}
    paths = data.get("paths", [])

    deleted = []
    errors = []
    for path in paths:
        try:
            if os.path.isfile(path):
                os.remove(path)
                deleted.append(path)
            else:
                errors.append({"path": path, "error": "File not found"})
        except Exception as e:
            errors.append({"path": path, "error": str(e)})

    return jsonify({"deleted": deleted, "errors": errors})


def _move_to_trash_macos(path):
    """Move file to macOS Trash via AppleScript."""
    import subprocess
    subprocess.run(["osascript", "-e",
        f'tell application "Finder" to delete POSIX file "{path}"'],
        capture_output=True, timeout=10)


def _move_to_trash_windows(path):
    """Move file to Windows Recycle Bin via SendTo / shell32."""
    import subprocess
    subprocess.run(["cmd", "/c", f'start /b "" "{path}" && timeout /t 1 >nul'],
                    capture_output=True, timeout=10)
    # Actually use PowerShell for proper recycle
    subprocess.run(["powershell", "-Command",
        f'Add-Type -AssemblyName Microsoft.VisualBasic; '
        f'[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile("{path}",'
        f'"OnlyErrorDialogs","SendToRecycleBin")'],
        capture_output=True, timeout=10)


def _move_to_trash_linux(path):
    """Move file to Linux Trash ($XDG_DATA_HOME/Trash)."""
    import subprocess
    subprocess.run(["gio", "trash", path], capture_output=True, timeout=10)


@app.route("/api/move_to_trash", methods=["POST"])
def move_to_trash():
    """Move files to OS trash / recycle bin (cross-platform)."""
    data = request.get_json(silent=True) or {}
    paths = data.get("paths", [])

    if sys.platform == "darwin":
        trash_fn = _move_to_trash_macos
    elif sys.platform == "win32":
        trash_fn = _move_to_trash_windows
    else:
        trash_fn = _move_to_trash_linux

    moved = []
    errors = []
    for path in paths:
        try:
            if os.path.isfile(path):
                trash_fn(path)
                moved.append(path)
            else:
                errors.append({"path": path, "error": "File not found"})
        except Exception as e:
            errors.append({"path": path, "error": str(e)})

    return jsonify({"moved_to_trash": moved, "errors": errors})


@app.route("/api/browse", methods=["POST"])
def browse_directory():
    """Open a system directory picker dialog and return the selected path.

    Falls back through: macOS osascript → tkinter (cross-platform) → error.
    """
    import subprocess, tempfile

    if sys.platform == "darwin":
        try:
            script = '''
            tell application "System Events"
                set folderPath to choose folder with prompt "选择照片目录"
                return POSIX path of folderPath
            end tell
            '''
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                path = result.stdout.strip()
                if path:
                    return jsonify({"path": path.rstrip("/")})
        except Exception:
            pass

    # Fallback: tkinter
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        path = filedialog.askdirectory(title="选择照片目录")
        root.destroy()
        if path:
            return jsonify({"path": path})
        return jsonify({"path": "", "cancelled": True})
    except Exception:
        return jsonify({"error": "No directory picker available"}), 501


@app.route("/api/list_dirs", methods=["POST"])
def list_dirs():
    """List subdirectories of a given path for quick navigation."""
    data = request.get_json(silent=True) or {}
    base_path = data.get("path", os.path.expanduser("~"))
    try:
        base = Path(base_path).expanduser().resolve()
        if not base.is_dir():
            return jsonify({"dirs": [], "current": str(base)})

        entries = []
        for entry in sorted(base.iterdir()):
            if entry.is_dir() and not entry.name.startswith('.'):
                entries.append({
                    "name": entry.name,
                    "path": str(entry),
                })
        return jsonify({
            "dirs": entries,
            "current": str(base),
            "parent": str(base.parent) if base.parent != base else None,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = 5800

    # Auto-open browser when running as packaged exe
    is_packaged = getattr(sys, 'frozen', False)
    if is_packaged:
        # Allow overriding port with command-line arg
        if len(sys.argv) > 1:
            try:
                port = int(sys.argv[1])
            except ValueError:
                pass
        # Give the server a moment to start, then open browser
        threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{port}")).start()

    print(f"  Photo Cleaner 启动中...")
    print(f"  打开浏览器访问: http://localhost:{port}")
    print(f"  按 Ctrl+C 停止服务")
    print()
    app.run(host="0.0.0.0", port=port, debug=False)
