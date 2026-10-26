"""Pergunta à IA se um documento mexe no status das promessas candidatas.

Um documento vai junto com até N promessas candidatas numa única chamada, para
economizar cota dos planos gratuitos.
"""
import json

from .llm import Cadeia, Resultado
from .llm.erros import RespostaInvalida
from .regras import inicio_acompanhamento

STATUS_SUGERIDOS = ["sem_mudanca", "em_andamento", "parcial", "cumprida", "abandonada"]

SCHEMA = {
    "type": "object",
    "properties": {
        "avaliacoes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "promessa_id": {"type": "string"},
                    "relevante": {"type": "boolean"},
                    "status_sugerido": {"type": "string", "enum": STATUS_SUGERIDOS},
                    "confianca": {"type": "number"},
                    "evidencia": {"type": "string"},
                    "justificativa": {"type": "string"},
                },
                "required": ["promessa_id", "relevante", "status_sugerido",
                             "confianca", "evidencia", "justificativa"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["avaliacoes"],
    "additionalProperties": False,
}


def montar_sistema(presidente: dict) -> str:
    reeleito = (" (foi reeleito: o governo atual já é dele, e as promessas são as do novo plano)"
                if str(presidente.get("acompanhar_desde", "")) < str(presidente["mandato_inicio"]) else "")
    return f"""Você é um verificador apartidário de promessas de campanha.
O presidente acompanhado é {presidente.get('nome_completo', presidente['nome'])} ({presidente['partido']}), mandato de {presidente['mandato_inicio']} a {presidente['mandato_fim']}.
Atos do governo federal a partir de {inicio_acompanhamento(presidente)} contam como ações dele{reeleito}.

Você recebe UM documento oficial (proposição legislativa, ato do Diário Oficial ou notícia do governo) e uma lista de promessas candidatas, escolhidas por palavra-chave. Muitas candidatas serão falsos positivos: na dúvida, marque relevante=false.

Para cada promessa, decida se o documento mostra uma AÇÃO DO GOVERNO FEDERAL que muda o andamento dela:
- sem_mudanca: o documento não tem relação real com a promessa, ou não altera nada.
- em_andamento: o governo deu um passo concreto (enviou projeto/PEC/MP ao Congresso, publicou decreto de regulamentação parcial, criou grupo de trabalho com prazo, lançou edital).
- parcial: parte da promessa já está em vigor, mas não toda (ex.: cortou 6 de 10 ministérios).
- cumprida: a medida prometida está EM VIGOR (lei sancionada, decreto publicado, programa lançado e funcionando). Proposta apenas enviada NÃO é cumprida.
- abandonada: o governo desistiu explicitamente, vetou, retirou o projeto ou agiu no sentido contrário.

Regras:
- Documento datado antes de {inicio_acompanhamento(presidente)} não conta para as promessas: use sem_mudanca.
- Notícia oficial é indício, não prova: confiança máxima 0.6 para notícias.
- "evidencia" deve ser um trecho LITERAL do documento (até 300 caracteres). Se não houver trecho que sustente, use relevante=false.
- "confianca" vai de 0 a 1.
- "justificativa": uma ou duas frases neutras, em português, sem adjetivos políticos.
- Avalie todas as promessas candidatas recebidas, usando exatamente os ids informados."""


def montar_usuario(doc: dict, promessas: list[dict]) -> str:
    candidatas = [
        {"id": p["id"], "promessa": p["titulo"], "texto_do_plano": p["fonte"]["trecho"],
         "status_atual": p["status"]}
        for p in promessas
    ]
    documento = {k: doc.get(k) for k in ("fonte", "tipo", "titulo", "data", "resumo", "url")}
    return (
        "DOCUMENTO:\n" + json.dumps(documento, ensure_ascii=False, indent=1)
        + "\n\nPROMESSAS CANDIDATAS:\n" + json.dumps(candidatas, ensure_ascii=False, indent=1)
    )


def classificar(cadeia: Cadeia, presidente: dict, doc: dict, promessas: list[dict]) -> Resultado:
    ids = {p["id"] for p in promessas}

    def validar(dados):
        for a in dados["avaliacoes"]:
            if a["promessa_id"] not in ids:
                raise RespostaInvalida(f"id desconhecido: {a['promessa_id']}")
            if not 0 <= a["confianca"] <= 1:
                raise RespostaInvalida(f"confiança fora de 0..1: {a['confianca']}")

    return cadeia.completar_json(montar_sistema(presidente), montar_usuario(doc, promessas),
                                 SCHEMA, validar=validar)
