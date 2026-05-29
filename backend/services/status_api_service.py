import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logger = logging.getLogger("StatusApiService")


class StatusApiService:
    def __init__(self, session_state, host="127.0.0.1"):
        self.state = session_state
        self.host = host
        self._server = None
        self._thread = None
        self._port = 0
        self._lock = threading.Lock()

    @property
    def port(self):
        return self._port

    def start(self, port):
        port = int(port)
        if port <= 0:
            self.stop()
            return True, "disabled"

        with self._lock:
            if self._server and self._port == port:
                return True, "already running"

        # stop old server before starting new one
        self.stop()

        service = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                # silence default stderr logging
                return

            def _send_json(self, status_code, payload):
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status_code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)

            def do_OPTIONS(self):
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.end_headers()

            def do_GET(self):
                if self.path in ("/status", "/status/", "/"):
                    self._send_json(
                        200,
                        {
                            "is_live": bool(service.state.is_live),
                            "room_id": service.state.room_id or "",
                        },
                    )
                    return
                if self.path in ("/health", "/healthz"):
                    self._send_json(200, {"ok": True})
                    return
                self._send_json(404, {"ok": False, "msg": "not found"})

        try:
            server = ThreadingHTTPServer((self.host, port), Handler)
        except OSError as e:
            logger.error(f"Failed to start status api on {self.host}:{port}: {e}")
            return False, str(e)

        thread = threading.Thread(target=server.serve_forever, name="StatusApiServer", daemon=True)
        with self._lock:
            self._server = server
            self._thread = thread
            self._port = port
        thread.start()
        logger.info(f"Status API started at http://{self.host}:{port}/status")
        return True, "started"

    def stop(self):
        with self._lock:
            server = self._server
            thread = self._thread
            self._server = None
            self._thread = None
            self._port = 0

        if not server:
            return
        try:
            server.shutdown()
        except Exception:
            pass
        try:
            server.server_close()
        except Exception:
            pass
        if thread:
            thread.join(timeout=2)
        logger.info("Status API stopped")

