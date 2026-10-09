import os
import shutil
import subprocess
import tempfile
import logging
import socket
import struct
import base64
import json
import urllib.request
import threading
import time
import atexit

logger = logging.getLogger(__name__)

# Semaphore to bound concurrent renders and protect RAM
_RENDER_SEMAPHORE = threading.Semaphore(4)
CDP_PORT = int(os.environ.get('CHROME_CDP_PORT', 9222))
CDP_HOST = os.environ.get('CHROME_CDP_HOST', '127.0.0.1')


class ChromiumDaemonManager:
    """
    Manages the lifecycle of a persistent headless Chromium daemon listening on CDP_PORT.
    Ensures sub-second vector PDF generation with zero process boot overhead.
    Auto-spawns on first request and self-heals if process ever terminates.
    """
    _process = None
    _lock = threading.Lock()

    @classmethod
    def is_running(cls, host=CDP_HOST, port=CDP_PORT, timeout=0.3):
        return is_cdp_available(host=host, port=port, timeout=timeout)

    @classmethod
    def ensure_daemon_running(cls, host=CDP_HOST, port=CDP_PORT, max_wait_seconds=2.5):
        if cls.is_running(host=host, port=port, timeout=0.3):
            return True

        with cls._lock:
            # Double-check inside lock
            if cls.is_running(host=host, port=port, timeout=0.2):
                return True

            chrome_path = find_chrome_binary()
            if not chrome_path:
                logger.warning("Chromium executable not located on server. Cannot launch CDP daemon.")
                return False

            try:
                cmd = [
                    chrome_path,
                    '--headless=new',
                    f'--remote-debugging-port={port}',
                    '--disable-gpu',
                    '--no-sandbox',
                    '--disable-dev-shm-usage',
                    '--remote-allow-origins=*',
                    '--hide-scrollbars',
                    '--disable-extensions',
                    '--disable-background-networking',
                    '--disable-default-apps',
                    '--disable-sync',
                    '--mute-audio',
                    '--no-first-run',
                ]

                # Spawn detached background daemon
                creation_flags = 0
                if os.name == 'nt':
                    # Windows: hide console window
                    creation_flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)
                    cls._process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=creation_flags
                    )
                else:
                    # Linux / POSIX: start new session
                    cls._process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True
                    )

                logger.info(f"Spawned warm Chromium daemon (PID {cls._process.pid}) on port {port}")

                # Poll until HTTP /json/version responds
                start = time.time()
                while time.time() - start < max_wait_seconds:
                    if cls.is_running(host=host, port=port, timeout=0.2):
                        logger.info(f"Warm Chromium CDP daemon ready in {round(time.time() - start, 2)}s")
                        return True
                    time.sleep(0.08)

                logger.warning(f"Chromium daemon started (PID {cls._process.pid}) but did not respond on port {port} within {max_wait_seconds}s")
                return cls.is_running(host=host, port=port, timeout=0.5)

            except Exception as e:
                logger.error(f"Failed to auto-spawn Chromium daemon: {e}")
                return False

    @classmethod
    def shutdown(cls):
        with cls._lock:
            if cls._process:
                try:
                    cls._process.terminate()
                    cls._process.wait(timeout=2)
                except Exception:
                    try:
                        cls._process.kill()
                    except Exception:
                        pass
                cls._process = None


# Register cleanup on interpreter exit
atexit.register(ChromiumDaemonManager.shutdown)


def is_cdp_available(host=CDP_HOST, port=CDP_PORT, timeout=0.5):
    """Check if the warm Chromium daemon is alive and responding on CDP port."""
    try:
        url = f"http://{host}:{port}/json/version"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def _send_ws_frame(sock, payload_dict):
    data = json.dumps(payload_dict).encode('utf-8')
    mask = os.urandom(4)
    length = len(data)
    if length <= 125:
        header = bytes([0x81, 0x80 | length])
    elif length <= 65535:
        header = struct.pack('!BBH', 0x81, 0x80 | 126, length)
    else:
        header = struct.pack('!BBQ', 0x81, 0x80 | 127, length)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    sock.sendall(header + mask + masked)


