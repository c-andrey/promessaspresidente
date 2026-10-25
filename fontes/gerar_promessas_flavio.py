"""Gera data/promessas.json a partir do plano de governo registrado no TSE.

Cada promessa carrega a página e um trecho literal do PDF. Ao rodar, o script
confere se todo trecho existe de fato no texto extraído e aborta se não existir.

Uso: python fontes/gerar_promessas_flavio.py
"""
import json
import re
import sys
import unicodedata
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
TEXTO = RAIZ / "fontes" / "plano-flavio-bolsonaro-2026.txt"
SAIDA = RAIZ / "data" / "promessas.json"
# Reportagens da campanha confirmadas por fontes/buscar_referencias.py (opcional)
REFERENCIAS = RAIZ / "data" / "referencias_campanha.json"
URL_TSE = "https://divulgacandcontas.tse.jus.br/divulga/rest/arquivo/doc/280017104726"

# (id, categoria, título, página, trecho literal, prazo, palavras-chave)
P = [
    # --- Segurança (Brasil sem Medo) ---
    ("faccoes-narcoterroristas", "Segurança", "Declarar PCC, CV e milícias organizações narcoterroristas", 13,
     "PCC, CV, milícias e todas as outras facções serão declaradas como organizações narcoterroristas.", None,
     ["narcoterror", "organização terrorista", "organizações terroristas", "facções criminosas", "facção criminosa", "lei antiterrorismo"]),
    ("maioridade-penal-16", "Segurança", "Reduzir a maioridade penal de 18 para 16 anos", 13,
     "vai apoiar e sancionar a redução da maioridade penal de 18 para 16 anos", None,
     ["maioridade penal", "imputabilidade penal", "inimputabilidade"]),
    ("sistema-nacional-fronteira", "Segurança", "Criar o Sistema Nacional de Fronteira com tropa de elite das Forças Armadas", 13,
     "Vamos criar o Sistema Nacional de Fronteira, uma tropa de elite do Exército, da Marinha e", None,
     ["sistema nacional de fronteira", "faixa de fronteira", "fronteiras", "controle de fronteiras"]),
    ("cinco-presidios-treva", "Segurança", "Construir 5 presídios federais de segurança máxima (complexo TREVA)", 14,
     "O Brasil terá 5 novos presídios de segurança máxima no modelo adotado por El Salvador.", None,
     ["presídio federal", "presídios federais", "penitenciária federal", "segurança máxima", "sistema penitenciário federal"]),
    ("meio-milhao-vagas-prisionais", "Segurança", "Criar meio milhão de vagas prisionais em 4 anos e zerar o déficit carcerário", 14,
     "vamos criar meio milhão de novas vagas no sistema prisional em 4 anos e zerar o déficit carcerário", "2030-12-31",
     ["vagas prisionais", "déficit carcerário", "sistema prisional", "funpen", "fundo penitenciário"]),
    ("castracao-quimica", "Segurança", "Aprovar castração química para estupradores e abusadores de crianças", 14,
     "aprovar e implementar a castração química de criminosos que abusam de mulheres e crianças", None,
     ["castração química"]),
    ("tornozeleira-agressores", "Segurança", "Monitorar com tornozeleira agressores de mulheres com medida protetiva", 14,
     "serão monitorados pelas autoridades com o uso de tornozeleiras eletrônicas", None,
     ["tornozeleira", "monitoramento eletrônico", "medida protetiva", "medidas protetivas"]),
    ("feminicidio-pena-integral", "Segurança", "Obrigar assassinos e agressores de mulheres a cumprir pena integral", 14,
     "obrigar assassinos e agressores de mulheres a cumprir integralmente suas penas", None,
     ["feminicídio", "violência contra a mulher", "maria da penha", "cumprimento integral"]),
    ("portos-aeroportos-tropas", "Segurança", "Ocupar portos e aeroportos com tropas da Marinha e Aeronáutica", 15,
     "vamos usar tropas especiais da Marinha e da Aeronáutica para ocupar e monitorar, de forma permanente, os portos e aeroportos brasileiros.", None,
     ["garantia da lei e da ordem", "glo", "portos e aeroportos", "porto de santos", "tráfico internacional"]),
    ("dobrar-investimento-seguranca", "Segurança", "Dobrar o investimento federal em segurança pública ao longo do mandato", 15,
     "vai dobrar os investimentos federais em segurança pública ao longo do mandato", "2030-12-31",
     ["fundo nacional de segurança pública", "fnsp", "orçamento da segurança pública", "investimentos em segurança pública"]),
    ("muralha-brasileira", "Segurança", "Implantar a Muralha Brasileira: reconhecimento facial e 1 milhão de câmeras", 15,
     "Vamos implantar o Muralha Brasileira - um sistema nacional de reconhecimento facial integrado a bancos de dados criminais", None,
     ["reconhecimento facial", "muralha brasileira", "videomonitoramento", "câmeras de segurança"]),
    ("auxilio-familias-vitimas", "Segurança", "Redirecionar recursos de famílias de presos para famílias de vítimas", 15,
     "Os recursos hoje direcionados às famílias de detentos serão redirecionados às famílias das vítimas", None,
     ["auxílio-reclusão", "auxílio reclusão", "vítimas de crimes", "famílias das vítimas"]),
    ("fim-progressao-hediondos", "Segurança", "Acabar com a progressão de regime para crimes hediondos", 15,
     "vai usar toda a sua força para acabar com a progressão de regime de quem comete crimes hediondos", None,
     ["progressão de regime", "crimes hediondos", "crime hediondo", "lei de execução penal"]),
    ("celular-pena-quadruplicada", "Segurança", "Quadruplicar a pena inicial de furto e receptação de celular", 16,
     "Quem furta ou revende um aparelho fruto de crime vai ter a pena inicial quadruplicada", None,
     ["roubo de celular", "furto de celular", "receptação", "aparelhos celulares", "celulares roubados"]),

    # --- Mulheres (Brasil por Elas) ---
    ("central-da-mulher", "Mulheres", "Abrir Centrais da Mulher em todo o Brasil, com unidades itinerantes", 17,
     "Vamos abrir essas centrais em todo o Brasil, em parceria com estados e municípios", None,
     ["central da mulher", "casa da mulher brasileira", "atendimento à mulher"]),
    ("app-claria", "Mulheres", "Lançar o aplicativo Central da Mulher com a assistente virtual ClarIA", 18,
     "A ClarIA estará ao lado da mulher em cada passo.", None,
     ["claria", "aplicativo central da mulher", "assistente virtual"]),
    ("bonus-internet", "Mulheres", "Garantir pacotes de internet gratuitos, com prioridade para mulheres", 24,
     "Vamos garantir pacotes de conexão para que nenhum brasileiro deixe de estudar", None,
     ["pacotes de internet", "pacotes de conexão", "bônus de internet", "inclusão digital", "wi-fi brasil"]),
    ("denuncia-integrada", "Mulheres", "Integrar Ligue 180 e canais de denúncia com avaliação nacional de risco", 18,
     "reunindo o Ligue 180, as delegacias online, o botão de", None,
     ["ligue 180", "avaliação de risco", "canais de denúncia", "violência doméstica"]),
    ("ganha-ganha", "Mulheres", "Criar o Ganha, Ganha: pontos por boas atitudes viram juros menores e cashback", 19,
     "Com o Ganha, Ganha, atitudes como concluir cursos de capacitação, trabalhar de forma regular, poupar e pagar as contas em dia passarão a gerar pontuação positiva.", None,
     ["ganha, ganha", "ganha-ganha", "ganha ganha", "caixa pra elas", "cashback"]),
    ("escola-brasil-por-elas", "Mulheres", "Criar a Escola Brasil por Elas, programa gratuito de capacitação feminina", 20,
     "A Escola Brasil por Elas será o maior programa gratuito de capacitação feminina da América Latina", None,
     ["escola brasil por elas", "brasil por elas", "capacitação feminina", "qualificação de mulheres"]),
    ("voucher-creche", "Mulheres", "Voucher-creche na rede privada onde faltar vaga pública", 21,
     "a família receberá um voucher-creche para acesso à rede privada credenciada até a vaga pública surgir", None,
     ["voucher-creche", "voucher creche", "vagas em creche", "educação infantil"]),
    ("rede-nacional-cuidado", "Mulheres", "Estruturar a Rede Nacional de Cuidado (centros-dia e atendimento domiciliar)", 21,
     "Também estruturaremos uma Rede Nacional de Cuidado, ampliando o apoio a idosos e pessoas com deficiência", None,
     ["rede nacional de cuidado", "política nacional de cuidados", "centros-dia", "centro-dia"]),
    ("unidades-moveis-saude-mulher", "Mulheres", "Unidades Móveis de Saúde da Mulher (ônibus, caminhões e barcos)", 21,
     "E teremos Unidades Móveis de Saúde da Mulher, ônibus ou caminhões adaptados que percorrerão o país", None,
     ["unidades móveis", "saúde da mulher", "carretas da mulher"]),

    # --- Serviços e Estado digital (Brasil sem Fila) ---
    ("servicos-100-digitais", "Serviços e Estado digital", "Digitalizar 100% dos serviços federais e criar identidade digital única", 25,
     "Vamos digitalizar 100% dos serviços públicos do Governo Federal passíveis de digitalização", None,
     ["governo digital", "gov.br", "serviços digitais", "transformação digital", "identidade digital"]),
    ("prontuario-eletronico-unico", "Serviços e Estado digital", "Prontuário eletrônico único vinculado ao CPF, público e privado", 26,
     "Vamos implantar o prontuário eletrônico único, vinculado ao CPF e integrado ao Gov.br, interoperável entre as redes pública e privada", None,
     ["prontuário eletrônico", "rede nacional de dados em saúde", "rnds", "conecte sus", "saúde digital"]),
    ("ia-agendamento-sus", "Serviços e Estado digital", "Usar IA para agendar consultas e exames do SUS na primeira vaga", 26,
     "usar inteligência artificial para agilizar o agendamento de consultas e exames", None,
     ["agendamento de consultas", "fila do sus", "regulação de consultas", "filas de espera"]),
    ("inss-sem-fila", "Serviços e Estado digital", "Zerar a fila do INSS com digitalização e gestão profissional", 26,
     "Vamos investir em tecnologia e digitalizar os processos do INSS, dando continuidade ao caminho do Meu INSS, e profissionalizar a gestão do órgão.", None,
     ["fila do inss", "meu inss", "requerimentos do inss", "análise de benefícios", "inss"]),
    ("liberdade-economica-nacional", "Serviços e Estado digital", "Levar a Lei de Liberdade Econômica a todos estados e municípios", 27,
     "Vamos fazer o bom exemplo da liberdade econômica alcançar todo o país, em cada estado e município", None,
     ["liberdade econômica", "abertura de empresas", "redesim", "baixo risco"]),
    ("revogaco-regulatorio", "Serviços e Estado digital", "Promover um amplo revogaço regulatório", 27,
     "Vamos promover um amplo revogaço regulatório, revisando normas obsoletas e racionalizando procedimentos", None,
     ["revogaço", "revogação de normas", "simplificação regulatória", "desburocratização", "revisão normativa"]),
    ("rastreabilidade-cadeias", "Agronegócio", "Rastreabilidade completa das cadeias produtivas num só sistema", 28,
     "Vamos instituir a rastreabilidade completa das cadeias produtivas", None,
     ["rastreabilidade", "guia de trânsito animal", "cadastro ambiental rural", "identificação animal"]),

    # --- Custo de vida e impostos (Brasil mais Barato) ---
    ("revisar-reforma-tributaria", "Custo de vida e impostos", "Revisar a reforma tributária e reduzir a alíquota do IVA", 30,
     "Vamos promover a revisão e o redimensionamento da reforma tributária em curso", None,
     ["reforma tributária", "ibs", "cbs", "alíquota de referência", "imposto seletivo", "split payment", "lei complementar nº 214"]),
    ("reduzir-cde", "Custo de vida e impostos", "Simplificar a conta de luz e reduzir gradualmente a CDE", 31,
     "promover a redução gradual da CDE e das fontes incentivadas, mantida a tarifa social para quem mais precisa dela", None,
     ["conta de desenvolvimento energético", "cde", "encargos setoriais", "tarifa de energia", "subsídios cruzados", "tarifa social"]),
    ("reduzir-impostos-energia-combustiveis", "Custo de vida e impostos", "Reduzir impostos sobre energia elétrica e combustíveis", 31,
     "Vamos reduzir impostos sobre energia elétrica e combustíveis", None,
     ["combustíveis", "pis/cofins", "cide", "gasolina", "óleo diesel", "energia elétrica"]),
    ("proibir-bets-programas-sociais", "Custo de vida e impostos", "Proibir uso de dinheiro de programas sociais em apostas online", 33,
     "Vamos proibir o uso dos recursos dos programas sociais para apostas", None,
     ["apostas de quota fixa", "apostas online", "bets", "bets"]),

    # --- Educação (Brasil que Prepara) ---
    ("metodo-fonico", "Educação", "Priorizar o método fônico na alfabetização", 35,
     "Vamos priorizar o método fônico, que é o de melhor resultado comprovado pela ciência", None,
     ["método fônico", "alfabetização", "política nacional de alfabetização", "compromisso nacional criança alfabetizada"]),
    ("programa-acolher", "Educação", "Criar o Programa Acolher: aluno remunerado para dar reforço a colegas", 35,
     "vamos criar o Programa Acolher: um aluno com bom desempenho é remunerado para dar reforço aos colegas que precisam", None,
     ["programa acolher", "reforço escolar", "monitoria", "tutoria entre alunos"]),
    ("repasses-por-resultado", "Educação", "Vincular repasses da educação a metas de aprendizagem", 35,
     "o financiamento seguirá o orçamento por resultados, com repasses vinculados a metas de aprendizagem", None,
     ["metas de aprendizagem", "fundeb", "vaar", "financiamento por resultados"]),
    ("escolas-civico-militares", "Educação", "Ampliar as escolas cívico-militares", 35,
     "Vamos ampliar as Escolas Cívico-Militares", None,
     ["cívico-militar", "cívico-militares", "civico militar", "pecim"]),
    ("altas-habilidades", "Educação", "Tirar do papel a política nacional para alunos com altas habilidades", 35,
     "a política nacional que trata do tema é lei mas segue sem implementação, e nós vamos tirá-la do papel", None,
     ["altas habilidades", "superdotação"]),
    ("escola-nas-ferias", "Educação", "Manter escolas abertas nas férias com o Escola nas Férias", 35,
     "Manteremos as escolas abertas nas férias com o Escola", None,
     ["escola nas férias", "férias escolares"]),
    ("voucher-educacional", "Educação", "Voucher educacional onde faltar vaga na rede pública", 36,
     "onde faltar vaga na rede pública, a família receberá um voucher educacional para matricular o filho em outra escola", None,
     ["voucher educacional", "vale-educação", "vale educação", "bolsas em escolas privadas"]),
    ("ensino-tecnico-medio", "Educação", "Ampliar o ensino técnico no ensino médio com o setor produtivo", 36,
     "Vamos ampliar a oferta de educação técnica para os alunos do ensino médio", None,
     ["ensino técnico", "educação profissional", "educação técnica", "ensino médio técnico", "itinerário formativo"]),
    ("politica-formacao-talentos", "Educação", "Criar a Política Nacional de Formação de Talentos", 36,
     "criar a Política Nacional de Formação de Talentos", None,
     ["formação de talentos"]),
    ("ensino-superior-mcti", "Educação", "Transferir o ensino superior do MEC para o Ministério da Ciência e Tecnologia", 36,
     "No ensino superior, vamos transferir a governança para o Ministério da Ciência, Tecnologia e Inovações", None,
     ["ensino superior", "ministério da ciência, tecnologia e inovação", "capes", "cnpq", "secretaria de educação superior"]),
    ("emprestimo-contingente-renda", "Educação", "Trocar o Fies pelo Empréstimo Contingente à Renda", 37,
     "vamos adotar o Empréstimo Contingente à Renda: o estudante só começa a pagar quando estiver empregado e ganhando", None,
     ["fies", "financiamento estudantil", "empréstimo contingente"]),

    # --- Saúde e cuidado ---
    ("corrigir-tabela-sus", "Saúde", "Garantir correção efetiva da tabela SUS", 37,
     "Vamos garantir as condições para que seja possível a correção efetiva da tabela SUS.", None,
     ["tabela sus", "tabela do sus", "santas casas", "procedimentos do sus", "remuneração de procedimentos"]),
    ("programa-atendimento-idosos", "Saúde", "Criar o Programa de Atendimento aos Idosos", 37,
     "criar o Programa de Atendimento aos Idosos", None,
     ["programa de atendimento aos idosos", "saúde da pessoa idosa"]),
    ("rede-privada-horarios-ociosos", "Saúde", "Contratar exames e serviços da rede privada em horários ociosos", 37,
     "vamos contratar serviços e exames na rede privada nos horários ociosos", None,
     ["rede privada", "horários ociosos", "agora tem especialistas", "contratação de serviços privados"]),
    ("hospitais-universitarios-ebserh", "Saúde", "Modernizar os hospitais universitários federais (EBSERH)", 38,
     "fortalecer a Rede Nacional de Hospitais Inteligentes (EBSERH), modernizando os hospitais universitários federais", None,
     ["ebserh", "hospitais universitários", "hospitais inteligentes"]),
    ("remedio-em-casa", "Saúde", "Entregar remédios em casa a idosos, pessoas com deficiência e doentes crônicos", 38,
     "um sistema de entrega de remédio em domicílio", None,
     ["entrega de medicamentos", "medicamentos em domicílio", "farmácia popular"]),
    ("casa-segura-envelhecer", "Saúde", "Criar o Casa Segura para Envelhecer e app de companhia para idosos", 39,
     "vamos oferecer o programa Casa Segura para Envelhecer, com subsídio à acessibilidade domiciliar para famílias de menor renda", None,
     ["casa segura para envelhecer", "acessibilidade domiciliar", "instituições de longa permanência"]),
    ("centros-tea", "Saúde", "Implantar Centros de Referência em Transtorno do Espectro Autista", 39,
     "a implantação de Centros de Referência em Transtorno do Espectro Autista", None,
     ["espectro autista", "autismo", "tea"]),
    ("escola-de-campeoes", "Esporte e cultura", "Criar o Programa Escola de Campeões (esporte competitivo nas escolas)", 40,
     "Vamos criar o Programa Escola de Campeões", None,
     ["escola de campeões", "esporte escolar", "jogos escolares"]),
    ("categoria-feminina-esporte", "Esporte e cultura", "Restringir a categoria feminina do esporte a atletas do sexo feminino", 41,
     "Vamos assegurar que a categoria feminina, da base ao alto rendimento, seja disputada por atletas do sexo feminino", None,
     ["categoria feminina", "atletas transgênero", "atletas trans", "sexo biológico"]),

    # --- Trabalho e renda (Brasil que Prospera) ---
    ("manter-programas-sociais", "Trabalho e renda", "Manter os programas sociais existentes", 42,
     "vamos manter os programas sociais existentes, com aperfeiçoamento da gestão, correção de distorções e combate às fraudes", None,
     ["bolsa família", "programas sociais", "cadastro único", "cadúnico", "benefício de prestação continuada"]),
    ("retorno-imediato-beneficio", "Trabalho e renda", "Retorno imediato e sem fila ao benefício após o seguro-desemprego", 43,
     "garantiremos o retorno imediato e sem fila ao benefício após o seguro-desemprego", None,
     ["regra de proteção", "retorno garantido", "seguro-desemprego", "bolsa família"]),
    ("reduzir-custo-trabalho", "Trabalho e renda", "Reduzir gradualmente o custo do trabalho formal", 43,
     "Vamos reduzir gradualmente o custo do trabalho, sem retirar direitos", None,
     ["desoneração da folha", "encargos trabalhistas", "contribuição previdenciária patronal", "custo do trabalho"]),
    ("contrato-primeiro-emprego", "Trabalho e renda", "Contrato de primeiro emprego (18 a 24 anos) com menor custo", 43,
     "vamos criar um contrato de", None,
     ["primeiro emprego", "contrato de trabalho para jovens", "jovem aprendiz", "carteira verde e amarela"]),
    ("contrato-50-mais", "Trabalho e renda", "Contrato mais atrativo para desempregados com 50 anos ou mais", 44,
     "vamos criar um contrato mais atrativo para a contratação de pessoas com 50 anos ou mais desempregadas há pelo menos 12 meses", None,
     ["50 anos ou mais", "trabalhadores mais velhos", "trabalhador maduro"]),
    ("negociado-sobre-legislado", "Trabalho e renda", "Defender o negociado sobre o legislado", 44,
     "Por isso defendemos o negociado sobre o legislado", None,
     ["negociado sobre o legislado", "acordo coletivo", "convenção coletiva", "jornada flexível"]),
    ("banco-nacional-vagas", "Trabalho e renda", "Banco nacional de vagas conectado ao RH das empresas", 44,
     "com um banco nacional de vagas conectado ao RH das empresas", None,
     ["banco nacional de vagas", "intermediação de mão de obra", "sine", "vagas de emprego"]),
    ("minha-primeira-empresa", "Trabalho e renda", "Criar o Minha Primeira Empresa (Sistema S + crédito da CAIXA)", 45,
     "vamos criar o Minha Primeira Empresa", None,
     ["minha primeira empresa", "sebrae", "sistema s", "microempreendedor", "empreendedorismo"]),
    ("casa-verde-amarela", "Moradia e saneamento", "Retomar o Casa Verde e Amarela com meta de 2,5 milhões de moradias", 47,
     "Vamos retomar o Casa Verde e Amarela, com meta de 2,5 milhões de residências e a menor taxa de juros possível no financiamento.", None,
     ["casa verde e amarela", "minha casa, minha vida", "minha casa minha vida", "unidades habitacionais", "habitação de interesse social"]),
    ("retomar-territorios-faccoes", "Segurança", "Retomar as áreas hoje sob domínio de facções", 47,
     "vamos retomar as áreas hoje sob domínio de facções", None,
     ["retomada de territórios", "domínio territorial", "comunidades dominadas"]),
    ("saneamento-2033", "Moradia e saneamento", "Universalizar água e esgoto até 2033 (Marco do Saneamento)", 47,
     "acelerar as concessões e parcerias para universalizar o acesso à água tratada e ao esgoto", "2033-12-31",
     ["marco legal do saneamento", "saneamento básico", "esgotamento sanitário", "água tratada", "universalização"]),
    ("nenhuma-escola-sem-agua-2030", "Moradia e saneamento", "Nenhuma escola sem água até 2030", 47,
     "nenhuma escola sem água até 2030", "2030-12-31",
     ["escolas sem água", "água nas escolas", "abastecimento de água nas escolas"]),
    ("lixao-zero", "Moradia e saneamento", "Acabar com os lixões a céu aberto (Lixão Zero)", 47,
     "a meta do Programa Lixão Zero, acabando com os lixões a", None,
     ["lixão zero", "lixões", "resíduos sólidos", "aterros sanitários"]),

    # --- Economia e infraestrutura (Brasil que Cresce) ---
    ("crescimento-4-porcento", "Economia e contas públicas", "Crescimento sustentado de 4% ao ano", 49,
     "Nossa meta é dobrar esse ritmo e alcançar um crescimento sustentado de 4% ao ano ao longo da próxima década", None,
     ["pib", "produto interno bruto", "crescimento econômico"]),
    ("licenca-por-decurso-prazo", "Infraestrutura", "Licença ambiental concedida se o órgão não decidir no prazo", 50,
     "Se não decidir nem se manifestar dentro desse prazo, a licença deve ser concedida", None,
     ["licenciamento ambiental", "licença ambiental", "aprovação tácita", "decurso de prazo", "lei geral do licenciamento"]),
    ("estabilidade-regras-20-anos", "Infraestrutura", "Estabilidade de regras de até 20 anos para grandes projetos", 50,
     "criaremos mecanismos de estabilidade das regras de até 20 anos", None,
     ["estabilidade regulatória", "segurança jurídica", "contratos de longo prazo"]),
    ("autonomia-terras-indigenas", "Meio ambiente", "Autonomia de indígenas e quilombolas para atividades produtivas em suas terras", 50,
     "Será conferida autonomia aos povos indígenas e quilombolas para decidir sobre atividades produtivas em suas terras", None,
     ["terras indígenas", "terra indígena", "quilombolas", "atividades produtivas", "mineração em terras indígenas"]),
    ("r900-bilhoes-infraestrutura", "Infraestrutura", "Investir R$ 900 bilhões em infraestrutura em quatro anos", 51,
     "Vamos investir R$ 900 bilhões em quatro anos em rodovias, hidrovias, portos, aeroportos e ferrovias.", "2030-12-31",
     ["concessões", "parcerias público-privadas", "ppp", "infraestrutura", "leilão", "bndes"]),
    ("fundo-imoveis-uniao", "Infraestrutura", "Fundo de infraestrutura lastreado em imóveis da União", 50,
     "fundo lastreado na securitização de ativos e imóveis da", None,
     ["imóveis da união", "securitização", "secretaria do patrimônio da união", "fundo de investimento"]),
    ("corredores-logisticos", "Infraestrutura", "Criar os Corredores Logísticos Inteligentes", 51,
     "Criaremos os Corredores Logísticos Inteligentes de integração nacional", None,
     ["corredores logísticos", "corredor logístico"]),
    ("ferrograo", "Infraestrutura", "Destravar a Ferrogrão", 51,
     "vamos destravar a Ferrogrão", None,
     ["ferrogrão", "ef-170"]),
    ("trem-do-nordeste", "Infraestrutura", "Trem do Nordeste: 1.400 km ligando sete capitais", 60,
     "Vamos viabilizar o Trem do Nordeste, 1.400 km de trilhos interligando Salvador, Aracaju, Maceió, Recife, João Pessoa, Natal e Fortaleza", None,
     ["trem do nordeste", "trem de passageiros", "transporte ferroviário de passageiros"]),
    ("transnordestina", "Infraestrutura", "Concluir a Transnordestina e estendê-la à Paraíba e ao RN", 51,
     "concluir a Transnordestina e ampliar o seu escopo para que a Paraíba e o Rio Grande do Norte sejam contemplados", None,
     ["transnordestina"]),
    ("hidrovia-tapajos", "Infraestrutura", "Destravar a Hidrovia do Tapajós", 51,
     "como a Hidrovia do Tapajós", None,
     ["hidrovia do tapajós", "tapajós", "hidrovias"]),
    ("obras-hidricas-nordeste", "Infraestrutura", "Concluir ramais da transposição e obras hídricas do Nordeste", 52,
     "Iremos concluir, iniciar, executar estruturas como o Ramal do Apodi, o Ramal do Salgado, a Adutora do Seridó", None,
     ["ramal do apodi", "ramal do salgado", "adutora do seridó", "canal do xingó", "ramal do piancó", "canal do sertão", "eixão das águas", "transposição do rio são francisco"]),
    ("fracking", "Energia", "Permitir a exploração de gás não convencional (fracking)", 52,
     "permitir a exploração do gás não convencional (fracking)", None,
     ["fracking", "gás não convencional", "fraturamento hidráulico"]),
    ("armazenamento-energia", "Energia", "Programa de armazenamento de energia com baterias de grande porte", 53,
     "implantar um programa de armazenamento de energia, com baterias", None,
     ["armazenamento de energia", "baterias", "leilão de reserva de capacidade"]),
    ("polo-data-centers", "Energia", "Transformar o país em polo global de data centers sustentáveis", 53,
     "transformar o país em polo global de data centers sustentáveis", None,
     ["data center", "data centers", "datacenter", "redata"]),
    ("renovabio-exterior", "Energia", "Ampliar o RenovaBio para vender créditos de carbono ao exterior", 53,
     "Vamos fortalecer e ampliar o Programa Renovabio para o exterior", None,
     ["renovabio", "cbios", "combustível do futuro", "biogás", "biometano"]),
    ("seguro-rural", "Agronegócio", "Ampliar o Seguro Rural", 54,
     "Vamos ampliar o Seguro Rural", None,
     ["seguro rural", "subvenção ao prêmio", "psr"]),
    ("armazenagem-safras", "Agronegócio", "Ampliar a capacidade de armazenamento das safras", 54,
     "Vamos ampliar a capacidade de armazenamento das safras", None,
     ["armazenagem", "armazéns", "silos", "capacidade estática"]),
    ("fertilizantes-nacionais", "Agronegócio", "Apoiar a produção nacional de fertilizantes (potássio e fosfato)", 54,
     "Vamos apoiar a produção nacional de fertilizantes, aproveitando as reservas de minerais que o país possui", None,
     ["fertilizantes", "potássio", "fosfato", "plano nacional de fertilizantes"]),
    ("titulacao-rural", "Agronegócio", "Retomar ritmo de titulação e regularização fundiária rural", 54,
     "Vamos retomar esse ritmo e promover ampla regularização e titulação do pequeno proprietário rural", None,
     ["titulação", "regularização fundiária", "títulos de propriedade", "incra"]),
    ("outorgas-irrigacao", "Agronegócio", "Simplificar outorgas e ampliar a área irrigada (Matopiba e semiárido)", 54,
     "Na irrigação, vamos ampliar significativamente a área irrigada do país", None,
     ["outorga", "irrigação", "matopiba", "baixio de irecê", "água de reuso"]),
    ("saf-2027", "Energia", "Implementar as metas de combustível sustentável de aviação a partir de 2027", 55,
     "Vamos implementar as metas do combustível sustentável de aviação previstas em lei a partir de 2027", "2027-12-31",
     ["combustível sustentável de aviação", "saf", "probioqav"]),
    ("estrategia-nacional-ia", "Tecnologia", "Estratégia Nacional de IA para micro, pequenas e médias empresas", 56,
     "A Estratégia Nacional de Inteligência Artificial vai difundir a IA para as micro, pequenas e médias empresas em larga escala", None,
     ["inteligência artificial", "estratégia nacional de inteligência artificial", "plano brasileiro de inteligência artificial"]),
    ("mercado-carbono", "Meio ambiente", "Consolidar o mercado regulado de carbono", 57,
     "vamos consolidar o mercado regulado de carbono com segurança jurídica", None,
     ["mercado regulado de carbono", "sbce", "créditos de carbono", "sistema brasileiro de comércio de emissões"]),
    ("desmatamento-ilegal-zero-2029", "Meio ambiente", "Zerar o desmatamento ilegal até 2029", 58,
     "Teremos a meta de zerar o desmatamento ilegal até 2029", "2029-12-31",
     ["desmatamento", "prodes", "deter", "queimadas", "crimes ambientais"]),
    ("eliminar-superposicao-ibama-funai-icmbio", "Meio ambiente", "Eliminar superposições entre Ibama, Funai e ICMBio", 59,
     "Vamos eliminar superposições entre Ibama, Funai e ICMBio", None,
     ["ibama", "icmbio", "funai"]),
    ("transparencia-ongs", "Meio ambiente", "Exigir transparência de ONGs com recursos internacionais", 59,
     "exigir transparência de financiadores e de projetos das ONGs que recebem recursos internacionais", None,
     ["organizações não governamentais", "ongs", "organizações da sociedade civil", "recursos estrangeiros"]),

    # --- Relações exteriores ---
    ("reaproximacao-eua-argentina-israel", "Relações exteriores", "Reverter o afastamento com Argentina, EUA e Israel", 62,
     "Nos últimos anos, as relações com países como Argentina, Estados Unidos e Israel foram levadas ao limite do rompimento.", None,
     ["israel", "estados unidos", "argentina", "embaixador", "acordo comercial"]),
    ("adesao-ocde", "Relações exteriores", "Retomar o cronograma de adesão à OCDE", 63,
     "O passo mais urgente é retomar o cronograma interrompido de adesão à OCDE", None,
     ["ocde", "organização para a cooperação e desenvolvimento econômico", "acessão"]),
    ("fim-iof-cambio", "Custo de vida e impostos", "Fim gradual do IOF sobre câmbio", 63,
     "incluindo o fim gradual do IOF sobre o câmbio", None,
     ["iof", "imposto sobre operações financeiras", "operações de câmbio"]),

    # --- Instituições (Brasil que Cumpre a Constituição) ---
    ("stf-fim-foro-originario", "Instituições", "Acabar com as competências criminais originárias do STF", 65,
     "Fim das competências criminais originárias do STF", None,
     ["foro privilegiado", "foro por prerrogativa", "competência originária", "competências criminais"]),
    ("limitar-decisoes-monocraticas", "Instituições", "Limitar decisões monocráticas do STF", 65,
     "Limitação das decisões monocráticas do STF", None,
     ["decisões monocráticas", "decisão monocrática"]),
    ("quarentena-ministros-stf", "Instituições", "Quarentena de 1 ano para ministros de Estado serem indicados ao STF", 65,
     "Quarentena de 1 ano para que Ministros de Estado possam ser indicados ao STF.", None,
     ["quarentena", "indicação ao supremo", "indicação ao stf"]),
    ("fim-reeleicao", "Instituições", "Acabar com a reeleição para Presidente da República", 66,
     "E propomos o fim da reeleição para o cargo de Presidente da República.", None,
     ["reeleição", "fim da reeleição", "mandato de cinco anos"]),
    ("tesouraco-censura", "Instituições", "Acabar com estruturas de \"Ministério da Verdade\" e revogar atos contra críticos", 66,
     "acabar com qualquer estrutura estatal que funcione como um “Ministério da Verdade\", encarregada de vigiar, rotular ou punir o que as pessoas dizem", None,
     ["desinformação", "procuradoria nacional da união de defesa da democracia", "liberdade de expressão", "censura", "regulação das redes"]),
    ("emendas-transparencia", "Instituições", "Mais transparência e rastreabilidade nas emendas parlamentares", 69,
     "daremos mais transparência, controle e rastreabilidade às emendas parlamentares", None,
     ["emendas parlamentares", "emendas pix", "transferências especiais", "emendas de bancada"]),

    # --- Gestão e contas públicas (Brasil que Não Volta Atrás) ---
    ("cortar-10-ministerios", "Economia e contas públicas", "Cortar no mínimo 10 ministérios", 69,
     "Isso inclui o corte de no mínimo 10 ministérios", None,
     ["ministérios", "organização básica da presidência", "organização dos órgãos da presidência", "extingue o ministério", "transforma o ministério"]),
    ("reduzir-cargos-comissionados", "Economia e contas públicas", "Reduzir cargos comissionados e despesas administrativas", 69,
     "a redução de cargos comissionados e de despesas administrativas", None,
     ["cargos em comissão", "cargos comissionados", "funções comissionadas", "funções de confiança"]),
    ("combater-supersalarios", "Economia e contas públicas", "Combater penduricalhos e supersalários", 69,
     "o combate aos penduricalhos e supersalários", None,
     ["supersalários", "penduricalhos", "teto remuneratório", "extrateto"]),
    ("reforma-administrativa", "Economia e contas públicas", "Dar continuidade à reforma administrativa", 69,
     "Daremos continuidade à reforma administrativa que já colocamos em discussão", None,
     ["reforma administrativa", "pec 32", "carreiras do serviço público", "estabilidade do servidor"]),
    ("fortalecer-lei-estatais", "Economia e contas públicas", "Fortalecer a Lei das Estatais e profissionalizar indicações", 70,
     "Tentaram enfraquecê-la; nós vamos fortalecê-la.", None,
     ["lei das estatais", "lei nº 13.303", "13.303", "conselho de administração"]),
    ("blindar-fundos-pensao", "Economia e contas públicas", "Blindar fundos de pensão das estatais de indicação política", 70,
     "Vamos blindar os fundos de pensão das estatais da indicação política", None,
     ["fundos de pensão", "previ", "petros", "funcef", "postalis"]),
    ("retomar-desestatizacao", "Economia e contas públicas", "Retomar o Programa Nacional de Desestatização, caso a caso", 70,
     "vamos retomar o Programa Nacional de Desestatização com critério", None,
     ["desestatização", "privatização", "programa nacional de desestatização", "conselho do programa de parcerias de investimentos"]),
    ("nova-regra-fiscal", "Economia e contas públicas", "Apresentar nova regra fiscal focada em reduzir a dívida", 71,
     "Apresentaremos uma reformulação nas atuais regras fiscais", None,
     ["regra fiscal", "arcabouço fiscal", "regime fiscal sustentável", "lei complementar nº 200", "limite de despesas"]),
    ("superavit-primario", "Economia e contas públicas", "Entregar superávits primários", 71,
     "entregando superávits primários", None,
     ["resultado primário", "superávit primário", "meta fiscal", "lei de diretrizes orçamentárias"]),
    ("pacote-antifraude-inss", "Previdência", "Reeditar pacote antifraude na Previdência (moldes da MP 871/2019)", 72,
     "Reeditaremos um pacote antifraude na Previdência, nos moldes do que fizemos em 2019", None,
     ["antifraude", "fraudes no inss", "descontos associativos", "revisão de benefícios"]),
    ("governanca-consignado-inss", "Previdência", "Mudar a governança do consignado do INSS com dupla checagem", 72,
     "Vamos mudar a governança do consignado para que a fraude não se repita", None,
     ["consignado", "crédito consignado", "empréstimo consignado"]),
]


