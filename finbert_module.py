import time
import logging
import requests
import finnhub
import os

logger = logging.getLogger(__name__)

_cache_sentimiento = {}
_SENT_TTL = 1800  # Cache de sentimiento: 30 min
_HF_ERROR_TTL = 900  # Si HF falla, NO reintentar por 15 min
_ultimo_error_hf = 0  # Timestamp del último fallo HF

FINNHUB_KEY = os.getenv("FINNHUB_KEY", "")

# URLs de HuggingFace — fallback si la principal falla
HF_API_URLS = [
    "https://router.huggingface.co/hf-inference/models/ProsusAI/finbert",
    "https://api-inference.huggingface.co/models/ProsusAI/finbert",
]

TICKER_NEWS_MAP = {
    'GC=F': 'gold',
    'EURUSD=X': 'forex EURUSD',
    'USDJPY=X': 'forex USDJPY',
    'BTC-USD': 'bitcoin crypto',
    'ES=F': 'S&P 500 stock market',
    'NQ=F': 'nasdaq tech stocks',
}

def obtener_noticias(ticker, max_noticias=10):
    try:
        client = finnhub.Client(api_key=FINNHUB_KEY)
        query = TICKER_NEWS_MAP.get(ticker, ticker)
        noticias = client.general_news('general', min_id=0)
        relevantes = []
        for n in noticias:
            titulo = n.get('headline', '').lower()
            if any(kw in titulo for kw in query.lower().split()):
                relevantes.append(n.get('headline', ''))
            if len(relevantes) >= max_noticias:
                break
        return relevantes
    except Exception as e:
        logger.warning(f"Error noticias Finnhub: {e}")
        return []

def _analizar_con_hf_api(textos, max_retries=2):
    """Analiza textos con FinBERT via HuggingFace API.
    Si la API falla, no reintenta por 15 min (evita martillear)."""
    global _ultimo_error_hf

    # Si falló recientemente, no reintentar
    if _ultimo_error_hf and (time.time() - _ultimo_error_hf) < _HF_ERROR_TTL:
        logger.info("HF API en cooldown — saltando FinBERT")
        return None

    headers = {"Authorization": f"Bearer {os.getenv('HF_TOKEN', '')}"}

    for url in HF_API_URLS:
        for i in range(max_retries):
            try:
                response = requests.post(url, headers=headers, json={"inputs": textos}, timeout=45)
                if response.status_code == 200:
                    data = response.json()
                    if isinstance(data, dict) and "error" in data:
                        if "loading" in data.get("error", "").lower():
                            wait_time = min(data.get("estimated_time", 20), 30)
                            logger.info(f"Modelo FinBERT cargando. Esperando {wait_time}s...")
                            time.sleep(wait_time)
                            continue
                        raise ValueError(data["error"])
                    _ultimo_error_hf = 0  # Reset en éxito
                    return data
                elif response.status_code == 503:
                    logger.warning(f"HF API 503 (cargando) en {url} — reintento {i+1}")
                    time.sleep(15)
                elif response.status_code == 404:
                    logger.warning(f"HF API 404 en {url} — probando URL alternativa")
                    break  # Probar siguiente URL
                else:
                    logger.warning(f"HF API {response.status_code} en {url}: {response.text[:200]}")
                    time.sleep(3)
            except requests.exceptions.Timeout:
                logger.warning(f"HF API timeout en {url} — reintento {i+1}")
                time.sleep(3)
            except Exception as e:
                logger.warning(f"HF API error en {url} (intento {i+1}): {e}")
                time.sleep(3)

    # Todas las URLs fallaron → marcar cooldown
    _ultimo_error_hf = time.time()
    logger.warning(f"HF API falló en todas las URLs — no se reintentará por {_HF_ERROR_TTL//60} min")
    return None

def analizar_sentimiento_finbert(ticker):
    global _cache_sentimiento
    cached = _cache_sentimiento.get(ticker)
    if cached and (time.time() - cached['ts']) < _SENT_TTL:
        return cached

    noticias = obtener_noticias(ticker)
    if not noticias:
        result = {'score': 0.0, 'label': 'neutral', 'confianza': 0.0, 'n_noticias': 0, 'ts': time.time()}
        _cache_sentimiento[ticker] = result
        return result

    try:
        resultados = _analizar_con_hf_api(noticias[:10])
        if resultados is None:
            result = {'score': 0.0, 'label': 'neutral', 'confianza': 0.0, 'n_noticias': 0, 'ts': time.time()}
            _cache_sentimiento[ticker] = result
            return result

        scores = []
        for r in resultados:
            pos = neg = 0
            if isinstance(r, list):
                for item in r:
                    if isinstance(item, dict):
                        if item.get('label') == 'positive':
                            pos = item.get('score', 0)
                        elif item.get('label') == 'negative':
                            neg = item.get('score', 0)
            elif isinstance(r, dict):
                # Formato alternativo: un solo dict por resultado
                if r.get('label') == 'positive':
                    pos = r.get('score', 0)
                elif r.get('label') == 'negative':
                    neg = r.get('score', 0)
            scores.append(pos - neg)

        avg_score = sum(scores) / len(scores) if scores else 0
        if avg_score > 0.15:
            label = 'positive'
        elif avg_score < -0.15:
            label = 'negative'
        else:
            label = 'neutral'

        result = {
            'score': round(avg_score, 3),
            'label': label,
            'confianza': round(abs(avg_score), 3),
            'n_noticias': len(noticias),
            'ts': time.time()
        }
        _cache_sentimiento[ticker] = result
        logger.info(f"FinBERT {ticker}: {label} ({avg_score:.3f}) de {len(noticias)} noticias")
        return result

    except Exception as e:
        logger.warning(f"Error FinBERT: {e}")
        result = {'score': 0.0, 'label': 'neutral', 'confianza': 0.0, 'n_noticias': 0, 'ts': time.time()}
        _cache_sentimiento[ticker] = result
        return result

def sentimiento_confirma_senal(ticker, tipo_senal):
    sent = analizar_sentimiento_finbert(ticker)
    label = sent['label']
    score = sent['score']
    conf = sent['confianza']
    if sent['n_noticias'] == 0:
        return True, "Sin noticias recientes", 0.0
    if tipo_senal == 'COMPRA':
        if label == 'positive':
            return True, f"Sentimiento POSITIVO ({score:.2f}, {sent['n_noticias']} noticias)", min(conf * 2, 1.0)
        elif label == 'neutral':
            return True, f"Sentimiento neutro ({score:.2f})", 0.3
        else:
            return False, f"Sentimiento NEGATIVO ({score:.2f})", 0.0
    elif tipo_senal == 'VENTA':
        if label == 'negative':
            return True, f"Sentimiento NEGATIVO ({score:.2f}, {sent['n_noticias']} noticias)", min(conf * 2, 1.0)
        elif label == 'neutral':
            return True, f"Sentimiento neutro ({score:.2f})", 0.3
        else:
            return False, f"Sentimiento POSITIVO contra VENTA ({score:.2f})", 0.0
    return True, "Sentimiento no evaluado", 0.0
