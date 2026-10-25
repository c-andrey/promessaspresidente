"""Coletores de documentos oficiais.

Cada coletor recebe a data inicial e devolve uma lista de documentos no formato:
    {id, fonte, oficial, tipo, titulo, resumo, url, data}
"""
import logging

from . import camara, dou, legislacao, noticias, planalto

log = logging.getLogger(__name__)

COLETORES = {
    "legislacao": legislacao.coletar,
    "camara": camara.coletar,
    "planalto_noticias": planalto.coletar,
    "dou": dou.coletar,
    "noticias": noticias.coletar,
}


def coletar_tudo(desde: dict[str, str], ate: str, cfg: dict) -> tuple[list[dict], dict]:
    """Roda todos os coletores ativos, cada um a partir da sua última coleta bem-sucedida.

    Falha de um não derruba os outros. O relatório traz a contagem ou "erro: ...";
    quem chama só avança a data dos coletores que deram certo.
    """
    docs, relatorio = [], {}
    for nome, funcao in COLETORES.items():
        c = cfg["coletores"].get(nome, {})
        if not c.get("ativo"):
            continue
        try:
            achados = funcao(desde[nome], ate, c)
            for d in achados:
                d["oficial"] = c.get("oficial", False)
            docs.extend(achados)
            relatorio[nome] = len(achados)
            log.info("[%s] %d documentos desde %s", nome, len(achados), desde[nome])
        except Exception as e:  # noqa: BLE001 — coletor quebrado vira aviso, não erro fatal
            relatorio[nome] = f"erro: {e}"
            log.warning("[%s] falhou: %s", nome, e)
    return docs, relatorio
