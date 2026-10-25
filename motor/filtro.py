"""Filtro barato (sem IA) que cruza documentos com promessas por palavra-chave.

É ele que segura o custo: só documento com candidata vai para a IA.
"""
import re
import unicodedata
from functools import lru_cache


def normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto.lower())
    sem_acento = "".join(c for c in sem_acento if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", sem_acento)


@lru_cache(maxsize=4096)
def _padrao(palavra: str) -> re.Pattern:
    return re.compile(r"(?<![\w-])" + re.escape(normalizar(palavra)) + r"(?![\w-])")


def pontuar(texto_doc: str, promessa: dict) -> tuple[int, list[str]]:
    """Pontua pela quantidade de palavras de cada termo encontrado (termo longo vale mais)."""
    achados = [k for k in promessa["palavras_chave"] if _padrao(k).search(texto_doc)]
    return sum(len(k.split()) for k in achados), achados


def candidatas(doc: dict, promessas: list[dict], maximo: int = 5) -> list[dict]:
    texto = normalizar(f"{doc.get('titulo', '')} {doc.get('resumo', '')}")
    pontuadas = []
    for p in promessas:
        pontos, achados = pontuar(texto, p)
        if pontos:
            pontuadas.append((pontos, p["id"], achados))
    pontuadas.sort(key=lambda x: (-x[0], x[1]))
    return [{"id": pid, "termos": achados} for _, pid, achados in pontuadas[:maximo]]
