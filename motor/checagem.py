"""Checagem de fatos: a notícia já foi desmentida por uma agência de checagem?

Usa a Google Fact Check Tools API (claims:search), gratuita com uma chave do Google
Cloud em FACTCHECK_API_KEY. Ela reúne as checagens publicadas com ClaimReview por
agências como Lupa, Aos Fatos, Estadão Verifica e AFP Checamos. Sem a chave, não faz
nada. Uma checagem que classifica a afirmação como falsa ou enganosa impede a mudança
automática de status e aparece para quem revisa.
"""
import logging
import os
import re

from .apuracao import normalizar

log = logging.getLogger(__name__)

API = "https://factchecktools.googleapis.com/v1alpha1/claims:search"
DESMENTE = re.compile(r"falso|false|enganos|mislead|fake|distorcid|sem contexto|fora de contexto|insustentavel"
                      r"|nao e bem assim|exagerad|impreciso|incorreto|boato")


def configurado() -> bool:
    return bool(os.environ.get("FACTCHECK_API_KEY"))


def desmentidos(c, consulta: str, max_idade_dias: int = 180) -> list[dict]:
    """Checagens que desmentem a consulta (geralmente a manchete). Lista vazia sem chave ou em erro."""
    chave = os.environ.get("FACTCHECK_API_KEY")
    if not chave or not consulta.strip():
        return []
    try:
        r = c.get(API, params={"query": consulta[:200], "languageCode": "pt", "maxAgeDays": max_idade_dias,
                               "pageSize": 5, "key": chave})
        r.raise_for_status()
        claims = r.json().get("claims", [])
    except Exception as e:  # noqa: BLE001 — checagem é reforço; sem ela, segue o fluxo normal
        log.info("checagem indisponível: %s", str(e).replace(chave, "***"))
        return []
    achados = []
    for claim in claims:
        for rev in claim.get("claimReview", []):
            if DESMENTE.search(normalizar(rev.get("textualRating", ""))):
                achados.append({"afirmacao": claim.get("text", ""), "avaliacao": rev.get("textualRating", ""),
                                "agencia": (rev.get("publisher") or {}).get("name", ""), "url": rev.get("url", "")})
    return achados
