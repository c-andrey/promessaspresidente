# Cumpriu?

Acompanhamento diário das promessas do plano de governo do presidente, com um documento oficial como prova de cada mudança de status.

- **Promessas**: 114 compromissos extraídos do plano de governo de Flávio Bolsonaro registrado no TSE (`data/promessas.json`). Cada um tem a página e o trecho literal do PDF.
- **Sync diário**: GitHub Actions coleta legislação federal, Câmara e Planalto → filtro por palavra-chave → IA (cadeia de provedores gratuitos com fallback) → regras → commit em `data/` → deploy do site na VPS (`/srv/promessaspresidente`, nginx atrás do edge-proxy).
- **Auditoria**: todo o estado é JSON versionado. O `git log data/promessas.json` é o histórico público.

## Como funciona

```
coletores (legislação federal via Senado, Câmara, Planalto; DOU opcional via túnel na VPS)
        │  ~dezenas de documentos/dia
        ▼
filtro por palavra-chave (sem IA, custo zero)
        │  só documentos com promessa candidata
        ▼
cadeia de IA: github-models → gemini → groq → mistral → cloudflare → openrouter → [ollama] → [claude-haiku]
        │  JSON validado contra schema
        ▼
regras: nao_iniciada → em_andamento (oficial, confiança ≥ 0.85, pós-posse) = automático
        todo o resto (parcial, cumprida, abandonada) = fila de revisão humana
```

Quando um provedor devolve 429/402 ou passa do `limite_diario`, ele fica marcado como esgotado no dia (`data/estado/uso_llm.json`) e a cadeia segue para o próximo. Se todos esgotarem, os documentos que sobraram ficam em `data/estado/pendentes.json` e são processados no dia seguinte.

## Deploy (GitHub Actions + VPS)

| Workflow | Quando | O que faz |
|---|---|---|
| `deploy.yml` | push na `main`, manual, ou chamado pelo sync | testes → `web/build.py` → rsync de `dist/` + `deploy/` para `/srv/promessaspresidente` → `docker compose up` (nginx em `127.0.0.1:3200`, alias `promessas` na rede `edge`) |
| `sync.yml` | todo dia 07:00 (Brasília) e manual | túnel SOCKS pela VPS → `motor.main` → commit em `data/` → chama o `deploy.yml` |

Na VPS, nada é editado à mão: o diretório é sempre reescrito pelo rsync do deploy.

**Endereço público**: o edge-proxy (repo `edge-proxy`) precisa de um `conf/sites/promessas.caddy`. Com domínio:

```caddy
meudominio.com.br {
	encode zstd gzip
	reverse_proxy promessas:80
}
```

## Configurar

**Settings → Secrets and variables → Actions → New repository secret.** Os quatro da VPS são os mesmos do Gamedle e do edge-proxy:

| Secret | Valor |
|---|---|
| `VPS_HOST` | `187.77.254.161` |
| `VPS_USER` | `root` |
| `VPS_SSH_KEY` | conteúdo inteiro da chave privada de deploy (a mesma do Gamedle) |
| `VPS_PORT` | opcional (padrão 22) |

Chaves de IA, todas opcionais (quem não tiver chave é pulado):

| Secret | Onde conseguir (plano gratuito) |
|---|---|
| *(nenhum)* | GitHub Models já funciona com o `GITHUB_TOKEN` do Actions |
| `GEMINI_API_KEY` | aistudio.google.com |
| `GROQ_API_KEY` | console.groq.com |
| `MISTRAL_API_KEY` | console.mistral.ai (plano Experiment) |
| `CLOUDFLARE_ACCOUNT_ID` + `CLOUDFLARE_API_TOKEN` | dash.cloudflare.com → Workers AI |
| `OPENROUTER_API_KEY` | openrouter.ai (modelos `:free`) |
| `ANTHROPIC_API_KEY` | só quando ligar o Haiku (pago) |

Depois, rode uma vez **Actions → Sync diário → Run workflow**.

### Ligar o Claude Haiku no fim da cadeia

Em `config/provedores.yaml`, no bloco `claude-haiku`, troque `ativo: false` por `ativo: true` e cadastre o secret `ANTHROPIC_API_KEY`. Ele continua sendo o último da fila, então só é chamado quando todos os gratuitos esgotarem.

### Adicionar outro provedor

Qualquer API compatível com `/chat/completions` entra só pela configuração: basta acrescentar um bloco `tipo: openai` com `base_url`, `modelo` e `chave_env` na posição desejada da lista.

## Rodar localmente

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m motor.main --sem-ia --dry-run      # só coleta e filtra
.venv/bin/python -m motor.main                         # execução completa
.venv/bin/python -m motor.revisar                      # fila de revisão
.venv/bin/python -m motor.revisar aprovar <id>
.venv/bin/python -m motor.revisar aprovar <id> --status parcial --nota "6 de 10 ministérios"
.venv/bin/python -m motor.revisar rejeitar <id>
.venv/bin/python web/build.py                          # gera dist/
.venv/bin/pytest -q
```

## Fontes e limitações

- **Legislação federal**: leis, LCs, decretos, MPs e emendas vêm da API de dados abertos do Senado (estável, acessível de qualquer lugar). A indexação atrasa alguns dias, por isso a consulta volta 15 dias.
- **DOU (desligado)**: o `in.gov.br` recusa IP estrangeiro e, mesmo pela VPS em São Paulo, o CDN dele devolveu 502 na maioria das tentativas (testado em 25/10/2026). O sync já abre um túnel SOCKS pela VPS (`DOU_PROXY`); para usar, ligue `dou.ativo` em `config/coleta.yaml`. Alternativa mais estável: INLABS (cadastro gratuito).
- **Falha de uma fonte** não perde dados: cada coletor guarda a última data coletada com sucesso (`data/estado/ultima_execucao.json`) e recoleta o período perdido na próxima execução.
- **Plano de governo**: o PDF em `fontes/` veio de um espelho (planodegoverno2026.com.br), porque o TSE também bloqueia IPs de fora. Antes de publicar, baixe do TSE pelo link em `config/presidente.yaml` e confira o `sha256`.
- **Antes da posse** (até 31/12/2026), nada é aplicado automaticamente: tudo que a IA achar vai para a fila.
- `config/presidente.yaml` → `resultado_confirmado: false` mostra um aviso no topo do site. Mude para `true` quando o TSE proclamar o resultado.

## Estrutura

```
config/        provedores.yaml (cadeia de IA), coleta.yaml (fontes e regras), presidente.yaml
data/          promessas.json, fila_revisao.json, documentos/AAAA-MM-DD.json, estado/
fontes/        PDF do plano, texto extraído e o script que gera promessas.json
motor/         coletores/, llm/ (cadeia + provedores), filtro, classificador, regras, main, revisar
web/           build.py + templates + CSS do site estático
deploy/        compose do nginx na VPS + override da rede edge + config do nginx
.github/       workflows deploy.yml e sync.yml, action ssh-vps
tests/
```