def _recv_ws_msg(sock, target_id=None, timeout=10):
    start = time.time()
    sock.settimeout(timeout)
    while True:
        if time.time() - start > timeout:
            raise TimeoutError(f"Timed out waiting for WebSocket message with id={target_id}")

        header = sock.recv(2)
        if len(header) < 2:
            raise ConnectionResetError("Incomplete WebSocket frame header")
        b1, b2 = struct.unpack('!BB', header)
        length = b2 & 0x7F
        if length == 126:
            ext = sock.recv(2)
            length = struct.unpack('!H', ext)[0]
        elif length == 127:
            ext = sock.recv(8)
            length = struct.unpack('!Q', ext)[0]
        payload = b''
        while len(payload) < length:
            chunk = sock.recv(length - len(payload))
            if not chunk:
                break
            payload += chunk
        msg = json.loads(payload.decode('utf-8', errors='ignore'))
        if target_id is None or msg.get('id') == target_id:
            return msg


def render_via_cdp(html_content, host=CDP_HOST, port=CDP_PORT, timeout=12):
    """
    Render HTML to vector PDF using a persistent warm Chromium daemon over CDP.
    Waits for layout and font settlement, completing in ~200-350ms with zero disk I/O.
    """
    target_id = None
    sock = None
    try:
        # 1. Create a blank page target in the warm browser (~10ms)
        create_url = f"http://{host}:{port}/json/new?about:blank"
        req = urllib.request.Request(create_url, method='PUT')
        with urllib.request.urlopen(req, timeout=2.5) as resp:
            target = json.loads(resp.read().decode('utf-8'))
        target_id = target.get('id')
        ws_url = target.get('webSocketDebuggerUrl')
        if not ws_url:
            raise RuntimeError("Chromium CDP did not return webSocketDebuggerUrl")

        # Extract WS path
        host_port = f"{host}:{port}"
        path = ws_url.split(host_port)[1] if host_port in ws_url else ws_url[ws_url.find('/devtools/page'):]

        # 2. Open TCP socket and complete standard WebSocket RFC 6455 handshake (~5ms)
        sock = socket.create_connection((host, port), timeout=timeout)
        ws_key = base64.b64encode(os.urandom(16)).decode('ascii')
        handshake = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {ws_key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        sock.sendall(handshake.encode('ascii'))
        resp_hdr = sock.recv(2048).decode('ascii', errors='ignore')
        if '101' not in resp_hdr:
            raise ConnectionError(f"WebSocket handshake failed: {resp_hdr}")

        # 3. Enable Page and Runtime domains (~5ms)
        _send_ws_frame(sock, {'id': 1, 'method': 'Page.enable'})
        _recv_ws_msg(sock, target_id=1, timeout=4)

        _send_ws_frame(sock, {'id': 2, 'method': 'Page.getFrameTree'})
        ft = _recv_ws_msg(sock, target_id=2, timeout=4)
        frame_id = ft.get('result', {}).get('frameTree', {}).get('frame', {}).get('id', target_id)

        # 4. Inject document HTML directly over memory (~20ms)
        _send_ws_frame(sock, {
            'id': 3,
            'method': 'Page.setDocumentContent',
            'params': {'frameId': frame_id, 'html': html_content}
        })
        _recv_ws_msg(sock, target_id=3, timeout=4)

        # 5. Fast layout & font settlement promise (~30-50ms)
        _send_ws_frame(sock, {
            'id': 4,
            'method': 'Runtime.evaluate',
            'params': {
                'expression': 'new Promise(resolve => {'
                              '  const settle = () => {'
                              '    const fontsP = (document.fonts && document.fonts.ready) ? document.fonts.ready : Promise.resolve();'
                              '    fontsP.then(() => requestAnimationFrame(() => resolve(true))).catch(() => resolve(true));'
                              '  };'
                              '  if (document.readyState === "complete") {'
                              '    settle();'
                              '  } else {'
                              '    window.addEventListener("load", settle);'
                              '  }'
                              '})',
                'awaitPromise': True,
                'returnByValue': True
            }
        })
        _recv_ws_msg(sock, target_id=4, timeout=4)

        # 6. Execute Print to PDF (~150-250ms)
        _send_ws_frame(sock, {
            'id': 5,
            'method': 'Page.printToPDF',
            'params': {
                'printBackground': True,
                'preferCSSPageSize': True,
                'paperWidth': 8.27,
                'paperHeight': 11.69,
                'marginTop': 0,
                'marginBottom': 0,
                'marginLeft': 0,
                'marginRight': 0,
            }
        })

        res = _recv_ws_msg(sock, target_id=5, timeout=timeout)
        if 'error' in res:
            raise RuntimeError(f"CDP printToPDF error: {res['error']}")

        pdf_base64 = res['result']['data']
        return base64.b64decode(pdf_base64)

    finally:
        if sock:
            try:
                sock.close()
            except Exception:
                pass
        if target_id:
            try:
                # Teardown page target immediately to keep browser RAM lean
                close_url = f"http://{host}:{port}/json/close/{target_id}"
                close_req = urllib.request.Request(close_url)
                urllib.request.urlopen(close_req, timeout=1.5)
            except Exception:
                pass


def find_chrome_binary():
    """Locate Google Chrome, Chromium, or Microsoft Edge executable on any OS."""
    custom_bin = os.environ.get('CHROME_BIN') or os.environ.get('CHROMIUM_PATH') or os.environ.get('GOOGLE_CHROME_BIN')
    if custom_bin and os.path.exists(custom_bin):
        return custom_bin

    candidates = [
        # Windows standard Chrome paths
        r'C:\Program Files\Google\Chrome\Application\chrome.exe',
        r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
        os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe'),
        os.path.expandvars(r'%PROGRAMFILES%\Google\Chrome\Application\chrome.exe'),
        os.path.expandvars(r'%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe'),
        # Windows Microsoft Edge (Chromium based, identical CDP support)
        r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
        r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
        shutil.which('msedge'),
        shutil.which('chrome'),
        # Linux standard paths
        shutil.which('google-chrome'),
        shutil.which('google-chrome-stable'),
        shutil.which('chromium'),
        shutil.which('chromium-browser'),
        '/usr/bin/google-chrome',
        '/usr/bin/google-chrome-stable',
        '/usr/bin/chromium',
        '/usr/bin/chromium-browser',
        '/snap/bin/chromium',
        # macOS standard paths
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
        '/Applications/Chromium.app/Contents/MacOS/Chromium',
    ]

    for path in candidates:
        if path and os.path.exists(path):
            return path
    return None


def render_via_cli_subprocess(html_content, timeout_seconds=15):
    """
    Fallback: Render HTML to vector PDF using CLI subprocess.
    Uses tempfile and compositor draw flags.
    """
    chrome_path = find_chrome_binary()
    if not chrome_path:
        raise RuntimeError("Headless Chromium binary not found on server environment.")

    with tempfile.NamedTemporaryFile(suffix='.html', delete=False, mode='w', encoding='utf-8') as f:
        f.write(html_content)
        html_file = f.name

    pdf_file = html_file.replace('.html', '.pdf')

    try:
        cmd = [
            chrome_path,
            '--headless=new',
            '--disable-gpu',
            '--no-sandbox',
            '--disable-dev-shm-usage',
            '--disable-software-rasterizer',
            '--no-pdf-header-footer',
            '--run-all-compositor-stages-before-draw',
            '--virtual-time-budget=350',
            f'--print-to-pdf={pdf_file}',
            f'file://{html_file}',
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )

        if result.returncode != 0:
            logger.error(f"Chrome headless print error (code {result.returncode}): {result.stderr}")
            raise RuntimeError(f"Headless Chrome failed with return code {result.returncode}: {result.stderr}")

        if not os.path.exists(pdf_file) or os.path.getsize(pdf_file) == 0:
            raise RuntimeError("Headless Chrome did not produce an output PDF file.")

        with open(pdf_file, 'rb') as pf:
            pdf_bytes = pf.read()

        return pdf_bytes

    finally:
        if os.path.exists(html_file):
            try:
                os.unlink(html_file)
            except Exception:
                pass
        if os.path.exists(pdf_file):
            try:
                os.unlink(pdf_file)
            except Exception:
                pass


def render_html_to_vector_pdf(html_content, timeout_seconds=15):
    """
    Render HTML content into an ultra-sharp, 100% pixel-perfect vector PDF.
    - Guarantees warm Chromium CDP daemon is active (~250-350ms render time)
    - Automatically falls back to CLI Chromium process if CDP communication fails
    - Protected by Semaphore(4) against memory starvation under concurrent load
    """
    with _RENDER_SEMAPHORE:
        # Ensure warm daemon is ready
        daemon_ready = is_cdp_available() or ChromiumDaemonManager.ensure_daemon_running()
        if daemon_ready:
            try:
                return render_via_cdp(html_content, timeout=timeout_seconds)
            except Exception as e:
                logger.warning(f"CDP warm render failed ({e}), attempting CLI fallback...")

        return render_via_cli_subprocess(html_content, timeout_seconds=timeout_seconds)
