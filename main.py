#!/usr/bin/env python3
"""
Photo Cleaner Desktop - Native desktop application entry point.
Launches Flask backend in a background thread and opens a native window
via pywebview instead of requiring the user to open a browser.
"""

import os
import sys
import threading
import multiprocessing

def resource_path(relative_path):
    """Get absolute path to resource, works for dev and PyInstaller."""
    try:
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


def start_flask():
    """Start the Flask app in the main thread, or a separate process."""
    # Import here so pywebview window opens faster
    sys.path.insert(0, resource_path("."))
    from app import app
    app.run(host="127.0.0.1", port=5800, debug=False, use_reloader=False)


def main():
    """Launch Flask in background thread, then open native window."""

    # Fix working directory for PyInstaller
    if getattr(sys, 'frozen', False):
        os.chdir(os.path.dirname(sys.executable))

    # Start Flask in a background thread
    flask_thread = threading.Thread(target=start_flask, daemon=True)
    flask_thread.start()

    # Wait for Flask to be ready
    import urllib.request
    import time
    for i in range(30):
        try:
            urllib.request.urlopen("http://127.0.0.1:5800/")
            break
        except Exception:
            time.sleep(0.3)

    # Import pywebview and open native window
    import webview

    # Window settings
    window = webview.create_window(
        title="Photo Cleaner",
        url="http://127.0.0.1:5800",
        width=1280,
        height=800,
        min_size=(900, 600),
        resizable=True,
        fullscreen=False,
        text_select=True,
        confirm_close=True,
    )

    webview.start(
        debug=False,
        http_server=False,  # We use our own Flask server
        private_mode=False,
    )


if __name__ == "__main__":
    # Required for pywebview on macOS
    multiprocessing.freeze_support()
    main()
