"""Interpreta respostas de limite (429) dos provedores compatíveis com OpenAI.

Cada provedor avisa de um jeito:
- Groq/OpenRouter/GitHub: cabeçalhos retry-after e x-ratelimit-* ("1m26.4s", "7.66s", epoch em ms…)
- Gemini: corpo com RetryInfo.retryDelay ("32s") e quotaId contendo "PerDay" ou "PerMinute"
- mensagens em texto ("tokens per day (TPD)", "requests per minute")
"""
import json
import re

# Acima disso não compensa esperar: trata como cota do dia (ou pausa longa).
ESPERA_MAXIMA = 120

_DIARIO = re.compile(r"per ?day|perday|daily|\brpd\b|\btpd\b|bymodelbyday|free_tier_requests", re.I)


def segundos(texto: str | None) -> float | None:
    """'1m26.4s' → 86.4 · '7.66s' → 7.66 · '250ms' → 0.25 · '30' → 30."""
    if not texto:
        return None
    texto = texto.strip()
    try:
        return float(texto)
    except ValueError:
        pass
    total, achou = 0.0, False
    for valor, unidade in re.findall(r"([\d.]+)\s*(ms|h|m|s)", texto):
        total += float(valor) * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[unidade]
        achou = True
    return total if achou else None


def mensagem_curta(corpo: str, limite: int = 140) -> str:
    """Extrai error.message de corpos JSON (objeto ou lista, como o Gemini manda) em uma linha."""
    try:
        d = json.loads(corpo)
        if isinstance(d, list) and d:
            d = d[0]
        msg = d.get("error", {}).get("message") or corpo
    except (ValueError, AttributeError):
        msg = corpo
    msg = " ".join(str(msg).split())
    return msg[:limite] + ("…" if len(msg) > limite else "")


def _retry_delay_gemini(corpo: str) -> float | None:
    m = re.search(r'"retryDelay"\s*:\s*"([\d.]+)s"', corpo)
    return float(m.group(1)) if m else None


def classificar_429(headers, corpo: str) -> tuple[bool, float]:
    """(é_diário, espera_em_segundos) para uma resposta 429."""
    espera = (segundos(headers.get("retry-after"))
              or _retry_delay_gemini(corpo)
              or segundos(headers.get("x-ratelimit-reset-tokens"))
              or segundos(headers.get("x-ratelimit-reset-requests"))
              or 20.0)
    diario = bool(_DIARIO.search(corpo)) or headers.get("x-ratelimit-remaining-requests") == "0"
    if "PerMinute" in corpo and "PerDay" not in corpo:
        diario = False
    return diario or espera > ESPERA_MAXIMA, espera


def pausa_pelos_cabecalhos(headers) -> tuple[bool, float]:
    """Depois de um sucesso: (cota_do_dia_acabou, segundos_até_poder_chamar_de_novo)."""
    acabou = headers.get("x-ratelimit-remaining-requests") == "0"
    restantes = headers.get("x-ratelimit-remaining-tokens")
    espera = 0.0
    if restantes is not None and restantes.isdigit() and int(restantes) < 1500:
        espera = segundos(headers.get("x-ratelimit-reset-tokens")) or 0.0
    return acabou, espera
