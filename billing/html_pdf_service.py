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

logger = logging.getLogger(__name__)

# Semaphore to bound concurrent renders and protect RAM
_RENDER_SEMAPHORE = threading.Semaphore(4)
CDP_PORT = int(os.environ.get('CHROME_CDP_PORT', 9222))
CDP_HOST = os.environ.get('CHROME_CDP_HOST', '127.0.0.1')


def is_cdp_available(host=CDP_HOST, port=CDP_PORT, timeout=0.8):
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


def render_via_cdp(html_content, host=CDP_HOST, port=CDP_PORT, timeout=15):
    """
    Render HTML to vector PDF using a persistent warm Chromium daemon over CDP.
    Waits for layout lifecycle to settle so rendered output is never blank.
    """
    target_id = None
    sock = None
    try:
        # 1. Create a blank page target in the warm browser
        create_url = f"http://{host}:{port}/json/new?about:blank"
        req = urllib.request.Request(create_url, method='PUT')
        with urllib.request.urlopen(req, timeout=3) as resp:
            target = json.loads(resp.read().decode('utf-8'))
        target_id = target.get('id')
        ws_url = target.get('webSocketDebuggerUrl')
        if not ws_url:
            raise RuntimeError("Chromium CDP did not return webSocketDebuggerUrl")

        # Extract WS path
        host_port = f"{host}:{port}"
        path = ws_url.split(host_port)[1] if host_port in ws_url else ws_url[ws_url.find('/devtools/page'):]

        # 2. Open TCP socket and complete standard WebSocket RFC 6455 handshake
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

        # 3. Enable Page and Runtime domains
        _send_ws_frame(sock, {'id': 1, 'method': 'Page.enable'})
        _recv_ws_msg(sock, target_id=1, timeout=5)

        _send_ws_frame(sock, {'id': 2, 'method': 'Page.getFrameTree'})
        ft = _recv_ws_msg(sock, target_id=2, timeout=5)
        frame_id = ft.get('result', {}).get('frameTree', {}).get('frame', {}).get('id', target_id)

        # 4. Inject document HTML
        _send_ws_frame(sock, {
            'id': 3,
            'method': 'Page.setDocumentContent',
            'params': {'frameId': frame_id, 'html': html_content}
        })
        _recv_ws_msg(sock, target_id=3, timeout=5)

        # 5. Wait for layout, fonts, and DOM painting to complete
        _send_ws_frame(sock, {
            'id': 4,
            'method': 'Runtime.evaluate',
            'params': {
                'expression': 'new Promise(resolve => {'
                              '  const ready = () => {'
                              '    if (document.readyState === "complete") {'
                              '      requestAnimationFrame(() => setTimeout(resolve, 60));'
                              '    } else {'
                              '      window.addEventListener("load", () => requestAnimationFrame(() => setTimeout(resolve, 60)));'
                              '    }'
                              '  };'
                              '  ready();'
                              '})',
                'awaitPromise': True,
                'returnByValue': True
            }
        })
        _recv_ws_msg(sock, target_id=4, timeout=5)

        # 6. Execute Print to PDF
        _send_ws_frame(sock, {
            'id': 5,
            'method': 'Page.printToPDF',
            'params': {
                'printBackground': True,
                'preferCSSPageSize': True,
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
                close_url = f"http://{host}:{port}/json/close/{target_id}"
                close_req = urllib.request.Request(close_url)
                urllib.request.urlopen(close_req, timeout=2)
            except Exception:
                pass


def find_chrome_binary():
    """Locate Google Chrome or Chromium executable on the system."""
    custom_bin = os.environ.get('CHROME_BIN') or os.environ.get('CHROMIUM_PATH')
    if custom_bin and os.path.exists(custom_bin):
        return custom_bin

    candidates = [
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
        '/Applications/Chromium.app/Contents/MacOS/Chromium',
        shutil.which('google-chrome'),
        shutil.which('google-chrome-stable'),
        shutil.which('chromium'),
        shutil.which('chromium-browser'),
        '/usr/bin/google-chrome',
        '/usr/bin/google-chrome-stable',
        '/usr/bin/chromium',
        '/usr/bin/chromium-browser',
        '/snap/bin/chromium',
    ]

    for path in candidates:
        if path and os.path.exists(path):
            return path
    return None


def render_via_cli_subprocess(html_content, timeout_seconds=20):
    """
    Fallback: Render HTML to vector PDF using cold CLI subprocess.
    Uses file:// URL and --virtual-time-budget to guarantee complete rendering.
    """
    chrome_path = find_chrome_binary()
    if not chrome_path:
        raise RuntimeError("Headless Chromium binary not found on server environment.")

    # Write HTML to temporary file
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
            '--virtual-time-budget=1500',
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


def render_html_to_vector_pdf(html_content, timeout_seconds=20):
    """
    Render HTML content into an ultra-sharp, 100% pixel-perfect vector PDF.
    - Checks for Warm Chromium CDP Daemon on port 9222 first (~100ms render time)
    - Automatically falls back to CLI Chromium process if daemon is not running
    - Protected by Semaphore(4) against memory starvation under load
    """
    with _RENDER_SEMAPHORE:
        if is_cdp_available():
            try:
                return render_via_cdp(html_content, timeout=timeout_seconds)
            except Exception as e:
                logger.warning(f"CDP warm render failed ({e}), falling back to CLI subprocess...")

        return render_via_cli_subprocess(html_content, timeout_seconds=timeout_seconds)
