"""Gera o site estático em dist/ a partir de data/ e config/.

Uso: python web/build.py
"""
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
from motor import armazem  # noqa: E402

SITE = RAIZ / "web"
DIST = RAIZ / "dist"

ROTULOS = {
    "nao_iniciada": "Não iniciada",
    "em_andamento": "Em andamento",
    "parcial": "Parcialmente cumprida",
    "cumprida": "Cumprida",
    "abandonada": "Abandonada",
}


def data_br(iso):
    if not iso:
        return ""
    a, m, d = str(iso)[:10].split("-")
    return f"{d}/{m}/{a}"


def main():
    promessas = armazem.ler_json(armazem.PROMESSAS, [])
    presidente = armazem.ler_yaml("presidente.yaml")
    execucao = armazem.ler_json(armazem.EXECUCAO, {})

    contagem = Counter(p["status"] for p in promessas)
    placar = [{"status": s, "rotulo": ROTULOS[s], "n": contagem.get(s, 0),
               "pct": round(100 * contagem.get(s, 0) / max(len(promessas), 1), 1)}
              for s in armazem.STATUS]
    categorias = sorted({p["categoria"] for p in promessas})
    recentes = sorted(
        ({"promessa": p, "evento": h} for p in promessas for h in p["historico"]),
        key=lambda x: x["evento"]["data"], reverse=True)[:8]

    env = Environment(loader=FileSystemLoader(SITE / "templates"),
                      autoescape=select_autoescape(["html"]))
    env.filters["data_br"] = data_br
    env.globals.update(rotulos=ROTULOS, presidente=presidente,
                       atualizado=data_br(execucao.get("executado_em")) or "ainda não sincronizado")

    if DIST.exists():
        shutil.rmtree(DIST)
    (DIST / "promessa").mkdir(parents=True)
    shutil.copytree(SITE / "static", DIST / "static")

    def gravar(caminho, template, **ctx):
        (DIST / caminho).write_text(env.get_template(template).render(**ctx), encoding="utf-8")

    gravar("index.html", "index.html", promessas=promessas, placar=placar,
           categorias=categorias, total=len(promessas), recentes=recentes, raiz="")
    gravar("metodologia.html", "metodologia.html", raiz="")
    for p in promessas:
        gravar(f"promessa/{p['id']}.html", "promessa.html", p=p, raiz="../")
    (DIST / "dados.json").write_text(json.dumps(promessas, ensure_ascii=False), encoding="utf-8")
    print(f"Site gerado em {DIST.relative_to(RAIZ)}/ ({len(promessas)} promessas)")


if __name__ == "__main__":
    main()
