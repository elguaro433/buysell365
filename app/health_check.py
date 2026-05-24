"""
BuySell365 Pro — health_check.py
Health-check HTTP local entre bot y copier.

PROBLEMA QUE RESUELVE:
Hoy bot.py y copier comunican via archivos JSON + heartbeats. Eso detecta
"proceso vivo" pero no "proceso correctamente operativo" (puede estar vivo
pero con thread monitor muerto). Tampoco hay manera de pedir info live al
otro proceso.

SOLUCION:
HTTP server local en cada proceso. Endpoints simples:
- GET /health  -> estado completo (200 si OK, 503 si degraded)
- GET /stats   -> contadores en RAM
- GET /open_signals -> dict en memoria del proceso

USO:
    from health_check import start_health_server
    start_health_server(port=5556, get_state_fn=lambda: {...})

    from health_check import check_peer
    health = check_peer(port=5557)  # consulta otro proceso
"""
import json
import threading
import time
import http.server
import socketserver
from typing import Callable, Optional, Dict, Any
import urllib.request
import urllib.error


_state_fn: Optional[Callable] = None


class _HealthHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # suprimir logs verbose

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/")
        try:
            if _state_fn is None:
                self._respond(503, {"error": "no state function registered"})
                return
            state = _state_fn()
            if path == "/health":
                healthy = state.get("healthy", True)
                code = 200 if healthy else 503
                self._respond(code, state)
            elif path == "/stats":
                self._respond(200, state.get("stats", {}))
            elif path == "/open_signals":
                self._respond(200, state.get("open_signals", {}))
            elif path in ("", "/"):
                self._respond(200, {
                    "service": state.get("service", "unknown"),
                    "endpoints": ["/health", "/stats", "/open_signals"],
                })
            else:
                self._respond(404, {"error": "not found"})
        except Exception as e:
            self._respond(500, {"error": str(e)})

    def _respond(self, code: int, data: dict):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_health_server(port: int, get_state_fn: Callable[[], Dict[str, Any]]) -> bool:
    """Arranca HTTP server en localhost:port en thread daemon.

    Args:
        port: ej. 5556 (bot) o 5557 (copier)
        get_state_fn: callback que retorna dict con {service, healthy, stats, open_signals}

    Returns:
        True si arranco OK, False si puerto ocupado.
    """
    global _state_fn
    _state_fn = get_state_fn
    try:
        # Try-bind explicito antes de TCPServer (mejor error msg)
        import socket as _sock
        _s = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
        try:
            _s.bind(("127.0.0.1", port))
            _s.close()
        except OSError:
            return False
        # Arrancar real server
        socketserver.TCPServer.allow_reuse_address = True
        srv = socketserver.TCPServer(("127.0.0.1", port), _HealthHandler)

        def _run():
            try:
                srv.serve_forever()
            except Exception:
                pass

        t = threading.Thread(target=_run, daemon=True, name=f"health_server_{port}")
        t.start()
        return True
    except Exception:
        return False


def check_peer(port: int, timeout: float = 2.0) -> Optional[Dict[str, Any]]:
    """Consulta el health endpoint de otro proceso.

    Returns:
        dict del estado o None si no responde / error.
    """
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/health")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError):
        return None


# Puertos convencionales del proyecto
PORT_BOT = 5556
PORT_COPIER = 5557


if __name__ == "__main__":
    # Smoke test: arranca un server fake y consulta
    def _fake_state():
        return {
            "service": "test",
            "healthy": True,
            "stats": {"counter": 42},
            "open_signals": {"sig1": {"pair": "XAUUSD"}},
        }
    if start_health_server(5556, _fake_state):
        print("Server arrancado en 5556")
        time.sleep(1)
        h = check_peer(5556)
        print(f"check_peer result: {h}")
    else:
        print("ERROR arrancando server (puerto ocupado?)")
