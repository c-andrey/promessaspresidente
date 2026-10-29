"""Gera o site estático em dist/ a partir de data/ e config/.

Uso: python web/build.py
"""
import os
import shutil
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
from motor import armazem, regras  # noqa: E402
from motor.campanha import partidaria  # noqa: E402

SITE = RAIZ / "web"
DIST = Path(os.environ.get("SITE_DIR", RAIZ / "dist"))

ROTULOS = {
    "nao_iniciada": "Não iniciada",
    "em_andamento": "Em andamento",
    "parcial": "Parcialmente cumprida",
    "cumprida": "Cumprida",
    "abandonada": "Abandonada",
}

DESCRICOES = {
    "nao_iniciada": "Nenhum ato do governo encontrado até agora.",
    "em_andamento": "Houve um passo concreto: projeto enviado ao Congresso, decreto parcial ou programa em estruturação.",
    "parcial": "Parte do que foi prometido já está em vigor.",
    "cumprida": "A medida está em vigor: lei sancionada, decreto publicado ou programa funcionando.",
    "abandonada": "O governo desistiu, vetou, retirou a proposta ou agiu no sentido contrário.",
}

ORIGENS = {"plano_tse": "Plano oficial (TSE)", "declaracao": "Declaração de campanha"}
CONTEXTOS = {"entrevista": "entrevista", "debate": "debate", "sabatina": "sabatina", "discurso": "discurso",
             "rede_social": "rede social", "propaganda_eleitoral": "propaganda eleitoral", "outro": "declaração pública"}


def data_br(iso):
    if not iso:
        return ""
    a, m, d = str(iso)[:10].split("-")
    return f"{d}/{m}/{a}"


def data_hora_br(iso):
    """ISO em UTC → "25/10/2026 às 07:42" no horário do contêiner (TZ=America/Sao_Paulo)."""
    if not iso:
        return ""
    return datetime.fromisoformat(iso).astimezone().strftime("%d/%m/%Y às %H:%M")


def rfc822(iso):
    return format_datetime(datetime.combine(date.fromisoformat(str(iso)[:10]), datetime.min.time(), timezone.utc))


def site_url() -> str:
    return (os.environ.get("SITE_URL") or armazem.ler_yaml("site.yaml").get("url", "")).rstrip("/")


def ambiente(presidente: dict | None = None) -> Environment:
    """Templates com filtros e variáveis globais (também usado pelo painel de revisão)."""
    presidente = presidente or armazem.ler_yaml("presidente.yaml")
    execucao = armazem.ler_json(armazem.EXECUCAO, {})
    env = Environment(loader=FileSystemLoader(SITE / "templates"),
                      autoescape=select_autoescape(["html", "xml"]))
    env.filters.update(data_br=data_br, data_hora_br=data_hora_br, rfc822=rfc822)
    env.tests["partidaria"] = partidaria
    env.globals.update(rotulos=ROTULOS, descricoes=DESCRICOES, origens=ORIGENS, contextos=CONTEXTOS, presidente=presidente,
                       posse=str(presidente["mandato_inicio"]), site_url=site_url(),
                       atualizado=data_hora_br(execucao.get("executado_em")) or "ainda não sincronizado")
    return env


def eventos(promessas: list[dict]) -> list[dict]:
    """Todas as mudanças de status, mais recentes primeiro."""
    return sorted(({"promessa": p, "evento": h} for p in promessas for h in p["historico"]),
                  key=lambda x: x["evento"]["data"], reverse=True)


def semanas(lista: list[dict], quantas: int = 12) -> list[dict]:
    """Agrupa os eventos por semana (segunda a domingo), só as últimas `quantas` semanas."""
    corte = date.today() - timedelta(weeks=quantas)
    grupos: dict[date, list] = {}
    for e in lista:
        d = date.fromisoformat(e["evento"]["data"][:10])
        if d >= corte:
            grupos.setdefault(d - timedelta(days=d.weekday()), []).append(e)
    return [{"inicio": ini.isoformat(), "fim": (ini + timedelta(days=6)).isoformat(), "eventos": evs}
            for ini, evs in sorted(grupos.items(), reverse=True)]


def main():
    promessas = armazem.ler_json(armazem.PROMESSAS, [])
    presidente = armazem.ler_yaml("presidente.yaml")

    contagem = Counter(p["status"] for p in promessas)
    placar = [{"status": s, "rotulo": ROTULOS[s], "n": contagem.get(s, 0),
               "pct": round(100 * contagem.get(s, 0) / max(len(promessas), 1), 1)}
              for s in armazem.STATUS]
    categorias = sorted({p["categoria"] for p in promessas})
    n_plano = sum(1 for p in promessas if p.get("origem", "plano_tse") == "plano_tse")
    todos = eventos(promessas)
    env = ambiente(presidente)

    # Esvazia em vez de apagar a pasta: em produção ela é um volume montado.
    DIST.mkdir(parents=True, exist_ok=True)
    for item in DIST.iterdir():
        shutil.rmtree(item) if item.is_dir() else item.unlink()
    (DIST / "promessa").mkdir()
    shutil.copytree(SITE / "static", DIST / "static")

    def gravar(caminho, template, **ctx):
        (DIST / caminho).write_text(env.get_template(template).render(pagina=caminho, **ctx), encoding="utf-8")

    gravar("index.html", "index.html", promessas=promessas, placar=placar,
           categorias=categorias, total=len(promessas), recentes=todos[:8], raiz="",
           n_plano=n_plano, n_declaracoes=len(promessas) - n_plano)
    gravar("metodologia.html", "metodologia.html", raiz="")
    gravar("mudancas.html", "mudancas.html", semanas=semanas(todos), raiz="")
    gravar("feed.xml", "feed.xml", eventos=todos[:50], raiz="")
    for p in promessas:
        regras.limpar_noticias(p)  # ordem por data e sem repetição também no que acabou de entrar
        gravar(f"promessa/{p['id']}.html", "promessa.html", p=p, raiz="../")
    print(f"Site gerado em {DIST}/ ({len(promessas)} promessas)")


if __name__ == "__main__":
    main()
