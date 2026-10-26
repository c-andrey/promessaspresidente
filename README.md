# Cumpriu?

Acompanhamento diário das promessas do presidente eleito em 2026, **Lula (PT)**, com um documento oficial como prova de cada mudança de status.

- **Promessas**, com duas origens sempre separadas:
  - **Plano oficial (TSE)**: 132 compromissos para o mandato 2027-2030 tirados do plano registrado no TSE (só o que é compromisso futuro; o balanço do mandato atual ficou de fora), com página e trecho literal conferidos no PDF.
  - **Declarações de campanha**: promessas feitas em entrevistas, debates, sabatinas e discursos que não estão no plano. Só entram depois de aprovação humana.
  - Cada promessa do plano também lista reportagens em que o candidato falou dela ("Na campanha").
- **Tudo em docker compose** na VPS (`/srv/promessaspresidente`): o serviço `sync` roda o pipeline todo dia às 07:00 (coleta → filtro → IA com fallback → regras) e gera o site; o `web` (nginx) serve o site. Público em **http://187.77.254.161:8081** via edge-proxy.
- **Auditoria**: cada promessa guarda o próprio histórico (data, documento oficial, trecho, quem decidiu), publicado no site. Os dados brutos ficam só no volume da VPS (uso interno). O volume de dados tem backup diário no GitHub (`backup.yml`).

## Trocar de presidente

1. Crie `fontes/<nome>/` com `plano.pdf`, `plano.txt` (texto extraído) e `promessas.py` (lista `P` e `URL_TSE`).
2. Em `config/presidente.yaml`, mude `nome`, `nome_busca`, `partido`, as datas e `plano_de_governo.pasta`.
   Reeleito? `acompanhar_desde` = dia seguinte à eleição (atos do governo dele já contam); senão, a data da posse.
3. Arquive `data/referencias_campanha.json`, `promessas_candidatas.json`, `promessas_declaracoes.json` e
   `declaracoes_rejeitadas.json` na pasta do anterior e rode `fontes/gerar_promessas.py`.
4. Push. Na VPS o `sync` percebe o nome novo, move os dados do anterior para `arquivo/` dentro do volume e recomeça.

## Promessas: plano oficial × declarações (automático, todo dia)

Além dos atos oficiais, o sync diário (`motor/campanha.py`, configurado em `config/campanha.yaml`) pesquisa o que o presidente **disse** que ia fazer:

| Frente | Todo dia | Resultado |
|---|---|---|
| **Declarações** | buscas no Google Notícias dos últimos 2 dias ("promete", "se reeleito", debate, entrevista… + ~38 temas) → filtro de manchetes com cara de promessa (sem IA) → IA extrai só o que ele mesmo disse → consolida repetidas | o que repete o plano vira referência; o resto vira candidata. **Confirmada por 3+ veículos diferentes → publicada sozinha**; as outras esperam revisão |
| **Referências** | rodízio de 30 promessas/dia: busca falas novas dele sobre cada uma desde a última verificação | seção "Na campanha" de cada promessa |

A campanha tem teto próprio de chamadas de IA por dia (`max_chamadas_ia_por_dia`): o acompanhamento dos atos oficiais roda antes e tem prioridade na cota.

```bash
# na VPS (comandos, não edição de arquivos)
docker compose exec sync python -m motor.campanha listar            # candidatas pendentes
docker compose exec sync python -m motor.campanha aprovar ID [ID…]
docker compose exec sync python -m motor.campanha rejeitar ID [ID…]
docker compose exec sync python -m motor.campanha buscar --amplo    # pesquisa grande (450 buscas: temas × veículos × janelas)
docker compose exec sync python -m motor.campanha referencias --todas
```

Rodando localmente (sem `DADOS_DIR`), os mesmos comandos gravam em `data/` do repositório, que vira **semente**: no deploy, o `sync` mescla esses arquivos ao volume sem apagar nada (decisões tomadas no volume prevalecem). `python fontes/gerar_promessas.py` monta `data/promessas.json` com o plano do TSE (trechos conferidos no PDF) + declarações aprovadas + referências.

## Como funciona

```
coletores (legislação federal via Senado, Câmara, Planalto; DOU opcional)
        │  ~dezenas de documentos/dia
        ▼
filtro por palavra-chave (sem IA, custo zero)
        │  só documentos com promessa candidata
        ▼
cadeia de IA: groq (3 modelos) → gemini (lite, flash) → nvidia → mistral → cloudflare → openrouter → [ollama] → [claude-haiku]
        │  JSON validado contra schema
        ▼
regras: nao_iniciada → em_andamento (oficial, confiança ≥ 0.85, pós-posse) = automático
        todo o resto (parcial, cumprida, abandonada) = fila de revisão humana
```

Quando um provedor devolve 429/402 ou passa do `limite_diario`, ele fica marcado como esgotado no dia (`estado/uso_llm.json`) e a cadeia segue para o próximo. Se todos esgotarem, os documentos que sobraram ficam em `estado/pendentes.json` e são processados no dia seguinte.

## Deploy (docker compose + GitHub Actions)

