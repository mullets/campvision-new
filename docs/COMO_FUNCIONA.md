# CAMP Vision 2 — como funciona, passo a passo

Guia completo do caminho de um lote: da digitalização na estação até a folha aparecer no painel. Vale para a **versão 2026-10-14-05**.

Os detalhes técnicos de cada arquivo e de cada chamada HTTP ficam no contrato com o painel: [contrato-painel.md](contrato-painel.md). A instalação e o uso no Mac ficam no [README](../README.md).

---

## Índice

1. [Em uma frase](#1-em-uma-frase)
2. [As peças](#2-as-peças)
3. [O caminho de um lote, passo a passo](#3-o-caminho-de-um-lote-passo-a-passo)
4. [Como o vigia decide cada coisa](#4-como-o-vigia-decide-cada-coisa)
5. [O que fica no acervo](#5-o-que-fica-no-acervo)
6. [O que vai para o painel, e quando](#6-o-que-vai-para-o-painel-e-quando)
7. [O servidor: serviço, log, atualização e estado](#7-o-servidor-serviço-log-atualização-e-estado)
8. [Comandos](#8-comandos)
9. [Problemas conhecidos e como resolver](#9-problemas-conhecidos-e-como-resolver)
10. [Pendências abertas](#10-pendências-abertas)
11. [Onde mexer no código](#11-onde-mexer-no-código)

---

## 1. Em uma frase

As estações de digitalização deixam os scans em `100 - Scanners` no QNAP. O CAMP Vision 2, que roda no servidor `campvision2`, pega cada lote quando a estação termina e copia os arquivos com o nome CAMP para `ACERVOS_CAMP`. Depois lê cada imagem com o modelo de visão, grava os metadados dentro dos arquivos, escreve a catalogação e avisa o painel, que importa as folhas para revisão.

```
 Estações                 QNAP                       Servidor .40               QNAP                    Painel .60        Site
 (Mac VueScan,      ┌──────────────────┐        ┌──────────────────┐     ┌──────────────────┐     ┌────────────┐
  Windows Contex,──►│ 100 - Scanners   │──────► │ CAMP Vision 2    │───► │ ACERVOS_CAMP     │───► │ revisão    │──► Tainacan
  Universal)        │ (entrada bruta)  │        │ (vigia)          │     │ (acervo final)   │     │ e publica  │
                    └──────────────────┘        └───────┬──────────┘     └──────────────────┘     └─────▲──────┘
                                                        │  contexto do fundo, reservar código,          │
                                                        └──────────── heartbeat, aviso ─────────────────┘
```

**Regra de ouro:** os arquivos no QNAP são a fonte da verdade. O HTTP com o painel só acelera e informa. O painel só **lê** o `ACERVOS_CAMP`: nunca lê o `100 - Scanners` e nunca escreve em pasta de projeto.

---

## 2. As peças

| Peça | Onde | O que faz |
|---|---|---|
| **Estação Mac** (VueScan) | `camp-sistema-digitalizacao-mac` | Digitaliza fotos, negativos e slides. O operador escolhe o fundo e o projeto (novo ou já existente, pela lista do painel). No fim (F10) grava `info_lote_*_mac.json` e `info_projeto.json`; no backup (F12) copia para o QNAP e grava `status_mac.json` com `etapa: enviado_mac` por último. |
| **Estações Windows** (Contex, Universal) | outro sistema | Digitalizam pranchas e documentos. Marcam o fim com `manifesto.json` ou `status.json` com `status: enviado_windows`. |
| **`100 - Scanners`** | QNAP | Entrada bruta. As estações escrevem; só o vigia lê. |
| **CAMP Vision 2 (vigia)** | servidor `campvision2` (192.168.15.40), serviço `campvision2`, repositório `campvision-new` | Recebe, renomeia, lê, grava metadados, cataloga e avisa o painel. |
| **`ACERVOS_CAMP`** | QNAP, montado em `/mnt/camp/backup/Fundos e Escritorios/ACERVOS_CAMP` | Acervo final, organizado por fundo e projeto. |
| **Painel** | `camp-painel` (192.168.15.60:8000) | Dá os códigos de projeto (`/reservar`), recebe os avisos, importa as folhas para revisão e publica no site. |
| **API Anthropic** | nuvem | O modelo que lê cada imagem. A chave fica em `~/.campvision2/ambiente`. |

---

## 3. O caminho de um lote, passo a passo

Tudo acontece em `nucleo/entrada.py` (`Recebedor.processar`). A cada **30 s** o vigia faz uma rodada.

### Passo 1 — Achar os lotes

O vigia percorre `100 - Scanners` até 6 níveis de profundidade. Um **lote** (chamado de "unidade" no código) é a primeira pasta que:

- tem arquivo direto dentro dela; ou
- tem um marcador (`manifesto.json`, `info_projeto.json`, `status.json`, `status_mac.json`); ou
- só tem subpastas de formato (`TIF/`, `JPG/`, `DNG/`, `PDF/`…).

Ele **pula** pastas que começam com `.` ou `_`. Para tirar um lote da fila sem apagar, renomeie a pasta com `_` na frente. O `_conferidos/` da quarentena também começa com `_`.

Também pula o lote que já está marcado como **pronto** ou **erro** e cujos arquivos não mudaram. Para isso guarda uma assinatura com o nome e o tamanho de cada arquivo. Exceção: um lote cortado porque o serviço parou ("cancelado antes da leitura") **volta sozinho** para a fila.

A ordem da fila é alfabética. O `fila N` do log é quantos lotes estão pendentes.

### Passo 2 — Esperar a estação terminar

Um lote só é processado quando está **completo**:

- **Com marca de fim** (`manifesto.json`, `status.json` com `enviado_windows` ou `status_mac.json` com `enviado_mac`): espera **45 s** sem nenhum arquivo mudar.
- **Sem marca:** espera **10 min** sem mudança (`entrada_quieto_minutos`).
- **"Mudar"** considera também a hora em que o arquivo **chegou** no QNAP (ctime), e não só a data de modificação. O Mac copia mantendo a data original do scan. Sem essa regra, o vigia pegava a pasta TIFF no meio da cópia, antes de chegarem os JSON do lote (foi o que aconteceu no SESI Marília em 14/10).
- Se o `manifesto.json` diz quantos arquivos são, a contagem precisa bater.

### Passo 3 — Montar o contexto (sem gastar API)

`contexto()` junta o que a estação informou com o caminho da pasta:

| O quê | De onde vem, em ordem de prioridade |
|---|---|
| **Fundo** | Campo do manifesto/info **ou** o caminho (`F009 - EOL …`), sempre conferido na **tabela de autoridade** de fundos. Se nenhum identifica, o lote dá erro. Se os dois identificam fundos **diferentes**, é conflito e o lote para. |
| **Série** | 1) campo `serie` do manifesto; 2) pasta de material no caminho até o lote; 3) **`series` do `info_lote` do Mac**, ou a pasta de material **dentro** do lote (`03 - Fotografias/…`); 4) se nada disso existe, a série é **deduzida** (S03 para estação de foto/VueScan, senão S01) e passa pela triagem do passo 6. |
| **Código do projeto** | 1) código completo `F0xx-P0xxx` no manifesto ou na pasta; 2) **Mac com `projeto_ja_existia: true`**: `codigo_fundo` + `numero_projeto` (por exemplo F009-P0060), porque o Mac escolheu o projeto na lista do painel; 3) senão não há código ainda: o "P0001" que a estação escreve é **só pista** (decisão do Rafa, 09/10). O código sai da identificação pelo carimbo (passo 7). |
| **Nome, ano, cidade** | Manifesto/info → nome da pasta (`SBU-Taruma-1972` também). |
| **Operador, estação** | Manifesto/info. |
| **Teste** | `"teste": true` ou a palavra "teste" no caminho: o lote nunca vira publicável. |

Sem painel configurado e sem projeto existente, o lote **espera**: o vigia nunca inventa número de projeto.

### Passo 4 — Copiar para a pasta provisória

Antes de ter código, o lote vai para `<Fundo>/00 - Em identificação/F0xx-P9xxx - <nome>/`.

- **P9xxx é provisório:** nunca vai ao painel, ao EXIF nem ao site.
- Cada documento ganha o nome CAMP `F0xx-P9xxx-AAAA-S0x-DNNNNN`. TIF, JPG e DNG do mesmo documento recebem o mesmo código.
- **Cópia conferida:** hash SHA-256 do original, cópia para um temporário, hash da cópia; só então renomeia. Hash diferente vira erro.
- **Arquivo que já existe** no acervo (mesmo hash, em qualquer projeto do fundo) não é copiado de novo.
- **Espaço:** antes de começar, confere se cabe no disco.
- **Formato não suportado** (JSON e outros): fica na entrada e entra como aviso.

No log: `Lote …: copiando 7 de 38` (na primeira, na última e a cada 15 s).

### Passo 5 — Preparar as imagens (sem gastar API)

Para cada documento, a partir da melhor versão (TIF de preferência):

1. **Imagem bruta**, grande.
2. **Orientação pelo texto:** OCR (tesseract) nas 4 rotações e no espelho; vence a que lê mais texto plausível. Empate ou pouco texto = **orientação incerta**. Mesmo incerta, o "melhor chute" é aplicado à imagem de leitura e ao preview.
3. **Imagem de leitura** (2000 px), guardada em `~/.campvision2/leitura/<código>/`.
4. **Preview** (3000 px), em `ACERVOS_CAMP/_campvision/preview/<código>/`. É o que o site usa.
5. **md5 e hash perceptual**, para achar duplicatas.

Se a orientação foi decidida **com certeza**, o JPG do acervo é girado (o TIF original **nunca** é mexido). Tudo fica em `catalogacao/preparo.json`.

No log: `Lote …: preparando 12 de 19`.

### Passo 6 — Triagem por série (só se a série foi deduzida)

Sinais baratos da imagem (fração de papel claro, saturação, proporção, quantidade de texto) decidem entre S01 (prancha), S02 (documento textual) e S03 (foto). Os arquivos são renomeados para a série decidida.

> **Limite conhecido:** foto de arquitetura muito clara (céu, parede branca) parece papel e a triagem põe em S01. Por isso a série que a estação informa vale antes (passo 3), e a leitura tem uma rede de segurança (passo 7).

### Passo 7 — Ler com o modelo (gasta API)

Uma chamada por documento, com a imagem de leitura. O modo depende da série no nome do arquivo:

| Série | Modo | O que o modelo devolve |
|---|---|---|
| S03, S04, S05 | **fotografia** | tipo de imagem, assunto, enquadramento, elementos visíveis, texto na imagem, **legenda proposta** (vira o título), pessoas só por traços, **`rotacao`** (0/90/180/270 no sentido horário para ficar em pé) e **`orientacao_clara`** |
| S02 | **textual** | transcrição integral, remetente, destinatário, data, assunto |
| demais (S01…) | **prancha** | todos os campos do carimbo (projeto, arquiteto, escritório, data, folha, escala…), transcrição integral, anotações, e **`e_fotografia`** |

**Rede de segurança para foto:** se a leitura de prancha diz `e_fotografia: true` e não há carimbo, a mesma imagem é **relida com as regras de fotografia** (segunda chamada) e ganha legenda e descrição. Se a série tinha sido deduzida, o documento é renomeado para **S03**.

**Orientação de foto:** foto quase nunca tem texto, então o OCR não decide. Com `orientacao_clara`, vale o giro do modelo: ele é **somado** ao giro que o preparo já tinha aplicado. O vigia gira o JPG do acervo, a imagem de leitura e o preview, e tira o "incerta". Em dúvida, não gira e a foto continua marcada para conferência.

Cada leitura é gravada no **checkpoint** (`campvision2_checkpoint.jsonl`) assim que termina. Se o serviço cair, o que já foi lido **não é pago de novo**.

No log: `Lote iniciado: 19 prancha(s)…`, `Lote terminado: 19 lidas, 0 com carimbo, 0 erro(s)`, e o custo.

Se alguma leitura falhou (chave inválida, sem crédito, rede), o lote vira **erro** e nada é apagado.

### Passo 8 — Decidir o projeto pelo carimbo

`nucleo/identidade.py` agrupa as folhas pelo que o carimbo diz (obra, endereço, contribuinte, alvará) e compara com a lista de projetos do fundo que o painel devolve (`/api/estacoes/contexto`).

| Situação | O que o vigia faz |
|---|---|
| Código já informado pela estação (passo 3) | Usa ele, a não ser que o carimbo aponte **com força** outro projeto; aí pergunta. |
| Um candidato forte na base | Usa o código dele. |
| Dois ou mais candidatos | `POST /reservar` com `candidatos`. O painel abre a decisão "projeto parecido" e o lote **espera** (log: `aguardando decisão … — decisão no painel`). |
| Nenhum | `POST /reservar` normal; o painel devolve o código novo. |

Enquanto espera a decisão, o lote fica "aguardando" e é tentado de novo a cada rodada. A leitura **não é paga de novo**: vem do checkpoint.

Capas, envelopes e folhas sem nada legível vão junto com o projeto principal, mas fora da publicação.

### Passo 9 — Mover para o projeto definitivo

Os arquivos vão de `00 - Em identificação/F0xx-P9xxx…` para `<Fundo>/01 - Projetos/F0xx-P0xxx - <Nome>/<série>/<formato>/`. O D é renumerado para continuar a sequência do projeto, e checkpoint, cache e preview vão junto. **Não relê.** Log: `19 documento(s) de F009-P9001 → F009-P0060 (código oficial informado pela estação)`.

### Passo 10 — Metadados dentro dos arquivos

O `exiftool` grava EXIF/XMP/IPTC completo em **todas** as versões (TIF e JPG): crédito "Acervo {Arquiteto}/CAMP …", código, fundo, série, operador, estação, título, licença. Depois **confere arquivo por arquivo**. Se não conferir tudo, o lote vira erro ("EXIF conferido em 6 de 48").

No log: `gravando metadados em 38 arquivo(s)…`, `conferindo metadados…`, `Metadados gravados em 38 de 38`.

### Passo 11 — Catalogação e "pronto"

Na pasta do projeto, `catalogacao/` recebe `leituras.json`, `catalogacao.csv`, `pacote_tainacan.json` (o que o painel importa), `erros.json`, `contatos.jpg`, `relatorio.txt`, `orientacao.txt`, `preparo.json`, `mapa_origem.json` e `lotes/<lote>.json`. O `info_projeto.json` é atualizado.

Por último, `status.json` recebe `status: pronto`, com gravação atômica, e o painel recebe o **aviso**.

### Passo 12 — Original vai para a quarentena

Só depois do "pronto", o original em `100 - Scanners` vai para `100 - Scanners/_conferidos/AAAA-MM-DD/<lote>/`, e só o que foi copiado e conferido. Ele só é apagado de vez quando a **réplica** confirmou a cópia **e** passaram `quarentena_dias` (7). Sem réplica configurada, nada é apagado. Log: `… → F009-P0060 pronto: 19 documento(s), 38 copiado(s), 38 apagado(s)` ("apagado" aqui quer dizer que saiu da entrada).

### Se algo falha no meio

Antes do "pronto", qualquer falha deixa o projeto em **erro** com o motivo (`catalogacao/catalogacao_ERRO.txt`, `erros.json`, log) e **nada é apagado da entrada**. O lote não volta sozinho enquanto os arquivos não mudarem; use `--refazer-lote` depois de corrigir. A exceção é o corte por parada do serviço antes da leitura, que volta sozinho.

Toda ação, arquivo por arquivo, vai para o **livro de registro**: `ACERVOS_CAMP/_campvision/registro/AAAA-MM.jsonl` e `.csv`. Ações: recebido, copiado, conferido, renomeado, lido, exif_gravado, refeito, quarentena, apagado_original, relido, erro, decisao.

---

## 4. Como o vigia decide cada coisa

| Pergunta | Resposta curta | Onde |
|---|---|---|
| O lote está pronto? | Marca de fim + 45 s quieto, ou 10 min quieto; o ctime também conta | `entrada.pronta` |
| Que fundo? | Tabela de autoridade; nunca pelo nome solto | `entrada.contexto`, `fundos.py` |
| Que série? | Estação > pasta de material > `series` do Mac > dedução + triagem > rede de segurança da leitura | `entrada.contexto`, `triagem.py`, `visao.py` |
| Que projeto? | Código da estação > carimbo × base do fundo > painel (`/reservar`, decisão humana) | `entrada._planejar`, `identidade.py` |
| Que número D? | Sequencial no projeto, contínuo entre séries | `estrutura.py`, `transferir.py` |
| Como orientar? | OCR pelo texto; em foto, a leitura do modelo; em dúvida, não gira e marca "incerta" | `preparo.py`, `derivados.orientar_pela_foto` |
| Que título? | Prancha: carimbo; foto: legenda proposta; textual: assunto | `visao.py`, `projeto.py` |
| Publicável? | Sem bloqueio (autoria divergente, projeto divergente, teste) | `projeto.py`, pacote |

---

## 5. O que fica no acervo

```
ACERVOS_CAMP/
  F009 - EOL Euclides Oliveira/
    00 - Em identificação/                 ← provisório (P9xxx); some depois do passo 9
    01 - Projetos/
      F009-P0060 - SESI — Marília/
        info_projeto.json                  ← código, nome, ano, operador, estação
        status.json                        ← processando | aguardando | pronto | erro
        campvision2_checkpoint.jsonl       ← memória das leituras (não pagar de novo)
        03 - Fotografias/
          TIF/F009-P0060-1984-S03-D00001.tif   ← master: nunca é mexido
          JPG/F009-P0060-1984-S03-D00001.jpg   ← derivado: pode ser girado
        catalogacao/
          leituras.json  catalogacao.csv  pacote_tainacan.json  erros.json
          preparo.json   mapa_origem.json contatos.jpg  relatorio.txt  orientacao.txt
          lotes/<lote>.json
          releituras/<AAAAMMDD-HHMMSS>/    ← antes × depois de cada releitura
  _campvision/
    preview/<F0xx-P0xxx>/<código>.jpg      ← 3000 px, já girado, para o site
    registro/AAAA-MM.jsonl                 ← livro de registro
    sentinela.json                         ← prova para a réplica
    fundos/F0xx.json                       ← parâmetros por fundo (escritos por gente)
```

Séries: S01 Desenhos e pranchas · S02 Documentos textuais · S03 Fotografias · S04 Negativos · S05 Slides · S06 Materiais e especificações · S07 Publicações · S99 Não identificado.

---

## 6. O que vai para o painel, e quando

| Chamada | Quando |
|---|---|
| `POST /api/estacoes/heartbeat` | a cada 60 s: estado, projeto, fila, números do dia, montagens, quarentena |
| `GET /api/estacoes/contexto?fundo=` | ao identificar: fundos, coautores e projetos do fundo |
| `POST /api/estacoes/projetos/reservar` | projeto novo ou duvidoso (202 = decisão pendente) |
| `POST /api/campvision/aviso` | a cada troca de `status` e no fim de `--reler` / `--avisar-painel` |
| `GET /api/estacoes/pedidos-releitura` | a cada rodada: "Ler dados agora" pedido no painel |

Se o painel estiver fora, avisos e heartbeats ficam em `~/.campvision2/painel_pendentes.json` e são reenviados.

**O que o painel faz com o aviso** (`camp-painel`, `rotas_operacao.ler_pasta_do_aviso` → `importador_lote.importar_lote`): relê a pasta e, se o lote está em revisão e não foi aprovado, importa o `pacote_tainacan.json`. O importador **não mexe** em folha já revisada por uma pessoa nem em folha no site.

> **Limite atual do painel (14/10):** se uma folha muda de série depois de importada (S01 → S03 com o mesmo D), o importador recusa a nova ("o número D… já é de …-S01-…") e a folha antiga continua lá. Foi o caso do SESI Marília. A correção é no `camp-painel`: renomear a folha existente do mesmo lote em vez de recusar. Até lá, uma folha que mudou de série precisa ser acertada à mão no painel.

---

## 7. O servidor: serviço, log, atualização e estado

**Serviço:** `campvision2.service` (systemd). Sobe no boot, volta se cair, lê as chaves de `~/.campvision2/ambiente` e roda `vigia.py --sem-painel`. Ao parar, termina a imagem em andamento (até 180 s).

```bash
systemctl status campvision2
sudo systemctl restart campvision2      # corta o lote em andamento; prefira o atualizar.sh
journalctl -u campvision2 -f            # log ao vivo
journalctl -u campvision2 --since 16:40 # log desde um horário
```

**Atualizar:**

```bash
cd ~/campvision-new && ./atualizar.sh          # espera o lote terminar
cd ~/campvision-new && ./atualizar.sh --agora  # não espera (o lote cortado volta à fila)
cd ~/campvision-new && ./atualizar.sh --voltar # desfaz a última atualização
```

O script puxa o código do `main`, instala dependências, faz backup de `~/.campvision2`, **roda os testes (se falharem, volta sozinho)**, reinicia e abre o log ao vivo. **Aperte Ctrl+C para sair do log.** Comandos colados depois dele só rodam quando o log fecha. Sem isso, o próprio vigia se atualiza a cada **60 min**, sempre entre lotes. O build aparece no log: `CAMP Vision 2 build 2026-10-14-05`.

**Estado local (`~/.campvision2/`):**

| Arquivo | O que guarda |
|---|---|
| `config.json` | configuração (pastas, painel, modelo, intervalos) |
| `ambiente` | `ANTHROPIC_API_KEY`, `CAMP_PAINEL_TOKEN` (permissão 600) |
| `estado.json` | o que o vigia está fazendo agora (situação, projeto, fila), regravado a cada 5 s |
| `entrada.jsonl` | o que já foi feito com cada lote da entrada (pronto, erro, aguardando + assinatura) |
| `reservas.json` | códigos já reservados no painel (para não reservar duas vezes) |
| `eventos.jsonl`, `relatorios/` | números do dia e relatórios |
| `leitura/`, `leitura_bruta/` | imagens de leitura (cache) |
| `painel_pendentes.json` | avisos que o painel ainda não recebeu |
| `backups/` | cópias feitas pelo `atualizar.sh` |

**Comandos pela linha de comando que usam a API ou o painel** precisam das chaves no terminal:

```bash
cd ~/campvision-new
set -a; . ~/.campvision2/ambiente; set +a
.venv/bin/python vigia.py --reler F009-P0060 --escopo projeto
```

**Ver o que o vigia está fazendo** (o log pode ficar parado minutos em lote de TIFF grande):

```bash
python3 -c "import json,pathlib; d=json.loads((pathlib.Path.home()/'.campvision2'/'estado.json').read_text()); print(d['situacao'], '|', d['projeto_atual'], '| fila', d['fila'], '|', d['gravado_em'])"
```

---

## 8. Comandos

Todos com `~/campvision-new/.venv/bin/python vigia.py` na frente.

**Dia a dia**

| Comando | Para quê |
|---|---|
| `--lotes` | o que está em `100 - Scanners` e em que pé está |
| `--status` | o que está pendente agora, sem processar |
| `--monitor` | a tela do servidor, por SSH |
| `--historico TERMO` | caminho completo de um arquivo (nome original, código, lote ou data) |
| `--diagnostico [F0xx]` | por projeto: documentos, lidos, vazios, sem JPG, pacote; quarentena e réplica |
| `--proximo-p F0xx` | próximo número P livre no acervo |

**Refazer e consertar**

| Comando | Para quê | Gasta API? |
|---|---|---|
| `--refazer-lote TEXTO` | devolve à fila o lote da entrada cuja pasta contém TEXTO | depende (checkpoint segura) |
| `--reler F0xx-P0xxx --escopo vazios\|projeto\|documentos [--documentos COD,COD]` | relê no acervo, guarda o antes em `releituras/`, aplica os giros, **avisa o painel** | sim |
| `--avisar-painel F0xx-P0xxx` | aplica nos JPG os giros já decididos e pede ao painel para reimportar | não |
| `--reclassificar F0xx-P0xxx --serie S03 [--de S01] [--ano AAAA] [--codigo-novo …] [--nome-novo …]` | troca série, ano ou código de todos os documentos (arquivos, catalogação, cache, preview, XMP) | não |
| `--reidentificar F0xx[-P0xxx] [--sim]` | reagrupa pelo carimbo o que já entrou; sem `--sim` só mostra o plano | não |
| `--consertar-acervo [F0xx] [--sim]` | gera JPG que faltam, relê o que veio vazio, refaz pacotes; sem `--sim` só estima | sim |
| `--decisao CODIGO --acao retirar\|publicar\|nota --motivo "…" --por NOME` | decisão humana sobre um documento | não |
| `--organizar-formatos` | move o que já está no acervo para `<série>/TIF`, `JPG`… | não |

**Configuração e diagnóstico**

| Comando | Para quê |
|---|---|
| `--criar-config`, `--identidade`, `--info` | config padrão, crédito gravado, esquema dos `info_projeto.json` |
| `--entrada CAMINHO`, `--acervo CAMINHO`, `--painel URL` | apontar as pastas e o painel (salva no config) |
| `--estimativa`, `--avaliar GABARITO.csv`, `--backtest …` | custo, placar de leitura contra gabarito |

---

## 9. Problemas conhecidos e como resolver

| Sintoma | Causa | O que fazer |
|---|---|---|
| Log parado por minutos | cópia, preparo ou EXIF de TIFF pela rede | ver `estado.json` (seção 7); a partir da 2026-10-14-03 o log mostra "copiando/preparando N de M" |
| `cancelado antes da leitura` | serviço parado no meio do lote | volta sozinho na próxima subida (desde 2026-10-14-01) |
| Lote em `erro` depois de reinício ("EXIF conferido em 6 de 48") | reinício cortou o exiftool | `--refazer-lote NOME`; a leitura não é paga de novo |
| `aguardando decisão … — decisão no painel` | carimbo parecido com 2+ projetos | decidir no painel; o lote segue sozinho |
| Fundo "não está na tabela de autoridade" | pasta sem código F000 reconhecível (ex.: "Convidado") | renomear a pasta com o código do fundo, ou `_` na frente para tirar da fila |
| Lote de teste gastando API | pasta de teste em `100 - Scanners` | `_` no começo do nome da pasta |
| Fotos em "Desenhos e pranchas", sem título | série deduzida errada (antes da 2026-10-14-04) | `--reclassificar F0xx-P0xxx --serie S03 --de S01` e depois `--reler … --escopo projeto` |
| Foto com orientação incerta | modelo em dúvida (detalhe, vista aérea) | conferir à mão no painel |
| Painel não muda depois de `--reler` | painel não importa folha que mudou de série; folha revisada é protegida | ver o limite na seção 6 |
| `Sem chave de API` num comando | terminal sem as chaves | `set -a; . ~/.campvision2/ambiente; set +a` |
| `--avisar-painel` diz "Painel não configurado" | `painel_url` vazio | `--painel http://192.168.15.60:8000` |
| Comandos colados depois do `atualizar.sh` não rodam | o script termina no log ao vivo | Ctrl+C e rodar de novo, um de cada vez |

---

## 10. Pendências abertas

1. **Painel:** importar folha que mudou de série (S01 → S03) renomeando a existente, em vez de recusar (seção 6).
2. **Lote do Mac com várias séries** (fotos e negativos no mesmo envio): hoje o lote inteiro recebe uma série só. Cada arquivo precisa pegar a série da sua pasta.
3. **Falha durante um reinício** que não seja "cancelado antes da leitura" (ex.: EXIF cortado) ainda fica em erro e precisa de `--refazer-lote`.
4. **Lote que já estava no acervo** ("os N arquivo(s) já estavam no acervo") não regrava metadados. Se o EXIF tinha sido cortado antes (Itatiba F016-P0024, 14/10), precisa de conserto à parte.
5. **Orientação de prancha sem texto** (croqui) continua só pelo OCR.
6. **Levantamento** dos projetos de foto que entraram como S01 ou sem descrição antes da 2026-10-14-04.

---

## 11. Onde mexer no código

| Quero mudar… | Arquivo |
|---|---|
| quando um lote está pronto, o que é um lote, a fila | `nucleo/entrada.py` (`descobrir`, `pronta`, `Recebedor.pendentes`) |
| fundo, série, código e nome a partir da estação e da pasta | `nucleo/entrada.py` (`contexto`, `lote_mac`, `codigo_do_mac`) |
| cópia, preparo, triagem, leitura, decisão, mudança de projeto | `nucleo/entrada.py` (`Recebedor.processar`, `_planejar`, `_concluir`) |
| prompts e o que o modelo devolve | `nucleo/visao.py` (`INSTRUCOES`, `INSTRUCOES_FOTO`, `INSTRUCOES_TEXTO`), `nucleo/esquema.py` |
| orientação pelo texto | `nucleo/preparo.py` |
| giro de JPG (OCR e foto) | `nucleo/derivados.py` (`orientar_jpg`, `orientar_pela_foto`) |
| triagem por série | `nucleo/triagem.py` |
| agrupar pelo carimbo, casar com a base | `nucleo/identidade.py` |
| renomear e reclassificar | `nucleo/renomear.py` |
| releitura | `nucleo/releitura.py` |
| metadados gravados | `nucleo/metadados.py` |
| pacote para o painel | `nucleo/projeto.py`, `entrada.pacote_tainacan` |
| chamadas ao painel | `nucleo/painel.py` |
| laço do serviço, heartbeat, auto-atualização | `nucleo/vigia.py`, `nucleo/atualizador.py`, `atualizar.sh` |
| comandos | `vigia.py` |
| quarentena e réplica | `nucleo/quarentena.py` |
| versão | `VERSAO_BUILD` em `nucleo/config.py` (subir a cada mudança) |

Testes: `python -m unittest discover -s tests -t .` (316 testes, sem rede). O `atualizar.sh` roda os mesmos testes antes de trocar a versão no servidor.
