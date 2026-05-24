"""
BuySell365 Pro — state_lock.py
Lock cross-proceso para archivos de estado compartidos (estado.json, etc).

PROBLEMA QUE RESUELVE:
4 escritores potenciales de estado.json (bot.py, launcher.py, send_manual_signal.py,
backup logic) sin coordinacion. Aunque cada uno usa tmp+rename atomico, NO previene
LOST UPDATES — si dos procesos leen al mismo tiempo y escriben con sus respectivos
cambios, el ultimo escritor pisa los cambios del otro sin merge.

SOLUCION:
portalocker — lock cross-proceso compatible Windows + Linux. Cada operacion
read-modify-write toma lock exclusivo, hace su trabajo, libera lock.

USO:
    from state_lock import locked_update_json
    def my_change(data):
        data["my_field"] = "new_value"
        return data
    locked_update_json("estado.json", my_change)

O manual:
    from state_lock import file_lock
    with file_lock("estado.json"):
        # leer + modificar + escribir aqui
        ...
"""
import os
import json
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Any, Optional, Dict

try:
    import portalocker
    _HAS_PORTALOCKER = True
except ImportError:
    _HAS_PORTALOCKER = False
    portalocker = None  # type: ignore

BASE_DIR = Path(__file__).parent


@contextmanager
def file_lock(target_path: str, timeout: float = 30.0):
    """Context manager: lock cross-proceso exclusivo sobre un archivo.

    Crea un archivo .lock al lado del target. Bloquea hasta poder adquirir
    o hasta timeout. NO falla si portalocker no esta disponible (degrada
    a no-op con warning).

    Args:
        target_path: ruta del archivo principal (ej. "estado.json")
        timeout: segundos a esperar antes de fallar (default 30s)
    """
    if not _HAS_PORTALOCKER:
        # Sin portalocker: no-op pero loguear para auditoria
        try:
            from events_log import log_event as _log_event
            _log_event("state_lock.no_portalocker", source="state_lock",
                       data={"target": str(target_path)})
        except Exception:
            pass
        yield None
        return

    target = Path(target_path)
    if not target.is_absolute():
        target = BASE_DIR / target
    lock_path = str(target) + ".lock"
    lock_file = None
    try:
        lock_file = open(lock_path, "a+")
        # portalocker 3.x cambio API — usar Lock con retries propios
        _start = time.time()
        _acquired = False
        while time.time() - _start < timeout:
            try:
                portalocker.lock(lock_file, portalocker.LOCK_EX | portalocker.LOCK_NB)
                _acquired = True
                break
            except (portalocker.LockException, portalocker.AlreadyLocked, BlockingIOError):
                time.sleep(0.1)
        if not _acquired:
            raise TimeoutError(f"No se pudo adquirir lock para {target} en {timeout}s")
        yield lock_file
    finally:
        if lock_file:
            try:
                portalocker.unlock(lock_file)
            except Exception:
                pass
            try:
                lock_file.close()
            except Exception:
                pass


def locked_update_json(target_path: str, mutate_fn: Callable[[Dict], Dict],
                       timeout: float = 30.0, default: Optional[Dict] = None) -> Dict:
    """Read-modify-write atomico cross-proceso para JSONs.

    Bajo lock exclusivo:
    1. Lee target_path
    2. Llama mutate_fn(data) — debe retornar el nuevo data
    3. Escribe data via tmp+rename
    4. Libera lock

    Args:
        target_path: ruta al JSON
        mutate_fn: funcion que recibe dict y retorna dict modificado
        timeout: segundos para adquirir lock
        default: valor inicial si el archivo no existe

    Returns:
        El dict final escrito.
    """
    target = Path(target_path)
    if not target.is_absolute():
        target = BASE_DIR / target
    with file_lock(str(target), timeout=timeout):
        data = default if default is not None else {}
        if target.exists():
            try:
                with open(target, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                # Corrupto — usar default y emitir warning
                try:
                    from events_log import log_event as _log_event
                    _log_event("state_lock.corrupt_load", source="state_lock",
                               data={"target": str(target)})
                except Exception:
                    pass
                data = default if default is not None else {}
        new_data = mutate_fn(data)
        if not isinstance(new_data, dict):
            raise TypeError("mutate_fn debe retornar dict")
        tmp = str(target) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(new_data, f, ensure_ascii=False, indent=2, default=str)
            f.flush()
            try:
                os.fsync(f.fileno())
            except Exception:
                pass
        os.replace(tmp, target)
        return new_data


def has_portalocker() -> bool:
    return _HAS_PORTALOCKER


if __name__ == "__main__":
    print(f"portalocker disponible: {_HAS_PORTALOCKER}")
    if _HAS_PORTALOCKER:
        print(f"  version: {portalocker.__version__}")
    # Smoke test
    test_path = BASE_DIR / "_state_lock_test.json"
    try:
        result = locked_update_json(
            str(test_path),
            lambda d: {**d, "counter": d.get("counter", 0) + 1, "ts": time.time()}
        )
        print(f"Test OK — counter: {result.get('counter')}")
        if test_path.exists():
            test_path.unlink()
        lock_path = Path(str(test_path) + ".lock")
        if lock_path.exists():
            lock_path.unlink()
    except Exception as e:
        print(f"Test FAIL: {e}")