```
compose.yaml
  sync  (build .)        motor/agendador.py: semeia promessas, gera site, sync diário em HORA_SYNC
    └─ volumes: dados (/dados: promessas, histórico, fila, estado) · site (/site: HTML gerado)
  web   (nginx)          serve o volume site em 127.0.0.1:3200
compose.proxy.yaml       liga o web à rede "edge" com alias "promessas" (o deploy liga sozinho)
```

| Workflow | Quando | O que faz |
|---|---|---|
| `deploy.yml` | push na `main` ou manual | testes + smoke da stack no runner → rsync do repo para `/srv/promessaspresidente` → `.env` a partir dos secrets → `docker compose up -d --build` → health check |
| `backup.yml` | todo dia 09:30 (Brasília) | tar do volume `promessaspresidente_dados` → artifact por 90 dias |

Na VPS, nada é editado à mão: o código é sempre reescrito pelo rsync do deploy. Os dados ficam no volume, que o deploy não toca. Quando o repositório ganha ou corrige promessas (`data/promessas.json`), o `sync` copia as novas e atualiza os textos ao subir, preservando status e histórico.

**Endereço**: `http://187.77.254.161:8081`, pelo edge-proxy (`conf/sites/promessas.caddy`). Com domínio, troque lá `http://:8081` por `dominio.com.br` e o Caddy emite o HTTPS.

**Operação na VPS** (comandos, não edição de arquivos):

```bash
cd /srv/promessaspresidente
docker compose logs -f sync
docker compose exec sync python -m motor.agendador --agora     # sync na hora
docker compose exec sync python -m motor.revisar               # fila de revisão
docker compose exec sync python -m motor.revisar aprovar <id>
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
| `NVIDIA_API_KEY` | build.nvidia.com → conta de desenvolvedor → Get API Key (`nvapi-…`) |
| `GEMINI_API_KEY` | aistudio.google.com |
| `GROQ_API_KEY` | console.groq.com |
| `MISTRAL_API_KEY` | console.mistral.ai (plano Experiment) |
| `CLOUDFLARE_ACCOUNT_ID` + `CLOUDFLARE_API_TOKEN` | dash.cloudflare.com → Workers AI |
| `OPENROUTER_API_KEY` | openrouter.ai (modelos `:free`) |
| `ANTHROPIC_API_KEY` | só quando ligar o Haiku (pago) |

Depois, rode **Actions → Deploy → Run workflow** (ou faça um push). Para mudar o horário do sync, crie a Variable `HORA_SYNC` (ex.: `06:30`).

### Ligar o Claude Haiku no fim da cadeia

Em `config/provedores.yaml`, no bloco `claude-haiku`, troque `ativo: false` por `ativo: true`, cadastre o secret `ANTHROPIC_API_KEY` e faça push (o deploy reescreve o `.env`). Ele continua sendo o último da fila, então só é chamado quando todos os gratuitos esgotarem.

### Adicionar outro provedor

Qualquer API compatível com `/chat/completions` entra só pela configuração: basta acrescentar um bloco `tipo: openai` com `base_url`, `modelo` e `chave_env` na posição desejada da lista.

## Rodar localmente

```bash
docker compose up -d --build                          # stack completa em http://127.0.0.1:3200
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
- **DOU (desligado)**: o `in.gov.br` recusa IP estrangeiro e, mesmo da VPS em São Paulo, o CDN dele devolveu 502 na maioria das tentativas (testado em 25/10/2026). Como o `sync` já roda no Brasil, basta ligar `dou.ativo` em `config/coleta.yaml` para testar de novo (`DOU_PROXY` só é preciso fora do Brasil). Alternativa mais estável: INLABS (cadastro gratuito).
- **Falha de uma fonte** não perde dados: cada coletor guarda a última data coletada com sucesso (`estado/ultima_execucao.json` no volume de dados) e recoleta o período perdido na próxima execução.
- **Plano de governo**: o PDF em `fontes/` veio de um espelho (planodegoverno2026.com.br), porque o TSE também bloqueia IPs de fora. Antes de publicar, baixe do TSE pelo link em `config/presidente.yaml` e confira o `sha256`.
- **Antes da posse** (até 31/12/2026), nada é aplicado automaticamente: tudo que a IA achar vai para a fila.
- `config/presidente.yaml` → `resultado_confirmado: false` mostra um aviso no topo do site. Mude para `true` quando o TSE proclamar o resultado.

## Estrutura

```
config/        provedores.yaml (cadeia de IA), coleta.yaml (fontes e regras), presidente.yaml
data/          promessas.json (semente; em produção o estado vive no volume "dados"), referências, declarações
fontes/        gerar_promessas.py (plano do TSE → data/promessas.json)
  lula/        plano.pdf, plano.txt e promessas.py do presidente atual
  flavio-bolsonaro/  arquivo: o site acompanhou o Flávio antes do resultado do 2º turno
motor/         coletores/, llm/ (cadeia + provedores), filtro, classificador, regras, main, revisar, campanha, agendador
web/           build.py + templates + CSS do site estático
deploy/nginx/  config do nginx
compose.yaml   serviços sync + web · compose.proxy.yaml: rede edge · Dockerfile
.github/       workflows deploy.yml e backup.yml, action ssh-vps
tests/
```
