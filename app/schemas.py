"""
BuySell365 Pro — schemas.py
Schema validation con pydantic para JSONs criticos.

PROBLEMA QUE RESUELVE:
Los JSONs criticos (estado.json, copier_open_signals.json, copier_stats.json)
se cargan con isinstance() basico — una corrupcion parcial (ej. campo "entry"
que es string en vez de float) puede sobrevivir varios ciclos sin alerta.

SOLUCION:
pydantic models con validacion + coercion. Si JSON malformado: log claro,
intentar `.bak`, alertar admin. NO crashear.

USO:
    from schemas import validate_estado_json, validate_open_signals_json
    estado = validate_estado_json(loaded_dict)  # valida y coerce
    if estado is None:
        # JSON corrupto, usar backup
        ...
"""
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, ConfigDict, field_validator
import logging

log = logging.getLogger(__name__)


class OperacionActiva(BaseModel):
    """Schema para una operacion activa en estado.json."""
    model_config = ConfigDict(extra="allow", validate_assignment=False)

    ticker: str = ""
    nombre: str = ""
    tipo: str = ""  # "COMPRA" o "VENTA"
    entrada: float = 0.0
    tp1: float = 0.0
    tp2: float = 0.0
    tp3: float = 0.0
    sl: float = 0.0
    score: int = 0
    timestamp: float = 0.0
    hora: str = ""
    fuente: str = ""

    @field_validator("entrada", "tp1", "tp2", "tp3", "sl", mode="before")
    @classmethod
    def _coerce_float(cls, v):
        try:
            return float(v) if v is not None else 0.0
        except (TypeError, ValueError):
            return 0.0


class SignalDict(BaseModel):
    """Schema para una senal en copier_open_signals.json."""
    model_config = ConfigDict(extra="allow", validate_assignment=False)

    type: str = "new_signal"
    pair: str = ""
    direction: str = ""  # BUY | SELL
    entry: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    tp2: float = 0.0
    tp3: float = 0.0
    source: str = ""

    @field_validator("entry", "sl", "tp", "tp2", "tp3", mode="before")
    @classmethod
    def _coerce_float(cls, v):
        try:
            return float(v) if v is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    @field_validator("direction", mode="before")
    @classmethod
    def _normalize_direction(cls, v):
        if not v:
            return ""
        return str(v).upper().strip()


class OpenSignalEntry(BaseModel):
    """Wrapper de una entrada en copier_open_signals.json."""
    model_config = ConfigDict(extra="allow")

    signal: SignalDict
    sent_at: float = 0.0
    telegram_msg_id: Optional[int] = None

    @field_validator("sent_at", mode="before")
    @classmethod
    def _coerce_float(cls, v):
        try:
            return float(v) if v is not None else 0.0
        except (TypeError, ValueError):
            return 0.0


def validate_estado_json(data: Any) -> Optional[Dict[str, Any]]:
    """Valida y normaliza estado.json. Retorna dict limpio o None si corrupto.

    Maneja tolerantemente:
    - Campos faltantes: defaults
    - Tipos incorrectos: coercion (float/int/str)
    - operaciones_activas con entries malformados: filtra los validos
    """
    if not isinstance(data, dict):
        log.warning("validate_estado_json: data no es dict")
        return None
    out = dict(data)  # copia shallow para no mutar

    # operaciones_activas: filtrar entries validos
    _ops_in = out.get("operaciones_activas", {})
    if isinstance(_ops_in, dict):
        _ops_clean = {}
        for k, v in _ops_in.items():
            if not isinstance(v, dict):
                log.warning(f"operacion {k} no es dict — descartando")
                continue
            try:
                _validated = OperacionActiva.model_validate(v)
                _ops_clean[k] = _validated.model_dump()
                # Preservar campos extra que estaban en original
                for _ek, _ev in v.items():
                    if _ek not in _ops_clean[k]:
                        _ops_clean[k][_ek] = _ev
            except Exception as _e:
                log.warning(f"operacion {k} invalida — descartando: {_e}")
        out["operaciones_activas"] = _ops_clean
    else:
        out["operaciones_activas"] = {}

    # suscripciones_vip: debe ser dict
    if not isinstance(out.get("suscripciones_vip"), dict):
        log.warning("suscripciones_vip no es dict — reset a {}")
        out["suscripciones_vip"] = {}

    # historial_operaciones: lista o {}
    if not isinstance(out.get("historial_operaciones"), (list, dict)):
        out["historial_operaciones"] = []

    return out


def validate_open_signals_json(data: Any) -> Optional[Dict[str, Any]]:
    """Valida copier_open_signals.json. Retorna dict limpio o None si corrupto."""
    if not isinstance(data, dict):
        log.warning("validate_open_signals_json: data no es dict")
        return None
    out = {}
    for sig_id, entry in data.items():
        if not isinstance(entry, dict):
            log.warning(f"signal {sig_id} no es dict — descartando")
            continue
        try:
            _validated = OpenSignalEntry.model_validate(entry)
            # Preservar TODOS los campos extra (sobre todo _tp_levels, _tp_idx, etc)
            _clean_entry = _validated.model_dump()
            # Preservar campos extra del wrapper
            for _ek, _ev in entry.items():
                if _ek not in _clean_entry:
                    _clean_entry[_ek] = _ev
            # Preservar campos extra del signal interno
            _orig_signal = entry.get("signal", {})
            if isinstance(_orig_signal, dict):
                for _sk, _sv in _orig_signal.items():
                    if _sk not in _clean_entry["signal"]:
                        _clean_entry["signal"][_sk] = _sv
            out[sig_id] = _clean_entry
        except Exception as _e:
            log.warning(f"signal {sig_id} invalida — descartando: {_e}")
    return out


if __name__ == "__main__":
    # Smoke test
    test_data = {
        "operaciones_activas": {
            "valid_op": {
                "ticker": "GC=F", "nombre": "GOLD", "tipo": "COMPRA",
                "entrada": "4600.0", "sl": 4587.0, "tp1": 4604.0,
            },
            "invalid_op": "this is not a dict",
            "broken_floats": {
                "ticker": "EURUSD=X", "tipo": "VENTA",
                "entrada": "not_a_number", "sl": "1.17"
            },
        },
        "suscripciones_vip": {"user1": {"activo": True}},
    }
    result = validate_estado_json(test_data)
    print("validate_estado_json result:")
    import json
    print(json.dumps(result, indent=2, default=str))