def normalizar(t: str) -> str:
    t = unicodedata.normalize("NFKC", t)
    t = t.replace("\t", " ").replace(" ", " ")
    return re.sub(r"\s+", " ", t).strip()


def main():
    texto = normalizar(TEXTO.read_text(encoding="utf-8"))
    # O PDF tem quebras de página no meio de frases: tira os marcadores para a checagem.
    texto = re.sub(r"=== PAGINA \d+ === \d+ ", "", texto)
    texto = re.sub(r"=== PAGINA \d+ === ", "", texto)

    ids, faltando, promessas = set(), [], []
    for (pid, cat, titulo, pag, trecho, prazo, kws) in P:
        assert pid not in ids, f"id duplicado: {pid}"
        ids.add(pid)
        if normalizar(trecho) not in texto:
            faltando.append(pid)
        promessas.append({
            "id": pid,
            "titulo": titulo,
            "categoria": cat,
            "prazo": prazo,
            "fonte": {"tipo": "plano_de_governo_tse", "url": URL_TSE, "pagina": pag, "trecho": trecho},
            "palavras_chave": sorted(set(k.lower() for k in kws)),
            "status": "nao_iniciada",
            "atualizado_em": None,
            "referencias": [],
            "historico": [],
        })
    if faltando:
        print("Trechos não encontrados no PDF:", *faltando, sep="\n  ")
        sys.exit(1)

    # Preserva status/histórico já registrados se o arquivo existir.
    if SAIDA.exists():
        antigos = {p["id"]: p for p in json.loads(SAIDA.read_text(encoding="utf-8"))}
        for p in promessas:
            if p["id"] in antigos:
                for campo in ("status", "atualizado_em", "historico", "imprensa"):
                    if campo in antigos[p["id"]]:
                        p[campo] = antigos[p["id"]][campo]

    referencias = json.loads(REFERENCIAS.read_text(encoding="utf-8")) if REFERENCIAS.exists() else {}
    for p in promessas:
        p["referencias"] = referencias.get(p["id"], [])

    SAIDA.write_text(json.dumps(promessas, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{len(promessas)} promessas gravadas em {SAIDA.relative_to(RAIZ)} (todos os trechos conferidos)")


if __name__ == "__main__":
    main()
