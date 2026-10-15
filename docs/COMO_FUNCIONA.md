# CAMP Vision 2 — como funciona, passo a passo

Guia completo do sistema: da digitalização na estação até a folha aparecer no painel. Vale para a **versão 2026-10-14-05** do CAMP Vision 2 (o "vigia"). Onde o comportamento depende de outro sistema (estação Mac, painel), isso está dito e com o arquivo de origem.

Documentos irmãos:
- [contrato-painel.md](contrato-painel.md) — contrato técnico CV2 ↔ painel (formato de cada arquivo e de cada chamada HTTP). Exemplos reais em [contrato/exemplos/](contrato/exemplos/).
- [README](../README.md) — instalação no Mac e no servidor, modo janela, histórico.

As referências `arquivo.py:linha` apontam para o código desta versão (`nucleo/` quando não diz outra pasta).

---

## Índice

1. [Visão geral](#1-visão-geral)
2. [As peças](#2-as-peças)
3. [As estações: o que chega no 100 - Scanners](#3-as-estações-o-que-chega-no-100---scanners)
4. [O caminho de um lote, passo a passo](#4-o-caminho-de-um-lote-passo-a-passo)
5. [Como o vigia decide cada coisa](#5-como-o-vigia-decide-cada-coisa)
6. [A leitura com o modelo, em detalhe](#6-a-leitura-com-o-modelo-em-detalhe)
7. [O que fica no acervo](#7-o-que-fica-no-acervo)
8. [Formatos de arquivo](#8-formatos-de-arquivo)
9. [O painel: o que recebe e o que faz](#9-o-painel-o-que-recebe-e-o-que-faz)
10. [O servidor](#10-o-servidor)
11. [Comandos](#11-comandos)
12. [Configuração (todas as chaves)](#12-configuração-todas-as-chaves)
13. [Modo antigo: pasta vigiada](#13-modo-antigo-pasta-vigiada)
14. [Problemas conhecidos e como resolver](#14-problemas-conhecidos-e-como-resolver)
15. [Pendências e riscos conhecidos](#15-pendências-e-riscos-conhecidos)
16. [Onde mexer no código](#16-onde-mexer-no-código)
17. [Testes e contrato](#17-testes-e-contrato)

---

## 1. Visão geral

As estações de digitalização deixam os scans em `100 - Scanners` no QNAP. O CAMP Vision 2, que roda como serviço no servidor `campvision2`, pega cada lote quando a estação termina e copia os arquivos com o nome CAMP para `ACERVOS_CAMP`. Depois lê cada imagem com um modelo de visão (Claude), grava os metadados dentro dos arquivos, escreve a catalogação e avisa o painel, que importa as folhas para revisão e publica no site.

```
 Estações                 QNAP                       Servidor .40               QNAP                    Painel .60        Site
 (Mac VueScan,      ┌──────────────────┐        ┌──────────────────┐     ┌──────────────────┐     ┌────────────┐
  Windows Contex,──►│ 100 - Scanners   │──────► │ CAMP Vision 2    │───► │ ACERVOS_CAMP     │───► │ revisão    │──► Tainacan
  Universal)        │ (entrada bruta)  │        │ (vigia)          │     │ (acervo final)   │     │ e publica  │
                    └──────────────────┘        └───────┬──────────┘     └──────────────────┘     └─────▲──────┘
                           ▲                            │  contexto, reservar, heartbeat,               │
                           │ quarentena (_conferidos)   └── aviso, pedidos de releitura ────────────────┘
```

**Regras de ouro**

- Os **arquivos no QNAP são a fonte da verdade**. O HTTP com o painel só acelera e informa.
- O **painel só lê** o `ACERVOS_CAMP`. Nunca lê o `100 - Scanners` e nunca escreve em pasta de projeto.
- O vigia **nunca inventa** código de fundo nem número de projeto: o fundo vem da tabela de autoridade, o número vem do painel.
- O **master** (TIF, DNG, PDF…) nunca é alterado. Só o JPG derivado pode ser girado.
- **Nada no `ACERVOS_CAMP` é apagado** pelo vigia. Da entrada, o original só sai depois de copiado e conferido, e vai para uma quarentena antes de ser apagado de vez.

---

## 2. As peças

| Peça | Onde | O que faz |
|---|---|---|
| **Estação Mac** (VueScan) | repositório `camp-sistema-digitalizacao-mac` | Fotos, negativos e slides. Ver [seção 3.1](#31-estação-mac-vuescan). |
| **Estações Windows** (Contex, Universal) | outro sistema | Pranchas e documentos. Ver [seção 3.2](#32-estações-windows-contex-universal). |
| **`100 - Scanners`** | QNAP, montado no servidor em `/mnt/camp/backup/Arquivos/100 - Scanners` | Entrada bruta. As estações escrevem; só o vigia lê. |
| **CAMP Vision 2 (vigia)** | servidor `campvision2` (Ubuntu, IP fixo 192.168.15.40), serviço systemd `campvision2`, repositório `mullets/campvision-new` em `~/campvision-new` | Recebe, renomeia, lê, grava metadados, cataloga e avisa o painel. |
| **`ACERVOS_CAMP`** | QNAP, montado em `/mnt/camp/backup/Fundos e Escritorios/ACERVOS_CAMP` | Acervo final, por fundo e projeto. |
| **Painel** | repositório `camp-painel`, `http://192.168.15.60:8000` | Tabela de fundos e projetos, reserva de números, decisões humanas, revisão e publicação. |
| **API Anthropic** | nuvem | O modelo que lê as imagens (padrão `claude-sonnet-5`). |
| **Réplica** (opcional) | caminho em `caminho_replica` | Cópia de segurança do acervo. Sem ela, a quarentena da entrada nunca é esvaziada. |

---

## 3. As estações: o que chega no 100 - Scanners

### 3.1 Estação Mac (VueScan)

Fluxo do operador (`sistema_mac.py`, `nucleo.py` do repositório do Mac):

1. **F9 — abrir lote.** O operador escolhe fundo e projeto.
   - **Projeto já existente:** escolhido na lista do painel. O Mac grava `projeto_ja_existia: true`.
   - **Projeto novo:** o Mac **reserva o número P no painel na hora** (tenta por até 180 s se o painel responde "aguardando decisão") e cria `100 - Scanners/F0xx - SIGLA - Nome/P0xxx - Nome - Ano/` com as 10 subpastas padrão. Para projeto novo grava sempre `projeto_ja_existia: false`, mesmo se o painel respondeu que já existia (`sistema_mac.py:1240`).
2. **Digitalização.** O VueScan gera TIFF e JPG localmente.
3. **F10 — finalizar.** Localmente:
   - separa duplicatas exatas em `_duplicados`;
   - numera os D continuando do maior D (no QNAP ou no painel);
   - renomeia para `F0xx-P0xxx-AAAA-S0x-DNNNNN_<slug>.tif/.jpg` em `<NN - Material>/01 - Arquivo Arquivístico (TIFF)/` e `03 - Preview (JPG)/`;
   - grava `info_lote_AAAAMMDD-HHMMSS_mac.json` e, se não houver, `info_projeto.json`.
4. **F12 — backup para o QNAP.**
   - Copia os scans e depois o `info_lote_*.json`, cada um por um temporário `.copiando` + renomear.
   - Copia o `info_projeto.json` **só se o QNAP ainda não tem**.
   - Confere a cópia **pelo tamanho**.
   - Grava **por último** `status_mac.json` = `{"etapa": "enviado_mac", …}` e apaga a cópia local.

O que o vigia usa do Mac:

| Do Mac | Uso no vigia |
|---|---|
| `status_mac.json` com `etapa: enviado_mac` | marca de fim (espera só 45 s) |
| `info_lote_*_mac.json` → `projeto_ja_existia`, `codigo_fundo`, `numero_projeto` | com `projeto_ja_existia: true`, o código `F0xx-P0xxx` vale como oficial |
| `info_lote_*_mac.json` → `series` | se declara **uma única** série, ela vale |
| `info_projeto.json` | fundo, nome, ano, cidade |
| Nome e numeração D do Mac | **descartados**: o vigia renumera tudo |

> O Mac grava operador e estação só no `info_lote` (`operador`, `estacao_id`, `estacao_tipo`). O vigia lê operador e estação só do `manifesto.json`/`info_projeto.json`. Resultado: **lotes do Mac chegam com operador e estação vazios** no EXIF, no `info_projeto.json`, no livro e no painel. Ver [pendências](#15-pendências-e-riscos-conhecidos).

### 3.2 Estações Windows (Contex, Universal)

O repositório das estações Windows não está neste projeto. O que o vigia espera delas:

- **Pastas:** `100 - Scanners/<fundo>/<material>/<projeto>/`, opcionalmente com `TIF/`, `JPG/`, `DNG/`, `PDF/` dentro. A ordem de material e projeto tanto faz.
- **Marca de fim:** um `manifesto.json` (qualquer conteúdo) ou um `status.json` com `status: "enviado_windows"` (ou `etapa: "enviado_windows"`: as duas chaves valem desde o contrato 2.3). Qualquer outro status conta como "sem marca".
- **Campos lidos** do `manifesto.json`, que valem sobre o `info_projeto.json`:
  - `fundo_codigo` ou `fundo`;
  - `serie` (só `S01`–`S07`, `S99`) ou `tipo_material`;
  - `codigo` ou `projeto_codigo` (um `F0xx-P0xxx` completo vale como oficial);
  - `nome`, `titulo` ou `projeto`; `ano`; `cidade`;
  - `operador`; `operador_email` ou `email`; `estacao`; `tipo_estacao`;
  - `enviado_em`, `horario` ou `data_envio`;
  - `lote_id` ou `lote`;
  - `teste`;
  - `contagens.arquivos` ou `arquivos` (a quantidade de arquivos tem que bater).
- Uma pasta no padrão `SBU-Taruma-1972` vira nome "Taruma" e ano 1972.

---

## 4. O caminho de um lote, passo a passo

Tudo acontece em `entrada.py` (`Recebedor.processar` e `_concluir`), chamado pelo laço do serviço (`vigia.py`).

### 4.0 A rodada

O serviço repete, para sempre (`vigia.py:901-933`):

1. **Quarentena e sentinela:** regrava `ACERVOS_CAMP/_campvision/sentinela.json` e apaga da quarentena o que já pode sair ([passo 13](#passo-13--quarentena-do-original)).
2. **Lotes da entrada** (`receber_lotes`): processa a fila do `100 - Scanners`, um lote por vez.
3. **Pedidos de releitura do painel** ([seção 9.3](#93-pedidos-de-releitura-ler-dados-agora)).
4. Só no [modo antigo](#13-modo-antigo-pasta-vigiada): a pasta vigiada.
5. Descarrega o livro de registro guardado localmente (se o QNAP tinha caído).
6. **Auto-atualização** (se for a hora) e **relatório diário** (se for a hora).
7. **Pausa de 30 s** (`intervalo_varredura_segundos`) e começa de novo. Os 30 s são a pausa **depois** de cada rodada, não um relógio fixo.

Em paralelo, uma linha de fundo grava `~/.campvision2/estado.json` a cada 5 s e manda o heartbeat ao painel a cada 60 s.

Se o QNAP der erro de disco ou rede (somente leitura, cheio, caído) no meio de um lote, a rodada para com o estado "QNAP com problema", **nada é apagado**, e o lote é tentado de novo na próxima rodada. Outro erro inesperado marca o lote como erro.

### Passo 1 — Achar os lotes

O vigia percorre o `100 - Scanners` até 6 níveis (`entrada.descobrir`). Um **lote** ("unidade" no código) é a primeira pasta, de cima para baixo, que:

- tem arquivo direto dentro (arquivos começados com `.` e os marcadores não contam); ou
- tem um marcador: `manifesto.json`, `info_projeto.json`, `status.json` ou `status_mac.json`; ou
- só tem subpastas de formato: `tif`, `tiff`, `jpg`, `jpeg`, `dng`, `pdf`, `png`, `raw`, `preview(s)`, `alta`, `baixa`, `alta/baixa resolucao`, `matriz`, `master`.

Regras:
- **Subpastas que começam com `.` ou `_` são ignoradas.** Para tirar um lote da fila sem apagar, renomeie a pasta com `_` na frente. A quarentena (`_conferidos`) é ignorada por isso.
- A própria raiz nunca é um lote.
- Num lote do Mac, a pasta do projeto (`P0060 - SESI …`) é o lote, porque tem os JSON direto nela.

**Fila** (`Recebedor.pendentes`): entra todo lote encontrado, menos o que está registrado em `~/.campvision2/entrada.jsonl` como **pronto** ou **erro** com a mesma **assinatura**. A assinatura é o caminho relativo + tamanho de cada arquivo, sem marcadores e sem ocultos (editar um `status.json` não muda a assinatura).
- Lote **cancelado porque o serviço parou antes da leitura** volta sozinho.
- Lote **aguardando** (decisão no painel) é tentado de novo a cada rodada.
- A fila é em ordem alfabética do caminho. O `fila N` do log é o tamanho dela.

### Passo 2 — Esperar a estação terminar

`entrada.pronta`:

| Situação | Espera |
|---|---|
| Lote com marca de fim (`manifesto.json`, `status.json` `enviado_windows`, `status_mac.json` `enviado_mac`) | 45 s sem nenhum arquivo mudar (`espera_estabilidade_segundos`) |
| Sem marca | 10 min sem mudança (`entrada_quieto_minutos`) |

- "Mudar" usa a data de modificação **e** a hora em que o arquivo chegou no QNAP (ctime). O Mac copia mantendo a data do scan; sem o ctime, a pasta TIFF era pega no meio da cópia (SESI Marília, 14/10).
- Se o manifesto diz quantos arquivos são, a contagem precisa bater ("contagem X de Y").
- Lote vazio ("vazia") ou com arquivo sumindo durante a leitura espera a próxima rodada.

### Passo 3 — Montar o contexto (sem gastar API)

`entrada.contexto` junta manifesto, `info_projeto.json`, `info_lote` do Mac e o caminho. Regras completas na [seção 5.1](#51-contexto-fundo-série-código-nome). Resumo:

- **Fundo** pela tabela de autoridade. Sem fundo, ou com conflito entre o que a estação diz e a pasta, o lote para em erro.
- **Série** declarada pela estação ou deduzida.
- **Código** só se a estação informar um oficial (manifesto, Mac com projeto existente, ou `F0xx-P0xxx` na pasta).
- **Nome, ano, cidade, operador, estação, lote, teste.**

Erro de contexto (fundo desconhecido, sem nome de projeto) só vai para o livro e para o `entrada.jsonl`; **não cria pasta nem `status.json`** e o painel não fica sabendo.

Sem painel configurado e sem projeto existente no acervo, o lote **espera** ("painel não configurado: não há como numerar projeto novo") **antes de ler**, sem gastar API.

### Passo 4 — Copiar para a pasta provisória

Antes de ter código, o lote vai para `<Fundo>/00 - Em identificação/F0xx-P9xxx - <nome>/`.

- **P9xxx é provisório** (P9001 a P9999, o primeiro livre). Nunca vai ao painel, ao EXIF nem ao site. A mesma pasta é reaproveitada nas rodadas seguintes graças ao marcador `.unidade_cv2`.
- A pasta do fundo é criada se faltar, com o modelo de pastas e `README.md` ([seção 7.1](#71-estrutura-de-pastas)).
- `status.json` = `processando` (sem aviso ao painel, porque o código é provisório).
- **Agrupar versões:** arquivos com o mesmo nome-base (sem extensão, sem diferenciar maiúscula, em qualquer subpasta) são **o mesmo documento** e recebem o mesmo código. Formatos não suportados (JSON, `.copiando`…) entram em `nao_suportados`, com aviso "formato não suportado — ficou na entrada".
- **Espaço:** precisa de espaço livre ≥ tamanho do lote × 1,1 + 200 MB, senão erro.
- **Já está no acervo?** Se **todas** as versões de um documento já existem no fundo (mesmo SHA-256, pelos `mapa_origem.json` dos projetos em `01 - Projetos`), o documento não é copiado. Se o lote inteiro já estava no acervo, ver o [caso especial](#caso-especial-tudo-já-estava-no-acervo).
- **Cópia conferida:** SHA-256 do original → cópia para `.<nome>.cv2tmp` → SHA-256 da cópia → renomear. Hash diferente = erro "cópia não confere". Destino que já existe com outro conteúdo = erro.
- Nome: `F0xx-P9xxx-AAAA-S0x-DNNNNN.<ext>` em `<série>/<FORMATO>/`. O D continua a sequência da pasta. Uma nova rodada reaproveita os códigos já dados (pelo `mapa_origem.json`).
- **Duplicata dentro do lote** (mesmo SHA com códigos diferentes): aviso "conteúdo idêntico a …".
- Série deduzida: aviso "série … deduzida …; corrigir com --reclassificar".
- Grava `catalogacao/mapa_origem.json` (destino, origem, nome original, lote, formato) e `catalogacao/lotes/<lote>.json`.

O cancelamento (parada do serviço) só é verificado **depois** da cópia. Se o serviço para durante a cópia, o systemd espera até 180 s e depois mata o processo.

No log: `Lote …: copiando 7 de 38` (na primeira, na última e a cada 15 s).

#### Caso especial: tudo já estava no acervo

Se nenhum arquivo precisou ser copiado, o vigia **não lê, não grava EXIF e não refaz a catalogação**: limpa a pasta provisória, manda os originais para a quarentena, marca o lote como pronto com o código do projeto existente e escreve no log `os N arquivo(s) já estavam no acervo (F0xx-P0xxx)`. É o que acontece com a pasta que sobra no `100 - Scanners` depois de um lote do Mac (só com os JSON): log `os 0 arquivo(s) já estavam no acervo ()`.

### Passo 5 — Preparar as imagens (sem gastar API)

Para cada documento (`preparo.preparar`), a partir da **versão mais leve** (ordem: jpg, jpeg, png, tif, tiff, dng, nef, pdf, cdr, plt — `formatos.py:30`):

1. **Imagem bruta** de até 3000 px em `~/.campvision2/leitura_bruta/<código>/` (apagada depois).
2. **Orientação pelo texto** ([seção 5.4](#54-orientação-e-espelho)): OCR nas 4 rotações e no espelho.
3. **Imagem de leitura** (2000 px, JPEG 78) em `~/.campvision2/leitura/<código>/`, já com a orientação aplicada, **mesmo quando incerta** (melhor chute).
4. **Preview** (3000 px, JPEG 85) em `ACERVOS_CAMP/_campvision/preview/<código>/`, também já orientado. É o que o site usa.
5. **md5 do master** e **hash perceptual** (dHash 64 bits da imagem orientada), para duplicatas.

Documento já preparado (em `preparo.json` e com a imagem de leitura) não é refeito. Imagem que não abre vira erro "arquivo corrompido" e o documento fica sem leitura (e o lote falha no passo 7).

Se a orientação foi decidida **com certeza**, o JPG do acervo é girado e desespelhado agora ([seção 5.4](#54-orientação-e-espelho)). Tudo vai para `catalogacao/preparo.json`: `md5, hash_perceptual, rotacao, espelhada, incerta, pontos, lido_de, origem_formato, tamanho, preview` e depois `jpg_orientado`, `triagem`, `identidade`, `orientacao_origem`.

No log: `Lote …: preparando 12 de 19`.

### Passo 6 — Triagem por série (só se a série foi deduzida)

Ver [seção 5.2](#52-série). Os documentos são renomeados para a série que a triagem decidir (log: `N arquivo(s) renomeado(s) (série decidida na triagem)`).

### Passo 7 — Ler com o modelo (gasta API)

Uma chamada por documento, 4 em paralelo, com a imagem de leitura. O modo depende da série: S03/S04/S05 → **fotografia**; S02 → **textual**; o resto → **prancha**. Detalhes na [seção 6](#6-a-leitura-com-o-modelo-em-detalhe).

- **Checkpoint:** cada leitura é gravada em `campvision2_checkpoint.jsonl` assim que termina. Numa nova rodada, o que já foi lido **não é pago de novo**. Leitura que deu erro é tentada de novo.
- **Rede de segurança de foto:** prancha sem carimbo que o modelo diz ser fotografia é relida como foto. Com série deduzida, o documento vira **S03** (log: `N arquivo(s) renomeado(s) (a leitura viu que é fotografia)`).
- **Orientação de foto pela leitura** ([seção 5.4](#54-orientação-e-espelho)).
- Depois da leitura: consolidação por grupo e pistas da pasta/info ([seção 6.4](#64-depois-da-leitura-consolidação-pistas-e-grafias)).
- **Qualquer leitura que falhou** (chave inválida, sem crédito, rede, documento sem leitura) faz o lote inteiro ir para **erro**: o painel não pode receber folha sem catalogação, e o original não pode ser apagado. Parar o serviço **durante** a leitura também termina em erro ("leitura falhou: cancelado antes de terminar") e o lote precisa de `--refazer-lote`; a leitura já feita fica no checkpoint.

No log: `Lote iniciado: 19 prancha(s) a ler de 19 na pasta`, `Lote terminado: 19 lidas, 0 com carimbo, 0 erro(s)`, `… US$ 0.44`.

### Passo 8 — Enriquecer (sem gastar API)

`entrada.enriquecer`:
- **Duplicatas:** md5 igual = "exata"; dHash a ≤ 6 bits = "quase". Vale como original o arquivo maior. A duplicata fica bloqueada para publicação.
- **Autoria divergente** (só prancha): o arquiteto lido não bate com o titular nem com os coautores do fundo → bloqueia, sem volta.
- **Fora do período** de atuação do fundo: só um aviso.
- **Pessoas em fotos:** nomeadas só pelas chaves de identidade do fundo ([seção 6.5](#65-regras-por-código-depois-da-leitura)).
- **Confiança da rotação** = (melhor − segundo) / melhor, dos pontos do OCR.

### Passo 9 — Decidir o projeto pelo carimbo

Ver [seção 5.3](#53-projeto-e-código). Resultado gravado em `catalogacao/identificacao.json` e `identificacao.txt`. Se falta uma decisão humana, o lote fica **aguardando** (`status.json` da pasta provisória) e é tentado de novo a cada rodada, **sem pagar a leitura de novo**. Log: `Lote … aguardando decisão: 'Familia - Nada': qual projeto? (F001-P0002 × F001-P0004) — decisão no painel`.

### Passo 10 — Mover para o projeto definitivo

`transferir.py`, para cada destino decidido:
- Os arquivos vão de `00 - Em identificação/F0xx-P9xxx…` para `<Fundo>/01 - Projetos/F0xx-P0xxx - <Nome>/<série>/<FORMATO>/`. A pasta do projeto é criada com as 8 pastas de série e `README.md`, ou reaproveitada se já existe (pelo código, pelo número P ou pelo nome).
- O D é **renumerado** para continuar a sequência do projeto. Se o projeto já tem o mesmo conteúdo (mesmo SHA), o documento fica com o código existente e a cópia repetida é descartada.
- Vão junto: checkpoint, `mapa_origem`, `preparo`, `lotes/*.json`, cache de leitura e preview. **Não relê.**
- Cada arquivo movido vai para o livro como `refeito`.
- A catalogação da pasta provisória é copiada para `<projeto>/catalogacao/identificacao/<lote>/`. A pasta provisória é apagada (a pasta `00 - Em identificação` fica); se sobrar documento nela, fica com um aviso.
- Um lote pode virar **vários projetos**; cada um é concluído separadamente e o custo é dividido pelo número de documentos.

Log: `19 documento(s) de F009-P9001 → F009-P0060 (código oficial informado pela estação)`.

### Passo 11 — Concluir o projeto

`Recebedor._concluir`, já na pasta definitiva:

1. `status.json` = `processando` **com aviso ao painel**.
2. **Reconsolida o projeto inteiro** (sem API): consolidação, melhor versão, grafias congeladas, título para publicação, arquivo de origem.
3. **Ano `0000`:** se os códigos foram dados sem ano, o projeto inteiro é renomeado para o ano mais frequente lido (se apareceu 2+ vezes ou é o único).
4. **S01 que é documento** (série deduzida): renomeado para S02 (CV-14).
5. **Metadados** (passo 12).
6. **Catalogação** ([seção 7.2](#72-a-pasta-catalogacao)): `leituras.json`, `catalogacao.csv`, `pacote_tainacan.json`, `erros.json`, `contatos.jpg`, `relatorio.txt`, `orientacao.txt`, `propostas.json` (se houver), `lotes/<lote>.json` com contagens, `execucao` e `aceite`.
7. `info_projeto.json` é **reescrito** com: `codigo, nome, fundo_codigo, ano, folhas_esperadas` (documentos do projeto), `estacao, tipo_estacao, operador, operador_email, teste, atualizado_em, gerado_por`.
8. **Por último**, `status.json` = `pronto` (gravação atômica) com contagens, e **aviso ao painel**. Se existia um `catalogacao_ERRO.txt`, ele é apagado.

### Passo 12 — Metadados dentro dos arquivos

Um único `exiftool` para o lote (`metadados.py`), em todas as versões **menos** `.cdr`, `.nef` e `.plt`. O que é gravado está na [seção 7.6](#76-metadados-gravados-nos-arquivos).

**Conferência:** lê de volta o `XMP-dc:Identifier` de cada arquivo e compara com o código. Se faltar algum (inclusive se o `exiftool` não estiver instalado), o lote vai para **erro** ("EXIF conferido em 6 de 48"), a não ser que `entrada_exigir_exif` esteja desligado.

Log: `gravando metadados em 38 arquivo(s)…`, `conferindo metadados…`, `Metadados gravados em 38 de 38 arquivo(s)`.

### Passo 13 — Quarentena do original

Com `apagar_original_apos_pronto` (padrão ligado):

- **Com `quarentena_entrada`** (padrão): o original vai para `100 - Scanners/_conferidos/AAAA-MM-DD/<caminho do lote>/<arquivo>` (sufixo `.HHMMSS` se o nome já existir) e é anotado em `_conferidos/indice.jsonl`. Livro: `quarentena`.
- **Sem quarentena:** o original é apagado na hora. Livro: `apagado_original`.
- Só sai o que foi copiado e conferido **e não mudou** desde a cópia (SHA de novo).
- Os marcadores (`manifesto.json`, `info_projeto.json`, `status.json`, `status_mac.json`) só saem se não sobrou nenhum arquivo não suportado. Pastas vazias do lote são apagadas.

**Esvaziar a quarentena** (a cada rodada, `quarentena.limpar`): um arquivo só é apagado de vez quando:
- a **réplica confirmou**: o arquivo `<caminho_replica>/_campvision/sentinela.json` tem data igual ou posterior à da quarentena; **e**
- passaram `quarentena_dias` (7), **ou** o disco passou de 85% de uso.

Sem `caminho_replica` configurado, **nada é apagado** da quarentena, e o heartbeat avisa "quarentena sem réplica: X GB". O instalador não configura a réplica ([pendências](#15-pendências-e-riscos-conhecidos)).

Log: `Lote … → F009-P0060 pronto: 19 documento(s), 38 copiado(s), 38 apagado(s)` ("apagado" = saiu da entrada).

### Se algo falha no meio

| Onde falhou | O que fica |
|---|---|
| Contexto (fundo, nome) | erro só no livro e no `entrada.jsonl`; nada copiado |
| Disco/rede (QNAP) | rodada interrompida, nada apagado, tenta de novo |
| Cópia, leitura, decisão (pasta provisória) | `status.json` = `erro` na pasta P9xxx, `catalogacao_ERRO.txt`, `erros.json`; **o painel não fica sabendo** (código provisório) |
| Conclusão (pasta definitiva) | `status.json` = `erro` com aviso ao painel |

Em todos os casos, **nada é apagado da entrada**. O lote não volta sozinho enquanto os arquivos não mudarem; depois de corrigir, use `--refazer-lote`. A exceção é o corte por parada do serviço entre a cópia e a leitura, que volta sozinho.

### O livro de registro

`ACERVOS_CAMP/_campvision/registro/AAAA-MM.jsonl` e `AAAA-MM.csv` (UTF-8 com BOM). Uma linha por ação por arquivo, **só acrescenta**; correção é linha nova.

- Colunas: `quando, acao, lote_id, codigo_projeto, codigo_documento, arquivo_origem, arquivo_destino, nome_original, tamanho, sha256, operador, estacao, versao_cv2, detalhe`.
- Ações escritas: `copiado`, `lido`, `exif_gravado`, `refeito` (renomeação, mudança de projeto, giro de JPG, JPG gerado), `quarentena`, `apagado_original`, `relido`, `releitura_cancelada`, `decisao`, `erro`, `legado`. (`recebido`, `conferido` e `renomeado` existem na lista mas não são usadas.)
- O que acontece na pasta provisória vai primeiro para `catalogacao/livro_adiado.jsonl` e entra no livro já com o código final.
- Se o QNAP estiver fora, as linhas ficam em `~/.campvision2/registro_reserva/` e são enviadas depois.
- `--historico TERMO` procura no livro por nome, código, lote ou data.

---

## 5. Como o vigia decide cada coisa

### 5.1 Contexto: fundo, série, código, nome

| Campo | Fontes, em ordem | Observações |
|---|---|---|
| **Fundo** | `fundo_codigo`/`fundo` do manifesto ou info **e** a primeira parte do caminho que a tabela reconhece | Tabela: painel → cache `~/.campvision2/fundos.json` → tabela embutida (F001–F030). Reconhece código, sigla ou nome. Se só uma fonte identifica, vale ela; se as duas identificam fundos diferentes, erro "conflito de fundo". Sem nenhuma: erro "não achei o fundo" ou "fundo 'X' não está na tabela". |
| **Série** | 1) `serie` ou `tipo_material` do manifesto; 2) pasta de material no caminho até o lote (`03 - Fotografias`, `S01 Pranchas`…); 3) `series` do `info_lote` do Mac ou pastas de material **dentro** do lote — só se for **uma única**; 4) deduzida | Deduzida = S03 se `tipo_estacao`/`estacao` contém foto, vuescan ou camera; senão `serie_padrao` (S01). Deduzida passa pela triagem e pela rede de segurança. |
| **Código** | 1) `codigo`/`projeto_codigo` completo no manifesto/info; 2) Mac com `projeto_ja_existia: true` → `codigo_fundo`-`numero_projeto`; 3) `F0xx-P0xxx` no caminho | Um P sozinho ("P0001", "P0001 - Nome") **não é oficial** (decisão do Rafa, 09/10): vira pista `p_estacao` e vai ao painel como `identificacao_original: "P0001 - Nome"`. Na prática o painel reaproveita o projeto existente com esse P no fundo. |
| **Nome** | `nome`/`titulo` → `projeto` do info → `projeto` do manifesto → pasta mais funda (que não seja fundo nem material) | Tira prefixo de código e ano final; limita a 90 caracteres. Sem nome e sem código: erro. |
| **Ano** | `ano` do manifesto/info → padrão Windows → ano no nome | |
| **Operador, estação** | manifesto/info | Não vem do `info_lote` do Mac. |
| **Lote** | `lote_id`/`lote`, senão data-hora + as 3 últimas pastas | |
| **Teste** | `teste: true` (ou "true") ou as palavras "teste"/"test" no caminho | Lote de teste nunca é publicável; o painel ignora projeto de teste. |

### 5.2 Série

Séries: S01 Desenhos e pranchas · S02 Documentos textuais · S03 Fotografias · S04 Negativos · S05 Slides · S06 Materiais e especificações · S07 Publicações · S99 Não identificado.

Quando a estação não diz a série (5.1, item 4), três etapas corrigem:

1. **Triagem antes da leitura** (`triagem.py`), por sinais da imagem de leitura:
   - papel claro < 25%, variação de tons > 25 e texto do OCR < 12 → **S03** (foto);
   - papel claro ≥ 45%: folha em pé com proporção de A4/ofício (1,35–1,70) e texto ≥ 60 → **S02**; em pé com texto ≥ 24 → série padrão, **incerta**; senão → **S01**;
   - o resto → série padrão, **incerta** ("sinais misturados").
   - Limite: foto de arquitetura clara (céu, parede branca) tem muito "papel claro" e vira S01.
2. **Rede de segurança na leitura:** prancha sem carimbo que o modelo diz ser foto → relida como foto e renomeada para S03.
3. **Na conclusão:** S01 que o modelo leu como documento textual → S02.

A série incerta aparece no pacote (`serie_incerta`), no CSV e no relatório.

### 5.3 Projeto e código

**Agrupar pelo carimbo** (`identidade.py`). As folhas lidas são agrupadas:
- obra parecida (≥ 0,6 de semelhança nas palavras que distinguem); se a obra é genérica ou ilegível, endereço parecido (≥ 0,5);
- mesmo contribuinte ou alvará une grupos;
- palavras genéricas (residência, edifício, rua…) não distinguem;
- grupos de 1–2 folhas parecidos com um grupo maior são unidos, a não ser que a base mande para outro lugar;
- **capas, envelopes e tiras** (palavras como capa, envelope, contracapa, pasta, invólucro, rótulo, etiqueta; proporção ≥ 3 sem carimbo; só código manuscrito) não são pranchas;
- folha sem obra, local nem identificador = **sem identificação**.

**Casar com a base do fundo** (lista de projetos de `/api/estacoes/contexto?fundo=`):
- uma palavra que aparece em mais de 2 projetos do fundo não basta sozinha;
- o nome inteiro de um projeto da base com palavra rara é forte mesmo com endereço diferente;
- o local decide entre projetos de mesmo nome;
- no máximo 5 candidatos.

**Decisão, grupo a grupo** (`entrada._planejar`):

| Situação | O que acontece |
|---|---|
| Código informado pela estação **e o lote é um grupo só** | Usa o código. Se o carimbo aponta com força **outro** projeto, pergunta ao painel com os dois. |
| Lote com vários grupos | O código da estação é ignorado; cada grupo segue as regras abaixo. |
| Um candidato forte e nenhuma dúvida | Usa o código dele (material entra no projeto existente). |
| Obras diferentes no mesmo endereço, ou escritórios diferentes em décadas diferentes (≥ 5 anos) | Pergunta ao painel ("projeto separado ou fase?") mesmo com um candidato. |
| Dois ou mais candidatos | `POST /reservar` com `candidatos`: o painel abre a decisão "projeto parecido". |
| Nenhum candidato (ou um fraco) | `POST /reservar` normal. |
| Lote com vários grupos: grupo com menos de 2 folhas e sem projeto forte | Vira "sem identificação". |
| Nada legível | O nome da pasta é a única pista. |

Sobre o `/reservar`:
- manda `fundo_codigo`, `titulo` (o que o carimbo diz), `chave_reserva` (fixa por fundo + nome, guardada em `~/.campvision2/reservas.json`: repetir é seguro), `ano`, `cidade`, `identificacao_original`, `operador`, `proximo_p_local` e um resumo do carimbo;
- resposta 202 = decisão pendente: o lote **espera**; resposta com código = segue ("decidido no painel" ou "projeto novo reservado no painel");
- se o painel devolve um código que já é **outro** projeto no acervo, o lote espera com "corrigir a numeração no painel" (CV-27).

Depois: grupos com o mesmo destino são unidos; capas, folhas sem identificação e o que sobrou vão com o projeto principal (o maior), **fora da publicação**.

### 5.4 Orientação e espelho

**OCR** (`preparo.py`): tesseract `por+eng`, `--psm 11`, numa versão de 1400 px em cinza, nas 4 rotações. Pontuação: +3 por palavra do vocabulário de prancha, +1 por número ou palavra plausível.
- **Incerta** se o melhor tem menos de 12 pontos ou o segundo está a menos de 15% dele. Sem tesseract, tudo fica incerto.
- **Espelho:** a versão espelhada é sempre testada; só é considerada espelhada com ≥ 24 pontos **e** o dobro da normal; com ≥ 12 e metade da normal, fica incerta.
- O melhor chute é aplicado à imagem de leitura e ao preview **mesmo quando incerto**.

**JPG do acervo:** só é girado/desespelhado quando a orientação está **certa** (`derivados.orientar_jpg`): sem perda com `jpegtran -perfect` quando o arquivo não tem etiqueta de orientação; senão regravado em JPEG 95, 4:4:4, com o perfil de cor e as etiquetas copiadas de volta. O master nunca é tocado. `preparo.json` marca `jpg_orientado`.

**Fotos** (`derivados.orientar_pela_foto`): foto quase nunca tem texto, então o OCR fica incerto. A leitura de fotografia devolve `rotacao` (graus no sentido horário, relativo à imagem que o modelo viu) e `orientacao_clara`. Quando o OCR estava incerto, sem espelho e o JPG ainda não girado, **e** `orientacao_clara`:
- o giro final é o chute do OCR **somado** ao giro do modelo;
- imagem de leitura e preview são giradas pelo giro do modelo;
- o JPG do acervo recebe o giro final;
- a foto deixa de ser "incerta" (`orientacao_origem: leitura da fotografia`).

Em dúvida, nada muda e a foto continua para conferir. O painel usa `orientacao_corrigida` (JPG já girado) e `rotacao_aplicada` para mostrar a imagem certa.

### 5.5 Título, publicação e retirados

**Título:**
- prancha: título lido no carimbo;
- foto: legenda proposta pelo modelo (com nomes das chaves de identidade na frente, se houver);
- textual: "<Tipo documental> — <assunto>" e "(remetente, data)" nas cartas;
- **título para publicar** (`titulo_publicacao`): tira nome do projeto e da pasta e passa texto todo em maiúsculas para frase normal.

- (autoria divergente deixou de bloquear no contrato 2.4: vira ressalva);
- autoria divergente;
- projeto divergente ("o carimbo diz '…'");
- sem identificação (pasta com várias obras);
- capa ("não é prancha");
- duplicata de outro documento;
- ficha de documentação em S01;
- lote de teste;
- retirado por decisão humana.

**`retirados`** (fora de `documentos` no pacote): decisão "retirar", e problemas de identificação (projeto divergente, sem identificação, capa) — estes voltam com uma decisão "publicar". Duplicata, ficha e teste ficam em `documentos` com `publicavel: false`. Ressalvas e "fora do período" nunca bloqueiam.

**Decisões humanas:** `vigia.py --decisao CODIGO --acao retirar|publicar|nota --motivo "…" --por NOME` grava em `catalogacao/decisoes.json` (vale a mais recente) e no livro.

---

## 6. A leitura com o modelo, em detalhe

### 6.1 A chamada

- Modelo: `modelo` no config, padrão **`claude-sonnet-5`**. Opções conhecidas na tabela de preços: sonnet-5, opus-5, haiku-4-5.
- Imagem enviada: 2000 px, JPEG 78 (`visao.py:29-30`).
- Uma ferramenta obrigatória por modo (`tool_choice`), `max_tokens` 4000, timeout 120 s, até 4 tentativas com espera crescente (até 30 s) e um pouco de aleatoriedade.
- 4 documentos em paralelo (`trabalhadores`). Cancelar descarta o que ainda não começou.
- Cada leitura guarda a versão do CV2, o modelo e a **versão do prompt** (8 primeiros caracteres do SHA-1 do texto do prompt). Mudou o prompt, muda a versão.
- **Custo** é estimado com a tabela fixa em `config.py` (por milhão de tokens: sonnet-5 US$ 2 entrada / US$ 10 saída; opus-5 5/25; haiku-4-5 1/5). Modelo fora da tabela mostra custo 0.

> As chaves de config `confianca_minima_para_aceitar`, `usar_cache_de_regiao`, `falhas_de_cache_toleradas`, `confianca_para_guardar_regiao`, `ganho_minimo_2o_passe`, `margem_recorte`, `lado_maximo_envio`, `qualidade_jpeg_envio` e `aplicar_automaticamente` **não têm efeito** nesta versão: são do método antigo (segundo passe e recorte do carimbo). A leitura é **uma chamada por folha**, inteira. A única segunda chamada é a rede de segurança de foto.

### 6.2 O que o modelo devolve, por modo

**Prancha** (`registrar_prancha`, `esquema.py`):
- `legivel`, `tem_carimbo`, `e_fotografia`;
- os campos: projeto, cliente, arquiteto, escritorio, endereco, cidade, uf, data, escala, titulo_prancha, folha, total_folhas, codigo_serie, codigo_unidade, revisao. Cada campo vem como `{valor, confianca 0–1, alternativas, onde}`, com `onde` ∈ carimbo, margem, contorno, manuscrito, legenda, corpo;
- `transcricao_integral`, `materiais_citados`, `anotacoes_manuscritas`, `observacoes_de_leitura`.

Regras do prompt: transcrever literal (inclusive erros e abreviações); campo ilegível = null; nunca preencher por plausibilidade nem conhecimento externo; sempre dar a alternativa em dígito ambíguo (3/5/8, 1/7, 0/6); arquiteto (pessoa) ≠ escritório (empresa); não descrever nem datar o projeto; se é foto, `e_fotografia: true`.

**Fotografia** (`registrar_fotografia`):
- `tipo_de_imagem` (fotografia, negativo, verso, cartela, folha_de_contato), `assunto`, `enquadramento` (externa, interna, detalhe, aérea, maquete, pessoas), `elementos_visiveis`, `texto_na_imagem`, `legenda_proposta`, `confianca`;
- `pessoas`: só traços (posição, barba, bigode, óculos, outros), **nunca nome**;
- `rotacao` (0/90/180/270) e `orientacao_clara`.

Regras: só o visível; não nomear pessoas nem autor; não dizer onde foi tirada sem indicação visual inequívoca; não estimar data, estilo nem autoria. O tipo vira "Fotografia" e a transcrição é o texto da imagem.

**Textual** (`registrar_documento`, série S02): `tipo_documental` (carta, memorial, programa, planilha, ficha, contrato, orçamento, anotação, outro), remetente, destinatário, data, ano (só com 4 dígitos), assunto, projeto citado, transcrição integral, legível, confiança. O tipo vira "Documento textual" e o projeto citado vai para `projeto`.

### 6.3 Imagens que não abrem e limites

- O limite de pixels do Pillow é desligado, JPG truncado é lido assim mesmo, TIFF de várias páginas usa a primeira.
- 16 bits é reescalado para 8, transparência vira branco, perfil de cor é convertido para sRGB.

### 6.4 Depois da leitura: consolidação, pistas e grafias

**Consolidação por grupo** (`grupos.py`, só código, sem API):
- grupo = `codigo_unidade` (ou projeto) + revisão (R-n também é tirado do código de série ou do título). Abreviações expandidas antes de comparar (MAL→marechal, AV→avenida, R→rua…). Nomes casam se um contém o outro ou com semelhança ≥ 0,82. Revisões diferentes nunca se juntam;
- campos do projeto (projeto, cliente, escritório, endereço, cidade, UF): vence o valor que aparece 2+ vezes (grafia mais frequente; empate, a mais longa). Folha discordante ganha `outliers` e guarda o original em `lidos_originais`. **O arquiteto nunca é sobrescrito**;
- endereço: variantes guardadas; números ou ruas diferentes = conflito;
- ano do grupo: o mais frequente (2+ vezes) ou o único. Folha 2+ anos fora vira `data_outlier` com `data_sugerida` e ressalva. `data_iso` normalizada (`14.10.83` → `1983-10-14`).

**Pistas da pasta e do info** (`caminho.py`): projeto, ano e fundo tirados do caminho. Prioridade: carimbo > `info_projeto.json` > pasta. Campo vazio é preenchido com confiança 0,5 (`campos_da_pasta`); valor que discorda vira `divergencias`. A pasta **nunca** é mostrada ao modelo.

**Grafias congeladas por fundo** (`projeto.py`): arquiteto, escritório, cidade e cliente congelam depois de 5 confirmações, em `~/.campvision2/canonicas/F0xx.json`. Leitura nova com semelhança ≥ 0,88 é trocada pela grafia congelada, com ressalva.

**Campos do projeto por fonte** (`fontes_e_lacunas`): cada campo guarda o valor de cada fonte (pasta, info_projeto, carimbo). Anos que diferem mais de 2, ou outros campos com semelhança < 0,8, ficam **vazios** com o motivo em `lacunas`.

### 6.5 Regras por código depois da leitura

`derivacao.py`:
- **Ano** só da data escrita (ano de 4 dígitos, ou `dd.mm.aa` → 19aa). O modelo não dá o ano em prancha.
- **Tipo de desenho** pelo título, com 15 detectores nesta ordem: Implantação, Planta, Corte, Elevação, Fachada, Detalhe, Estrutura, Instalações, Mobiliário, Perspectiva, Croqui, Fotografia, Levantamento, Documento, Tabela. No máximo 3, juntos com " / ". Implantação suprime Planta.
- **"É documento, não obra"**: título com documento, currículo, caderno, pasta, recorte, correspondência, memorial, contrato, ofício. Em S01 bloqueia a publicação (ficha) e impede sugestão de programa.
- **Autoria divergente:** nomes com 4+ letras (sem títulos e palavras vazias) comparados com titular e coautores. Arquiteto vazio nunca dispara.
- **Chaves de identidade** (fotos): uma pessoa só é nomeada quando os traços (barba, bigode, óculos) batem com **exatamente uma** chave escrita por gente no fundo. "só", "apenas" e "sem" tornam explícito o que não está. Qualquer ambiguidade = ninguém nomeado. O nome vai na frente da legenda, com ressalva.
- **Fora do período:** ano fora do `periodo_atuacao` do fundo.

`projeto.py`:
- **Sugestões de programa e natureza** (`programa.py`): cerca de 100 regras sobre o título do projeto (da mais específica para a mais genérica) dão o programa (taxonomia 9947); 8 regras dão a natureza (taxonomia 9952; padrão "obra nova" quando houve programa). Sempre **sugestão**: quem grava é gente. Liberado só com ≥ 85% de acerto no `--backtest-programa`.
- **Propostas:** obras distintas na mesma pasta (pelo código de unidade) e fotos sem obra agrupadas por semelhança visual → `propostas.json`.
- **Capa sugerida:** Perspectiva > Fachada > Elevação > Implantação em S01, senão a primeira S01, senão a primeira S03; nunca duplicata nem autoria divergente.
- **Fotógrafo:** do `info_projeto.json` (`fotografo`, `credito_fotografia`), senão "fotógrafo não identificado". Nunca do modelo.

---

## 7. O que fica no acervo

### 7.1 Estrutura de pastas

```
ACERVOS_CAMP/
  F009 - EOL Euclides Oliveira/                ← fundo (reaproveita pasta que começa com o código)
    README.md
    00 - Em identificação/                     ← provisório
      F009-P9001 - SESI — Marília/             ← some depois da mudança para o projeto
        .unidade_cv2  status.json  catalogacao/ …
    01 - Projetos/
      F009-P0060 - SESI — Marília/             ← "<código> - <nome>", até 110 caracteres
        README.md
        info_projeto.json
        status.json
        campvision2_checkpoint.jsonl
        01 - Desenhos e pranchas/  02 - Documentos textuais/  03 - Fotografias/
        04 - Negativos/  05 - Slides/  06 - Materiais e especificações/
        07 - Publicações/  99 - Não identificado/
          TIF/ JPG/ PNG/ DNG/ NEF/ PDF/ CDR/ PLT/   ← por formato
            F009-P0060-1984-S03-D00001.tif
        catalogacao/
    02 - Obras e documentação de obra/ 03 - Fotografias/ 04 - Negativos/ 05 - Slides/
    06 - Desenhos e pranchas/ 07 - Documentos textuais/ 08 - Materiais e especificações/
    09 - Publicações/ 99 - Não identificado/   ← modelo de pastas do fundo
  _campvision/
    preview/<F0xx-P0xxx>/<código>.jpg          ← 3000 px, orientado, para o site
    registro/AAAA-MM.jsonl, AAAA-MM.csv        ← livro de registro
    sentinela.json                             ← prova para a réplica
    fundos/F0xx.json                           ← parâmetros por fundo (escritos por gente)
```

Nome de documento: `F0xx-P0xxx-AAAA-S0x-DNNNNN` (fundo, projeto, ano, série, documento). O D é sequencial no projeto, contínuo entre séries.

### 7.2 A pasta `catalogacao/`

| Arquivo | Conteúdo |
|---|---|
| `leituras.json` | a leitura de cada documento (fonte para remontar tudo sem API) |
| `catalogacao.csv` | uma linha por documento ([7.4](#74-catalogacaocsv)) |
| `pacote_tainacan.json` | o que o painel importa ([7.3](#73-pacote_tainacanjson)) |
| `erros.json` | erros por arquivo ([7.5](#75-erros-relatório-e-aceite)) |
| `contatos.jpg` | folha de contatos: 6 colunas de miniaturas de 300 px, até 1000 documentos |
| `relatorio.txt` | relatório do lote em 8 partes |
| `orientacao.txt` | giro aplicado, folha por folha |
| `preparo.json` | orientação, hashes, triagem, identidade |
| `mapa_origem.json` | arquivo final ↔ arquivo original (pasta e nome), lote, formato |
| `identificacao.json` / `.txt` | grupos pelo carimbo e destino de cada folha |
| `identificacao/<lote>/` | catalogação da pasta provisória daquele lote |
| `propostas.json` | obras distintas e fotos sem obra (só se houver) |
| `decisoes.json` | decisões humanas (`--decisao`) |
| `reidentificacao.json` | plano do `--reidentificar` |
| `lotes/<lote>.json` | histórico de cada recebimento: arquivos, contagens, EXIF, `execucao`, `aceite` |
| `releituras/<data>/` | antes × depois de cada releitura |
| `catalogacao_ERRO.txt` | só quando o lote falhou |
| `livro_adiado.jsonl` | temporário, na pasta provisória |

### 7.3 `pacote_tainacan.json`

Versão 2. Topo: `versao, gerado_em, teste, projeto_codigo, projeto_nome, fundo_codigo`, `fundo` (código, sigla, nome, coautores, período), `projeto` (código, título, ano, cidade, UF, cliente, endereço, `fontes`, `lacunas`, `programa_sugerido`, `natureza_sugerida`, `sugestao`, `capa_sugerida`, `descricao_sugerida`), `documentos` (e `itens`, nome antigo), `retirados`, `propostas`, `avisos`, `execucao` (versão, contrato, modelo, prompts, chamadas, tokens, custo, tempo).

Por documento: `codigo, projeto_codigo, fundo_codigo, serie, modo, titulo, titulo_lido, tipo, tipo_de_desenho, folha, escala, codigo_unidade, revisao, data_lida, data_iso, data_sugerida, data_outlier, ano, ano_do_projeto, metadados, rastreio, alternativas, onde, transcricao_integral, materiais_citados, foto, textual, pessoas_identificadas, fotografo, credito, preview, arquivos, arquivo, arquivo_origem, origem_formato, rotacao_aplicada, confianca_rotacao, espelhada, orientacao_incerta, orientacao_corrigida, serie_incerta, duplicata_de, tipo_duplicata, autoria_divergente, projeto_divergente, fora_do_periodo, prancha_original, confianca, decisao, publicavel, bloqueios, ressalvas, versao_cv2, versao_prompt, modelo, carimbo, capa, sem_identificacao, lido`.

### 7.4 `catalogacao.csv`

UTF-8 com BOM, vírgula, ordenado por grupo e folha. Colunas: Código, Arquivo, OK?, Revisar, Campos a revisar, Grupo/Projeto, Carimbo?, Confiança média; os campos lidos; Fundo/Projeto/Ano (pasta) e Divergência; Modo, Autoria divergente, Duplicata de, Tipo de duplicata, Rotação aplicada, Espelhada, Orientação incerta, Outliers, Ressalvas, Documento (não obra), Materiais citados, Anotações manuscritas, Onde, Alternativas, Transcrição integral, md5, Hash perceptual, Arquivo de origem, Título para publicar, Data lida, Data ISO, Data sugerida, Confiança da rotação, Série incerta, Variantes de endereço; Rotação, Passes, Nota da IA, Erro; "conf. <campo>" e "lido: <campo>".

**Revisar = sim** quando: erro; prancha sem carimbo; projeto ou autoria divergente; duplicata; orientação incerta; outliers ou ressalvas; algum campo preenchido com confiança < 0,60.

### 7.5 Erros, relatório e aceite

**`erros.json`:** categorias metadado, autoria divergente, duplicata, projeto errado, série, espelhado, orientação, arquivo corrompido; gravidade bloqueia, corrigir ou aviso. Autoria divergente é `corrigir` (contrato 2.4): a folha fica em `documentos`, publicável, com a ressalva "autoria não confirmada: <o que o carimbo diz>".

**`relatorio.txt`**, sempre nas 8 partes: 1) origem × saída; 2) séries; 3) duplicatas; 4) autoria, projeto divergente, sem identificação, capas; 5) o que precisa de gente (orientação, série); 6) folhas faltantes pelo total do carimbo (ex.: "C-3/12"); 7) ressalvas; 8) retirados e não suportados. Termina com a linha de execução (versão, contrato, modelo, chamadas, tokens, custo, tempo, prompts).

**`lotes/<lote>.json` → `aceite`:** `contagem_origem_igual_saida`, `folhas_autoria_nao_confirmada` (contagem, não bloqueia), `orientacao_incerta_zerada`, `serie_incerta_zerada`, `exif_conferido`, `publicavel`, `pendencias`. Quem decide a publicação é o painel.

**`status.json` no "pronto":** `codigo, status, status_em, lote_atual, lotes_prontos, imagens, folhas, com_carimbo, a_revisar, erros_bloqueantes, versao_cv2`. Valores de `status`: `processando`, `aguardando` (só na pasta provisória), `pronto`, `erro`.

### 7.6 Metadados gravados nos arquivos

`metadados.py`. Primeiro limpa título, descrição e palavras-chave antigos (XMP, EXIF, IPTC, PDF). Depois grava:

| Conteúdo | Etiquetas |
|---|---|
| Autor (arquiteto lido, senão escritório, senão nome do fundo; nunca o cliente) | `EXIF:Artist`, `XMP-dc:Creator`, `IPTC:By-line` |
| Crédito "Acervo {nome do fundo}/CAMP - Casa da Arquitetura Moderna Paulista" e identidade | `XMP-dc:Publisher`, `XMP-photoshop:Credit`, `XMP-photoshop:Source`, `IPTC:Credit`, `IPTC:Source`, `XMP-xmpRights:WebStatement`, `XMP-iptcCore:CreatorWorkURL`, `XMP-iptcCore:CreatorContactInfoCiEmailWork` |
| Direitos ("autor / CAMP — licença") | `EXIF:Copyright`, `XMP-dc:Rights`, `IPTC:CopyrightNotice`, `XMP-xmpRights:UsageTerms`, `XMP-xmpRights:Marked` |
| Título (título lido, senão tipo, senão projeto) | `XMP-dc:Title`, `IPTC:ObjectName` (64 car.), `XMP-photoshop:Headline` (= projeto) |
| Descrição ("Código: … \| campo: valor …", até 1900 car., + crédito) | `EXIF:ImageDescription`, `XMP-dc:Description`, `IPTC:Caption-Abstract` |
| Data e lugar | `XMP-dc:Date` (ano), `XMP-photoshop:City/State`, `IPTC:City/Province-State` |
| Palavras-chave (projeto, autor, tipo, cidade, ano, instituição) | `XMP-dc:Subject`, `IPTC:Keywords` |
| Código do documento | `XMP-dc:Identifier`, `XMP-photoshop:TransmissionReference`, `IPTC:OriginalTransmissionReference` (32 car.) |
| Nome original | `XMP-xmpMM:PreservedFileName` |
| Quem e quando | `XMP-photoshop:CaptionWriter` ("operador / estação"), `XMP-xmp:MetadataDate`, `XMP-xmp:CreatorTool` ("CAMP Vision 2 (build)") |

A série e o fundo só aparecem dentro do código. `--identidade` mostra o crédito que será gravado; `identidade_site` vazio gera aviso.

---

## 8. Formatos de arquivo

Suportados: jpg, jpeg, png, tif, tiff, dng, nef, pdf, cdr, plt.

| Formato | Como é lido | EXIF |
|---|---|---|
| JPG, PNG, TIF | direto (etiqueta de orientação aplicada ao abrir) | sim |
| DNG, NEF | preview embutido via exiftool; senão `rawpy` em meia resolução; senão como TIFF | DNG sim, NEF não |
| PDF | primeira página via `pdftoppm` ou `pypdfium2` (150 dpi para ler) | sim |
| CDR | LibreOffice converte para PDF; página em branco → o texto (`pdftotext`) vira imagem e também `.txt` | não |
| PLT (HPGL) | desenhado pelo `hpgl.py` numa imagem de 3000 px (sem os textos LB) | não |

**JPG que falta** (`derivados.gerar_jpgs`, usado no `--consertar-acervo`): gerado a partir do master (tif > tiff > dng > nef > png > pdf > cdr > plt), 6000 px, JPEG 95 4:4:4, LANCZOS, sRGB, já orientado, com o metadado do master; entra no `mapa_origem.json` e no livro como `refeito`. Nunca sobrescreve JPG existente.

---

## 9. O painel: o que recebe e o que faz

### 9.1 Chamadas do vigia ao painel

Base `painel_url`, cabeçalho `X-Camp-Token` (de `CAMP_PAINEL_TOKEN`), timeout 8 s. O cliente nunca derruba o vigia.

| Chamada | Quando |
|---|---|
| `GET /api/estacoes/contexto` | ao subir: tabela de fundos (cache em `~/.campvision2/fundos.json`) |
| `GET /api/estacoes/contexto?fundo=F0xx` | ao identificar: coautores e projetos do fundo |
| `POST /api/estacoes/projetos/reservar` | projeto novo ou duvidoso ([5.3](#53-projeto-e-código)) |
| `POST /api/estacoes/heartbeat` | a cada 60 s |
| `POST /api/campvision/aviso` | ver abaixo |
| `GET /api/estacoes/pedidos-releitura` + `POST …/{id}/iniciado` + `POST …/{id}/concluido` | a cada rodada ([9.3](#93-pedidos-de-releitura-ler-dados-agora)) |

**Heartbeat:** `estacao_id, tipo_estacao, app, versao, estado` (vocabulário do painel: vigiando, processando, erro, pasta indisponível; a frase livre ("copiando 7 de 38", "gravando metadados", "relendo", "QNAP com problema") vai em `detalhe.situacao`; alerta de custo em `detalhe.alerta`), `projeto, progresso, fila, hoje` (projetos, imagens, erros, custo), `montagens` (entrada, acervo), `contrato, quarentena`. Heartbeat perdido não é guardado, mas a falha agora é logada (WARNING).

**Aviso** = `{codigo, pasta, status}`. É mandado em toda gravação de `status.json` de projeto definitivo (processando, pronto, erro), no fim de cada releitura, no `--avisar-painel`, no `--consertar-acervo` e no `--reidentificar --sim`. Nunca para código provisório. Se o painel não responder, o aviso fica em `~/.campvision2/painel_pendentes.json` (só o último de cada projeto) e é reenviado depois de cada heartbeat.

### 9.2 O que o painel faz com o aviso

`camp-painel`, `rotas_operacao.ler_pasta_do_aviso` e `importador_lote.importar_lote`:

1. Responde **202 sempre**, mesmo quando ignora a pasta (o vigia não fica sabendo).
2. Ignora: pasta fora da raiz final; pasta da entrada bruta; **projeto de teste**; subpasta "NN - Arquivo Arquivístico"; pasta sem código de projeto cadastrado; pasta com código diferente do aviso.
3. Um lote do painel por pasta (`pasta_qnap`). Pasta renomeada vira lote novo.
4. Etapa pelo `status`: pronto (e campvision_concluido, sem status) → **revisão**; processando (e enviado_windows) → processando; erro → erro; desconhecido → revisão com aviso. Um aviso "processando" devolve o lote para processando, a não ser que esteja publicado ou em rascunho.
5. **Importa as folhas** só se a etapa é revisão, existe pacote e **o lote do painel não foi aprovado**.
6. Na importação:
   - só aceita séries **S01 a S06** (S07 e S99 são recusadas como problema);
   - **não mexe** em folha já revisada por uma pessoa nem em folha no site;
   - número D usado por **outro** lote → abre a decisão "lote colide" (adicionar ao fim, ignorar ou substituir);
   - número D já usado por outra folha **do mesmo lote** (ex.: a folha mudou de série, S01 → S03) → **recusa** ("o número D… já é de …");
   - `orientacao_corrigida` zera giro e espelho; `publicavel` define `nao_publicado` ou `bloqueado`.
7. Etapas do painel: enviado, processando, revisão, rascunho, publicado, erro. Para ir a rascunho ou publicado: projeto autorizado para o site, sem bloqueios, perfil admin e lote aprovado.

No `/reservar`, o painel reaproveita o projeto existente quando `identificacao_original` começa com "P0xxx - " daquele fundo; responde 202 para projeto parecido ou com `candidatos`; 410 para pedido descartado (o vigia trata como falha e o lote fica aguardando).

### 9.3 Pedidos de releitura ("Ler dados agora")

1. `GET /api/estacoes/pedidos-releitura?estacao=campvision2`.
2. Para cada pedido, `POST …/{id}/iniciado`. **409** = cancelado no painel: não lê, não gasta, livro `releitura_cancelada`. 404 ou painel fora = lê assim mesmo.
3. Relê o escopo pedido (`folha`/documentos ou `projeto`; padrão `vazios`).
4. `POST …/{id}/concluido` com o resumo, e aviso "pronto".

Estado durante a releitura: "relendo".

---

## 10. O servidor

### 10.1 Instalação (`instalar.sh`)

Antes: IP fixo 192.168.15.40 via netplan e `git clone https://github.com/mullets/campvision-new.git ~/campvision-new` (README, "Servidor da CAMP"). Depois, como o usuário do serviço: `cd ~/campvision-new && sudo ./instalar.sh`. Pode rodar de novo sem estragar nada. Para rodar sem perguntas, defina `QNAP_IP`, `QNAP_COMPARTILHAMENTO`, `QNAP_USUARIO`, `QNAP_SENHA`, `ANTHROPIC_API_KEY`, `CAMP_PAINEL_URL`, `CAMP_PAINEL_TOKEN`, `GITHUB_TOKEN`.

1. **Pacotes:** python3 (≥ 3.10), venv, pip, git, exiftool, poppler-utils, cifs-utils, smbclient, ping, curl, tesseract-ocr e tesseract-ocr-por.
2. **Tampa e suspensão:** fechar a tampa não suspende; sleep/suspend/hibernate mascarados.
3. **QNAP:** credenciais em `/etc/camp-qnap.cred` (600, root; para trocar a senha, apague e rode de novo); linha no `/etc/fstab` (cifs, automount, `nofail`); montado em `/mnt/camp/backup`. Padrões: IP 192.168.15.30, compartilhamento "Backup Servidor CAMP". Entrada: `/mnt/camp/backup/Arquivos/100 - Scanners`. Acervo: `/mnt/camp/backup/Fundos e Escritorios/ACERVOS_CAMP`. Testa escrita; se falhar, mostra ping, porta 445 e smbclient.
4. **`.venv`** no repositório.
5. **Chaves:** `~/.campvision2/ambiente` (600) com `ANTHROPIC_API_KEY` e `CAMP_PAINEL_TOKEN`; `~/.git-credentials` com o token do GitHub (sem ele, a auto-atualização só funciona com repositório público).
6. **Config:** `--criar-config`, `--entrada`, `--acervo`, `--painel` (padrão `http://192.168.15.60:8000`); `identidade_site` = `https://camp.arq.br`; `formatos_saida` = csv. **Não configura `caminho_replica`.**
7. **Serviço** `/etc/systemd/system/campvision2.service` e `/etc/sudoers.d/campvision2` (o usuário pode reiniciar, parar e iniciar o serviço sem senha).
8. **Tela do notebook** (`scripts/tela_e_atalhos.sh`): login automático no tty1 (log ao vivo) e tty2 (Ctrl+Alt+F2, `vigia.py --monitor`); tela sem apagar; Ctrl+C ignorado nessas telas. Atalhos no `~/.bashrc`: `cvlog`, `cvtela`, `cvlotes`, `cvhist`, `cvatualizar`, `cvstatus`.
9. **Conferência final:** serviço ativo, `estado.json` existe, painel responde com o token, IP .40.

### 10.2 O serviço

`campvision2.service`: depois da rede e das montagens; usuário do serviço; lê `~/.campvision2/ambiente`; roda `vigia.py --sem-painel` (`--sem-painel` = log corrido, sem a tela redesenhada; **não** desliga o painel web); `Restart=always` (volta em 15 s se cair); ao parar, manda SIGTERM e espera até **180 s** antes de matar.

Ao receber SIGTERM/Ctrl+C, o vigia termina as leituras em andamento, não começa outro lote e sai ("Vigia encerrado."). Cópia e EXIF não são interrompidos no meio; se passarem de 180 s, o systemd mata o processo.

```bash
systemctl status campvision2
journalctl -u campvision2 -f              # log ao vivo
journalctl -u campvision2 --since 16:40   # desde um horário
.venv/bin/python vigia.py --monitor       # tela do servidor (também no tty2)
```

O log também vai para `~/.campvision2/vigia.log` (nunca é rotacionado).

**Ver o que o vigia está fazendo agora** (o log pode ficar parado por minutos em TIFF grande):

```bash
python3 -c "import json,pathlib; d=json.loads((pathlib.Path.home()/'.campvision2'/'estado.json').read_text()); print(d['situacao'], '|', d['projeto_atual'], '| fila', d['fila'], '|', d['gravado_em'])"
```

`gravado_em` antigo (mais de 2 min) = serviço parado ou travado.

### 10.3 Atualizar (`atualizar.sh`)

| Comando | O que faz |
|---|---|
| `./atualizar.sh` | espera o vigia ficar `vigiando` (qualquer lote em andamento, inclusive copiando/preparando/gravando metadados; aborta após 1 h), puxa o código, instala dependências, faz backup, roda os testes, reinicia, abre o log |
| `./atualizar.sh --agora` | não espera o lote |
| `./atualizar.sh --voltar` | volta para a versão anterior **e fixa** nela (pausa a auto-atualização até a próxima atualização manual); não roda testes |
| `./atualizar.sh --versao X` | vai para um commit ou tag e fixa |
| `./atualizar.sh --sem-log` | não abre o log no fim |
| `./atualizar.sh --auto` | usado pelo próprio vigia: não reinicia o systemd |

Detalhes:
- Puxa do branch que o repositório acompanha (`@{u}`, no servidor o `main`), só avanço direto. Recusa se houver mudança local em arquivo versionado.
- Instala também `rawpy` e os pacotes de sistema que faltarem (exiftool, poppler, cifs, tesseract, **jpegtran**).
- Backup em `~/.campvision2/backups/estado-<data>.tgz` (sem `leitura/` e `backups/`; guarda os 10 últimos). Testes em `/tmp/campvision2-testes.log`.
- **Se pip, pacotes, testes ou o reinício falharem, volta sozinho** para a versão anterior.
- O log ao vivo só abre num terminal; **Ctrl+C para sair**. Comandos colados depois só rodam quando o log fecha.
- Saída: 0 atualizado ou já em dia; 1 falhou (e voltou); 2 opção inválida; 3 já em dia ou fixado (só `--auto`).

**Auto-atualização:** com `auto_atualizar` (padrão ligado), o vigia roda `atualizar.sh --auto` na subida e depois no máximo a cada 60 min, sempre entre lotes. Se atualizou, reinicia a si mesmo no lugar (mesmo PID). Fica pausada enquanto existir `~/.campvision2/versao_fixada`. Como o `--auto` não pode instalar pacotes de sistema (o sudo só permite o systemctl), uma versão que precise de pacote novo volta sozinha até alguém rodar `./atualizar.sh` à mão.

O build aparece no log ao subir: `CAMP Vision 2 build 2026-10-14-05`.

### 10.4 Estado local (`~/.campvision2/`)

| Arquivo | O que guarda |
|---|---|
| `config.json` | configuração ([seção 12](#12-configuração-todas-as-chaves)) |
| `ambiente` | `ANTHROPIC_API_KEY`, `CAMP_PAINEL_TOKEN` (e `CAMPVISION_SMTP_SENHA` se usar e-mail) |
| `estado.json` | o que o vigia está fazendo agora, a cada 5 s |
| `entrada.jsonl` | o que já foi feito com cada lote (pronto/erro com assinatura; aguardando) |
| `reservas.json` | códigos já reservados no painel |
| `fundos.json` | cache da tabela de fundos |
| `canonicas/F0xx.json` | grafias congeladas por fundo |
| `eventos.jsonl` | um evento por projeto processado (números do dia) |
| `relatorios/` | relatórios diário e geral (txt e html) |
| `leitura/`, `leitura_bruta/`, `derivados/` | imagens de trabalho |
| `painel_pendentes.json` | avisos que o painel ainda não recebeu |
| `registro_reserva/` | livro de registro guardado enquanto o QNAP estava fora |
| `vigia.log` | log do serviço |
| `backups/`, `versao_anterior`, `versao_fixada` | atualização |

Na entrada: `100 - Scanners/_conferidos/` (quarentena) e `_conferidos/indice.jsonl`.

### 10.5 Relatório diário e e-mail

Às `hora_relatorio` (18:00), o vigia escreve `relatorios/relatorio-AAAA-MM-DD.txt` e `.html` e regrava `relatorio-geral`. Com `email_ativo`, manda por e-mail (STARTTLS, assunto "CAMP Vision 2 — dd/mm/aaaa", senha em `CAMPVISION_SMTP_SENHA`). Falha no e-mail nunca para o serviço. `--relatorio AAAA-MM-DD` regera (e reenvia) um dia.

### 10.6 Comandos que usam API ou painel no terminal

Precisam das chaves carregadas:

```bash
cd ~/campvision-new
set -a; . ~/.campvision2/ambiente; set +a
.venv/bin/python vigia.py --reler F009-P0060 --escopo projeto
```

### 10.7 Fundo novo ou parâmetros de fundo

A tabela de fundos e os arquivos `ACERVOS_CAMP/_campvision/fundos/F0xx.json` são lidos **uma vez, quando o serviço sobe**. Depois de cadastrar um fundo no painel ou editar esses arquivos, **reinicie o serviço** (ou espere uma auto-atualização).

`fundos/F0xx.json` (modelo em `docs/fundos/MODELO.json`; nome exatamente `F` + 3 dígitos; só vale para fundo que já está na tabela):
- `periodo_atuacao` `{de, ate}` → "fora do período";
- `fundos_relacionados[].nome` → coautores (autoria divergente);
- `chaves_de_identidade` `[{pessoa, sinal}]` → nomes em fotos;
- `observacoes`.

---

## 11. Comandos

### 11.1 `vigia.py` (servidor)

Todos com `~/campvision-new/.venv/bin/python vigia.py` na frente.

**Serviço e acompanhamento**

| Comando | O que faz |
|---|---|
| (sem opção) | roda o serviço; num terminal, tela ao vivo |
| `--sem-painel` | sem a tela redesenhada, log corrido (é assim que o serviço roda) |
| `--uma-vez` | uma rodada e sai (sem heartbeat nem relatório) |
| `--intervalo N` | pausa entre rodadas só nesta execução |
| `--sem-auto-atualizar` | desliga a auto-atualização só nesta execução |
| `--monitor` | tela do servidor: estado a cada 2 s, avisa "serviço parado?" se o `estado.json` tem mais de 2 min |
| `--lotes` | lotes do `100 - Scanners` e em que pé estão |
| `--historico TERMO` | procura no livro (nome original, código, lote ou data) |
| `--diagnostico [F0xx]` | por projeto: documentos, lidos, vazios, sem JPG, erro, carimbo, pacote; quarentena e réplica |
| `--proximo-p F0xx` | maior P no acervo local e o próximo (o oficial vem do painel) |

**Refazer e consertar**

| Comando | O que faz | API? |
|---|---|---|
| `--refazer-lote TEXTO` | devolve à fila **todos** os lotes da entrada cujo caminho contém TEXTO (diferencia maiúsculas) | depende (checkpoint segura) |
| `--reler F0xx-P0xxx [--escopo vazios\|projeto\|documentos] [--documentos COD,COD]` | relê no acervo. **Regrava** `leituras.json`, CSV, pacote e `erros.json` (o antes fica em `releituras/<data>/` com `comparacao.json`); aplica giros pendentes nos JPG; avisa o painel. Padrão `vazios` = erro, prancha sem carimbo, ou projeto/arquiteto/data/título vazio | sim |
| `--avisar-painel F0xx-P0xxx` | aplica giros pendentes nos JPG e pede ao painel para reimportar | não |
| `--reclassificar F0xx-P0xxx --serie SNN [--de SNN] [--ano AAAA] [--codigo-novo F0xx-P0xxx] [--nome-novo NOME]` | troca série, ano, código ou nome (arquivos, catalogação, cache, preview, XMP). **Não refaz o pacote nem avisa o painel**: rode `--reler` ou `--avisar-painel` depois | não |
| `--reidentificar F0xx[-P0xxx] [--sim]` | reagrupa pelo carimbo o que já entrou. Sem `--sim` só mostra; com `--sim` move o que tem um projeto forte, tira da publicação o que é de outra obra, refaz pacotes e avisa o painel | não |
| `--consertar-acervo [F0xx] [--escopo …] [--sim]` | por projeto: relê o escopo, gira e gera JPG que faltam, refaz o pacote e avisa o painel. Sem `--sim` só estima | sim |
| `--decisao CODIGO --acao retirar\|publicar\|nota --motivo "…" --por NOME` | decisão humana sobre um documento | não |
| `--organizar-formatos` | move o que já está no acervo para `<série>/TIF`, `JPG`… | não |
| `--livro-legado` | leva o histórico antigo (`eventos.jsonl`) para o livro, uma vez | não |

**Configuração**

| Comando | O que faz |
|---|---|
| `--criar-config` | cria o `config.json` com os padrões (nunca sobrescreve) |
| `--entrada CAMINHO` / `--acervo CAMINHO` / `--painel URL` / `--pasta CAMINHO` | salva no config e sai |
| `--identidade` | mostra o crédito que vai nas imagens |

**Avaliação**

| Comando | O que faz |
|---|---|
| `--avaliar GABARITO.csv` | relê as folhas do gabarito do painel (`arquivo_origem, campo, valor_correto`) sem gravar no acervo e mostra o placar e o custo (gasta API) |
| `--backtest CURADO.csv [--contra PASTA]` | compara as leituras com uma planilha curada; liberado com ≥ 85% exato |
| `--reconferir-tipo` | recalcula o tipo de desenho pelo título e lista divergências |
| `--backtest-programa CURADO.csv` | placar das sugestões de programa e natureza |

**Só no modo antigo** (pasta vigiada, [seção 13](#13-modo-antigo-pasta-vigiada)): `--status`, `--estimativa`, `--info`, `--planilha-geral`, `--relatorio-geral`, `--marcar-fase`, `--todos`, `--refazer TEXTO`. No servidor, **`--status` não mostra o `100 - Scanners`**: use `--lotes`.

`--relatorio AAAA-MM-DD|hoje` regera o relatório diário (e reenvia o e-mail se ligado).

Se várias opções forem dadas juntas, só a primeira na ordem do código roda (`vigia.py:747-848`).

### 11.2 `cli.py` (uma pasta, sem acervo nem painel)

`python cli.py PASTA [opções]`: o modo original em duas fases.
- **Fase 1** (padrão): lê a pasta, consolida e escreve `PASTA/catalogacao.csv`, `PASTA/relatorio.txt` e `PASTA/campvision2.log`.
- `--aplicar CSV` (simula) / `--aplicar CSV --valendo`: **Fase 2**, copia (os originais ficam) para `<PASTA>_catalogado/<Ano>/<Projeto>/<Projeto> - NNN - <Título>.<ext>` com EXIF; nunca sobrescreve (" (2)").
- `--regravar-metadados CSV`: regrava EXIF de arquivos já organizados.
- `--modelo`, `--trabalhadores N`, `--sem-consolidacao`, `--do-zero` (ignora o checkpoint).
- Saída: 0 ok; 1 erro fatal; 2 lote terminou com folhas em erro.

### 11.3 `app.py` (janela, Mac)

Janela "CAMP Vision 2 — build …": escolher pasta, modelo, pranchas em paralelo (1–12), consolidar por projeto. Botões **Fase 1 — Ler carimbos** (custo ao vivo), **Fase 2 — Aplicar planilha…** (prévia das 5 primeiras, confirma e copia) e **Cancelar** (termina as folhas em andamento). Log em `~/.campvision2/campvision2.log`.

### 11.4 `publicar.sh` (Mac)

Recusa publicar se houver chave real ou `config.json` versionado, roda os testes, faz commit (`git add -A`) e push do `main`. Sem remoto, cria um repositório privado `campvision2` com o `gh` — o repositório real é `mullets/campvision-new`. As instruções finais são do LaunchAgent do Mac (`launchagent/`), que não é usado no servidor.

---

## 12. Configuração (todas as chaves)

`~/.campvision2/config.json`. Chave desconhecida é ignorada com aviso; JSON com erro faz o vigia usar **todos os padrões** (com erro no log). `config.exemplo.json` tem a lista.

**Modelo e API**

| Chave | Padrão | O que faz |
|---|---|---|
| `modelo` | `claude-sonnet-5` | modelo de leitura |
| `max_tentativas_api` | 4 | tentativas por chamada |
| `timeout_segundos` | 120 | tempo máximo por chamada |
| `trabalhadores` | 4 | leituras em paralelo |
| `retomar_checkpoint` | true | não pagar de novo o que já foi lido |
| `consolidar_por_projeto` | true | consolidação por grupo |
| (`api_key`) | — | normalmente vem de `ANTHROPIC_API_KEY`; não é salva no arquivo |

**Recebimento (100 - Scanners → ACERVOS_CAMP)**

| Chave | Padrão | O que faz |
|---|---|---|
| `pasta_entrada` | "" | o `100 - Scanners`; vazio desliga o recebimento |
| `pasta_acervo_final` | "" | o `ACERVOS_CAMP`; vazio usa `pasta_vigiada` |
| `espera_estabilidade_segundos` | 45 | lote com marca de fim: segundos sem mudança |
| `entrada_quieto_minutos` | 10 | lote sem marca: minutos sem mudança |
| `serie_padrao` | S01 | série quando nada indica (estação que não é de foto) |
| `entrada_exigir_exif` | true | EXIF não conferido = erro |
| `apagar_original_apos_pronto` | true | tirar o original da entrada depois do "pronto" |
| `quarentena_entrada` | true | mandar para `_conferidos` em vez de apagar |
| `quarentena_dias` | 7 | dias mínimos na quarentena |
| `caminho_replica` | "" | onde a réplica do acervo aparece; vazio = quarentena nunca esvazia |
| `intervalo_varredura_segundos` | 30 | pausa entre rodadas |

**Painel**

| Chave | Padrão | O que faz |
|---|---|---|
| `painel_url` | "" | `http://192.168.15.60:8000`; vazio desliga o painel (projeto novo espera) |
| `painel_token` | "" | `CAMP_PAINEL_TOKEN` no ambiente tem prioridade |
| `estacao_id` | campvision2 | nome da estação no painel |
| `heartbeat_segundos` | 60 | intervalo do heartbeat |

**Identidade (gravada em cada imagem)**

| Chave | Padrão |
|---|---|
| `identidade_nome` | CAMP - Casa da Arquitetura Moderna Paulista |
| `identidade_site` | "" (o instalador põe `https://camp.arq.br`) |
| `identidade_licenca` | Todos os direitos reservados |
| `identidade_contato` | "" |

**Atualização e relatório**

| Chave | Padrão | O que faz |
|---|---|---|
| `auto_atualizar` | true | auto-atualização |
| `intervalo_atualizacao_minutos` | 60 | intervalo mínimo entre verificações |
| `hora_relatorio` | 18:00 | fecha o dia |
| `email_ativo`, `email_para`, `email_de`, `smtp_servidor`, `smtp_porta` (587), `smtp_usuario` | desligado | envio do relatório (senha em `CAMPVISION_SMTP_SENHA`) |

**Modo antigo (pasta vigiada)**

| Chave | Padrão | O que faz |
|---|---|---|
| `pasta_vigiada` | "" | pasta de projetos a vigiar |
| `processar_pasta_vigiada` | null | null = só quando não há `pasta_entrada` |
| `raiz_de_montagem` | "" | onde os compartilhamentos montam (`/Volumes`, `/mnt`) |
| `exigir_status_json` | true | só processa `status: enviado_windows` |
| `criar_status_ausente` | true | cria `status.json` em pasta que não tem |
| `status_pronto` / `status_concluido` | enviado_windows / campvision_concluido | valores do status |
| `reprocessar_se_houver_novas` | true | projeto que cresce volta à fila |
| `fase_organizacao` | organizado_v2 | marcador de fase |
| `processar_tudo_sem_fase` | false | mutirão (`--todos`) |
| `profundidade_maxima` | 5 | até onde desce |
| `pasta_acervo` | _catalogacao | pasta da planilha única |
| `usar_pasta_como_pista` | true | (o recebimento sempre desliga) |
| `escrever_por_projeto` | true | catalogação dentro de cada projeto |
| `formatos_saida` | ["csv"] | só CSV ("xlsx" é ignorado) |
| `extensoes` | jpg, jpeg, tif, tiff, png | extensões lidas no modo antigo |

**Sem efeito nesta versão:** `confianca_minima_para_aceitar`, `usar_cache_de_regiao`, `falhas_de_cache_toleradas`, `confianca_para_guardar_regiao`, `ganho_minimo_2o_passe`, `margem_recorte`, `lado_maximo_envio`, `qualidade_jpeg_envio`, `aplicar_automaticamente`.

---

## 13. Modo antigo: pasta vigiada

Antes do recebimento pelo `100 - Scanners`, o vigia trabalhava direto numa pasta de projetos (`pasta_vigiada`). Esse modo ainda existe e **liga sozinho quando `pasta_entrada` está vazio** (ou com `processar_pasta_vigiada: true`). No servidor ele fica desligado, porque há `pasta_entrada`.

- **Projeto** = pasta com `status.json` ou `info_projeto.json`, ou só com subpastas de imagem. Ignora `.`, `_`, `catalogacao`, `__MACOSX`. Lê todas as pastas de imagem, menos a matriz TIFF quando há outra cópia.
- **Fila:** cria `status.json` (`enviado_windows`) em pasta sem; processa só `enviado_windows`; projeto concluído que recebe folhas novas volta à fila. Só lê arquivo parado há 45 s.
- **Processar:** lê, consolida, aplica as pistas da pasta, escreve `catalogacao/catalogacao.csv`, `relatorio.txt`, `leituras.json`, marca `status` = `campvision_concluido` com contagens e a **fase** (`fase: organizado_v2`).
- **Atenção:** neste modo o vigia **escreve dentro das pastas de projeto** (status e catalogação).
- Comandos: `--pasta`, `--status`, `--estimativa`, `--info`, `--planilha-geral` (`_catalogacao/acervo.csv`, `projetos.csv`, `pendentes.csv`), `--relatorio-geral`, `--marcar-fase`, `--todos` (mutirão: tudo sem a fase), `--refazer TEXTO|tudo` (tira a fase).
- `--estimativa`: com 20+ folhas de histórico usa o custo medido (× 0,8 a 1,5); senão US$ 0,035–0,06 por folha; tempo = folhas × 4 s ÷ trabalhadores.

---

## 14. Problemas conhecidos e como resolver

| Sintoma | Causa | O que fazer |
|---|---|---|
| Log parado por minutos | cópia, preparo ou EXIF de TIFF pela rede | ver `estado.json` ([10.2](#102-o-serviço)); o log mostra "copiando/preparando N de M" |
| `cancelado antes da leitura` | serviço parado entre a cópia e a leitura | volta sozinho |
| Erro `leitura falhou: cancelado antes de terminar` | serviço parado durante a leitura | `--refazer-lote NOME` (a leitura feita está no checkpoint) |
| Erro `EXIF conferido em 6 de 48` depois de reinício | reinício cortou o exiftool | `--refazer-lote NOME` |
| Lote "já estava no acervo" mas com EXIF incompleto | esse caminho não regrava EXIF | conserto à parte (ver pendências) |
| `aguardando decisão … — decisão no painel` | carimbo parecido com 2+ projetos, ou dúvida de fase | decidir no painel; o lote segue sozinho |
| Fundo "não está na tabela de autoridade" | pasta sem código F000 reconhecível (ex.: "Convidado") | renomear com o código do fundo, ou `_` na frente; se cadastrar o fundo no painel, **reiniciar o serviço** |
| Lote de teste gastando API | pasta de teste em `100 - Scanners` | `_` no começo do nome (ou "teste" no caminho) |
| Fotos em "Desenhos e pranchas", sem título | série deduzida errada | `--reclassificar F0xx-P0xxx --serie S03 --de S01`, depois `--reler … --escopo projeto` |
| Foto com orientação incerta | modelo em dúvida (detalhe, vista aérea) | conferir à mão no painel |
| Painel não muda depois de `--reler`/`--avisar-painel` | folha mudou de série (S01 → S03) e o painel recusa; folha já revisada é protegida; lote do painel já aprovado | ver [pendências](#15-pendências-e-riscos-conhecidos); hoje, acertar no painel |
| `Sem chave de API` num comando | terminal sem as chaves | `set -a; . ~/.campvision2/ambiente; set +a` |
| `--avisar-painel`: "Painel não configurado" | `painel_url` vazio | `--painel http://192.168.15.60:8000` |
| Comandos colados depois do `atualizar.sh` não rodam | o script termina no log ao vivo | Ctrl+C e rodar de novo, um de cada vez |
| `--status` lista coisas estranhas | é comando do modo antigo | usar `--lotes` |
| Auto-atualização parou | `versao_fixada` (depois de `--voltar`/`--versao`) ou falta pacote de sistema | `./atualizar.sh` à mão |
| Quarentena só cresce | sem `caminho_replica` | configurar a réplica |
| Lote do Mac sem operador | o vigia não lê o operador do `info_lote` | ver pendências |

---

## 15. Pendências e riscos conhecidos

Encontrados na auditoria de 14/10 (nenhum foi corrigido ainda, salvo indicação):

1. **Painel recusa folha que mudou de série** (S01 → S03 com o mesmo D, mesmo lote). Correção no `camp-painel` (`importador_lote.py`): renomear a folha existente do mesmo lote em vez de recusar. Caso do SESI Marília (F009-P0060).
2. **Painel aceita só S01–S06.** Folhas S07 (Publicações) e S99 (Não identificado) não entram.
3. **Lote do Mac com várias séries** recebe uma série só.
4. **Operador e estação do Mac não chegam:** o vigia lê só `manifesto.json`/`info_projeto.json`; o Mac usa `info_lote` (e `estacao_tipo` em vez de `tipo_estacao`).
5. **Pasta do Mac fica no `100 - Scanners`** com `status_mac.json`, porque o `info_lote` (não suportado) sobra. O próximo envio para o mesmo projeto já nasce "marcado" (espera só 45 s); um arquivo grande copiando por mais de 45 s pode ser pego pela metade (inferência do código, não observado).
6. **Falha durante reinício** que não seja "cancelado antes da leitura" fica em erro e precisa de `--refazer-lote`.
7. ~~`atualizar.sh` não espera durante "copiando"…~~ Corrigido no contrato 2.3: só reinicia com o vigia `vigiando`; ao subir 3 vezes sem confirmar (`subiu_bem`) o `--boot` volta a versão anterior. Servidores já instalados precisam rodar `sudo ./instalar.sh` de novo para o unit ganhar o `ExecStartPre`.
8. **Lote que já estava no acervo não regrava EXIF** (Itatiba F016-P0024, 14/10).
9. **EXIF `_serie`** usa a série do lote, não a de cada documento renomeado (não é gravada numa etiqueta própria hoje, mas fica errada se passar a ser).
10. **Instalador não configura `caminho_replica`** (quarentena nunca esvazia) nem instala `jpegtran`/`rawpy` (o primeiro `atualizar.sh` instala).
11. **Fundo novo** só é visto depois de reiniciar o serviço.
12. **Mensagem do `--reler`** diz "sem sobrescrever nada", mas ele regrava a catalogação (guardando o antes).
13. **Painel responde 202 a todo aviso**, mesmo quando ignora a pasta; e 410 no `/reservar` deixa o lote aguardando para sempre.
14. **Chaves de config sem efeito** (seção 12) ainda aparecem no `config.json`.
15. **Orientação de prancha sem texto** (croqui) continua só pelo OCR.
16. **Levantamento** dos projetos de foto que entraram como S01 ou sem descrição antes da versão 2026-10-14-04.

---

## 16. Onde mexer no código

| Quero mudar… | Arquivo |
|---|---|
| o que é um lote, quando está pronto, a fila | `entrada.py` (`descobrir`, `pronta`, `Recebedor.pendentes`) |
| fundo, série, código e nome | `entrada.py` (`contexto`, `lote_mac`, `codigo_do_mac`), `fundos.py`, `info_projeto.py` |
| cópia, preparo, triagem, leitura, decisão | `entrada.py` (`Recebedor.processar`, `_planejar`) |
| mudar para o projeto definitivo | `transferir.py` |
| conclusão, EXIF, catalogação, quarentena | `entrada.py` (`_concluir`, `_apagar_originais`), `quarentena.py` |
| prompts e o que o modelo devolve | `visao.py` (`INSTRUCOES`, `INSTRUCOES_FOTO`, `INSTRUCOES_TEXTO`), `esquema.py` |
| leitura em paralelo e checkpoint | `lote.py` |
| orientação pelo texto, hashes | `preparo.py` |
| giro de JPG, JPG que falta | `derivados.py` |
| triagem por série | `triagem.py` |
| agrupar pelo carimbo, casar com a base | `identidade.py` |
| consolidação por grupo | `grupos.py` |
| ano, tipo de desenho, autoria, chaves de identidade | `derivacao.py` |
| pacote, bloqueios, grafias, título, propostas | `projeto.py` |
| programa e natureza | `programa.py` |
| CSV e relatório | `planilha.py`, `entrada.relatorio_lote` |
| metadados gravados | `metadados.py` |
| formatos (DNG, PDF, CDR, PLT) | `formatos.py`, `hpgl.py`, `imagem.py` |
| pastas e nomes | `estrutura.py` |
| renomear e reclassificar | `renomear.py` |
| releitura e diagnóstico | `releitura.py` |
| reidentificação | `reidentificar.py` |
| chamadas ao painel | `painel.py` |
| laço do serviço, heartbeat, auto-atualização, relatórios | `vigia.py` (nucleo), `atualizador.py`, `relatorio_diario.py`, `eventos.py`, `atualizar.sh` |
| comandos | `vigia.py` (raiz), `cli.py`, `app.py` |
| livro de registro | `livro.py` |
| versão | `VERSAO_BUILD` e `CONTRATO` em `config.py` (subir a cada mudança) |

---

## 17. Testes e contrato

- `python -m unittest discover -s tests -t .` — 361 testes, sem rede (cliente da API falso, imagens geradas na hora). O `atualizar.sh` roda os mesmos testes antes de trocar a versão no servidor.
- **Contrato com o painel:** versão em `CONTRATO` (`config.py`, hoje 2.2), mandada no heartbeat. Exemplos reais em `docs/contrato/exemplos/` (heartbeat, reservar, aviso, iniciado, concluído, pacote, erros, propostas, decisões, status, info_projeto, orientação). O teste `TestContrato` falha se um campo mudar sem subir o `CONTRATO`. Para regenerar: `python -m tests.contrato_exemplos`.
