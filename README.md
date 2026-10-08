# CAMP Vision 2

Catalogação de pranchas de arquitetura a partir do carimbo, usando um modelo de
visão. Lê o carimbo, monta a planilha, marca o que precisa de revisão humana e
grava o crédito da CAMP dentro de cada imagem.

Roda como aplicativo de janela, como comando de terminal ou como serviço que
vigia uma pasta sozinho.

---

## Índice

- [Começar](#começar) — ambiente, chave, primeira leitura
- [Servidor da CAMP (Ubuntu)](#servidor-da-camp-ubuntu) — instalar, atualizar, dia a dia
- [Como funciona](#como-funciona) — as duas fases, a leitura, a pasta, os metadados
- [Modo automático](#modo-automático) — vigia, acervo inteiro, serviço do macOS
- [Relatórios](#relatórios)
- [GitHub e auto-atualização](#github-e-auto-atualização)
- [Referência](#referência) — comandos, configuração, custo
- [Se der errado](#se-der-errado)

---

## Começar

### 1. Python 3.10 ou mais novo

O Python que vem com o macOS não serve. Confira:

```bash
python3 --version
```

Se for menor que 3.10, instale pelo **python.org** (instalador universal2,
funciona de Monterey em diante). Prefira o python.org ao Homebrew: ele já traz o
Tcl/Tk, e sem isso a janela não abre. Se usar Homebrew, precisa também de
`brew install python-tk`.

```bash
python3 -m tkinter    # tem que abrir uma janelinha de teste
```

### 2. Ambiente virtual

Descompacte num lugar **fixo e fora da Lixeira** — `~/Aplicativos/campvision2`,
por exemplo. A barra de título mostra o build; confira que bate com o que você
instalou.

```bash
cd ~/Aplicativos/campvision2
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

O ambiente virtual isola isto do Python onde mora o CAMP Vision antigo. Os dois
convivem na mesma máquina sem conflito de versão.

### 3. Chave da API

```bash
echo 'export ANTHROPIC_API_KEY="sk-ant-..."' >> ~/.zshrc
source ~/.zshrc
echo $ANTHROPIC_API_KEY     # tem que devolver a chave
```

Para o modo automático, veja a ressalva sobre LaunchAgent em
[Instalar como serviço](#instalar-como-serviço-do-macos).

### 4. Config e identidade da CAMP

Crie o arquivo de configuração com os padrões:

```bash
python vigia.py --criar-config    # escreve ~/.campvision2/config.json
```

Ele nunca sobrescreve um config existente. O `config.exemplo.json` no
repositório mostra todas as opções com os valores padrão — o `config.json` real
não é versionado, porque pode conter a chave.

Agora preencha a identidade, que é o que vai gravado dentro de cada imagem:

```json
{
  "identidade_nome": "CAMP - Casa da Arquitetura Moderna Paulista",
  "identidade_site": "https://SEU-DOMINIO",
  "identidade_licenca": "CC BY-NC 4.0",
  "identidade_contato": "acervo@SEU-DOMINIO"
}
```

```bash
python vigia.py --identidade   # confere o que será gravado
```

### 5. exiftool (só para a Fase 2)

```bash
brew install exiftool
```

Sem ele o app funciona, mas não grava metadados nas imagens.

### 6. Primeira leitura

```bash
python -m unittest discover -s tests -t .   # 233 testes, sem rede
python app.py                                # janela
```

Comece por uma pasta de 20 pranchas de um acervo difícil. O `relatorio.txt` diz
em dez segundos se vale seguir.

---

## Servidor da CAMP (Ubuntu)

A máquina dedicada é um notebook Ubuntu Server, usuário `campvision`, IP fixo
**192.168.15.40**. Ele lê `100 - Scanners`, organiza `ACERVOS_CAMP` e avisa o
painel (192.168.15.60). Contrato com o painel: [docs/contrato-painel.md](docs/contrato-painel.md).

### Instalar (uma vez)

**0. IP fixo .40** — no próprio notebook (não por SSH):

```bash
ip -br a                     # nome da placa (ex.: enp3s0)
ip route | grep default      # roteador (ex.: 192.168.15.1)
sudo tee /etc/netplan/01-camp-ip-fixo.yaml >/dev/null <<'EOF'
network:
  version: 2
  ethernets:
    enp3s0:
      dhcp4: false
      addresses: [192.168.15.40/24]
      routes:
        - to: default
          via: 192.168.15.1
      nameservers:
        addresses: [192.168.15.1, 1.1.1.1]
EOF
sudo chmod 600 /etc/netplan/01-camp-ip-fixo.yaml
sudo netplan try             # Enter para confirmar
```

**1. Código:**

```bash
sudo apt update && sudo apt install -y git
cd ~ && git clone https://github.com/mullets/campvision-new.git
```

**2. Tudo o resto, um comando:**

```bash
cd ~/campvision-new && sudo ./instalar.sh
```

Pergunta IP/usuário/senha do QNAP, chave da Anthropic, token do painel e token
do GitHub, e faz: pacotes (exiftool, poppler, cifs), **tampa do notebook não
suspende**, QNAP montado para sempre em `/mnt/camp/backup`, `.venv`, config,
serviço `campvision2` (sobe no boot, volta se cair) e a **tela do notebook**
(tty1) mostrando o vigia ao vivo. Termina conferindo tudo. Pode rodar de novo
sem estragar nada.

**3. Conferir:** `systemctl status campvision2`, `.venv/bin/python vigia.py --lotes`,
e `sudo reboot` — a tela tem que voltar sozinha em "vigiando".

### Atualizar

```bash
cd ~/campvision-new && ./atualizar.sh
```

Espera o lote em andamento, puxa o código, instala dependências novas, faz
backup do `~/.campvision2`, roda os testes — **se falharem, volta sozinho** — e
reinicia o serviço. `./atualizar.sh --voltar` desfaz a última atualização (e
pausa a auto-atualização até a próxima manual). A auto-atualização de hora em
hora usa o mesmo script.

### No dia a dia

| Comando | Para quê |
|---|---|
| `vigia.py --lotes` | o que está em `100 - Scanners` e em que pé está |
| `vigia.py --historico DEST352844.tif` | caminho completo de um arquivo (também aceita código, lote ou data) |
| `vigia.py --refazer-lote TEXTO` | devolve à fila uma pasta com erro, depois de corrigir |
| `vigia.py --monitor` | a mesma tela do notebook, por SSH |
| `journalctl -u campvision2 -f` | log ao vivo |

(Todos com `~/campvision-new/.venv/bin/python` na frente.)

## Como funciona

### As duas fases

**Fase 1 — Ler.** Lê os carimbos e escreve `catalogacao.csv`,
`leituras.json` e `relatorio.txt`. **Não move, não renomeia, não apaga nada.**

**Fase 2 — Aplicar.** Você revisa a planilha; ela vira a fonte da verdade. O app
copia os arquivos para `<pasta>_catalogado/{ano}/{projeto}/` com o nome final e
grava os metadados. Os originais ficam onde estão.

Separar as duas é o ponto do redesenho: leitura errada vira célula errada na
planilha, corrigida em cinco segundos — nunca mais uma prancha em pasta errada.

### O que mudou em relação ao CAMP Vision antigo

O app antigo gastou meses numa pergunta só: **onde está o carimbo?** YOLO, busca
por contorno, fusão de caixas, quatro rotações, limiar de conteúdo — cinco causas
raiz diferentes para o mesmo arquivo de teste. Nada disso existe aqui: o mesmo
modelo que lê o carimbo é quem o localiza, e ele lê texto girado, manuscrito e
tabela institucional sem detector nenhum.

**Aposentados:** YOLO/ultralytics, torch, OpenCV, Tesseract, o pré-processamento
de OCR, o banco SQLite de conhecimento, a quarentena de grafias,
`unificar_grafias`, a moda de ano por grupo, a correção de orientação por
heurística, `imagecodecs` e o pin `numpy<2`.

**Dependências:** anthropic, Pillow. Só. Nada exige AVX2, GPU ou
compilação — roda igual no Mac Pro 2013 e no MacBook Pro 2011.

**Sobreviveram, porque provaram valor:** log com o nome do arquivo em toda linha,
cancelamento que cancela de verdade, ordenação por projeto e folha, e a
atribuição institucional no Copyright.

### A leitura

Segue o **método de leitura** (projeto Tainacam: `claude/campvision-metodo-de-leitura.md`),
em quatro passadas — só a 2 chama o modelo:

1. **Preparo** (código): imagem de leitura a 2000 px; **orientação pelo texto**
   (OCR rápido nas 4 rotações, testa espelhado; empate → `orientacao_incerta`);
   md5 e hash perceptual; preview de 3000 px já girado; EXIF herdado é limpo.
2. **Leitura** (modelo): **uma chamada por folha, página inteira, sem recorte do
   carimbo.** Transcrição literal, `null` quando ilegível, alternativas para dígito
   ambíguo, `onde` foi lido. Campos novos: escritório separado do arquiteto,
   código de série, revisão, transcrição integral, materiais citados.
   Fotografias, negativos e slides (S03–S05) têm prompt próprio; crédito de
   fotógrafo nunca vem do modelo.
3. **Consolidação** (código): moda por grupo (projeto + revisão), quarentena,
   outliers marcados sem apagar o lido; ano do grupo pela moda, ano de cada folha
   intacto, com ressalva quando a alternativa bate com a série; autoria nunca é
   "corrigida" pelo consenso.
4. **Derivação** (código): ano só da data escrita; tipo de desenho pelos 15
   detectores (§5.2); ficha de documentação ≠ obra; autoria divergente contra
   titular + coautores do fundo; duplicatas; `pacote_tainacan.json` e checklist
   de aceite.

Validação: `vigia.py --backtest CURADO.csv` compara com a base curada à mão
(≥ 85% exato para liberar) e `--reconferir-tipo` recalcula o tipo gravado.

### As fontes secundárias

Além do carimbo, o acervo já carrega informação em dois lugares: o
`info_projeto.json` de cada projeto e a própria estrutura de pastas. A ordem de
prioridade é:

```
carimbo lido  >  info_projeto.json  >  nome da pasta
```

O carimbo é o documento. O `info_projeto.json` é curadoria humana anterior, então
vale mais que convenção de nome de pasta. **Nenhum dos dois sobrescreve o que o
carimbo disse.**

O leitor do `info_projeto.json` é deliberadamente tolerante: casa chave por
palavra, então `nome_do_projeto`, `nomeDoProjeto` e `titulo` caem todos em
Projeto, e `data_projeto: "1979-04-03"` vira Ano 1979. Casa por palavra, e não
por pedaço de texto, justamente para `plano_diretor` não virar "ano". Chave que
ele não reconhece é ignorada, e arquivo corrompido não quebra o lote.

Para ver o esquema real dos seus arquivos, sem eu adivinhar:

```bash
python vigia.py --info
```

Ele lista cada chave encontrada no acervo, quantas vezes aparece, em que campo
caiu e um exemplo do valor. As que aparecem com `—` não têm campo
correspondente; se alguma importar, dá para mapear em `nucleo/info_projeto.py`.

A estrutura de pastas entra por último, com uma regra rígida de proveniência:

**A pasta nunca é mostrada ao modelo.** Se ele souber que a pasta se chama
`TeatroDeSantos-1968`, passa a *confirmar* isso no carimbo em vez de transcrever
o que está escrito. A leitura é cega; a pasta entra depois, etiquetada como outra
fonte. Há teste garantindo que nem o nome do arquivo vaza para o prompt.

Com isso a pasta serve para três coisas:

1. **Conferir.** Carimbo dizendo "EDIFÍCIO COPAN" dentro de
   `1968/TeatroDeSantos` acende a coluna **Divergência**. Quase sempre é prancha
   na pasta errada.
2. **Preencher o que faltou.** Campo ilegível ganha o valor da pasta, com
   confiança 0.5 e célula azul. Valor do carimbo *sempre* vence.
3. **Situar.** Colunas `Fundo (pasta)`, `Projeto (pasta)` e `Ano (pasta)`.

| Pasta | Projeto | Ano | Fundo |
|---|---|---|---|
| `Fundo OCG/1968/TeatroDeSantos` | Teatro De Santos | 1968 | Fundo OCG |
| `OCG-TeatroDeSantos-1968` | Teatro De Santos | 1968 | OCG |
| `SBU_Eletropaulo_CARMONA` | Eletropaulo CARMONA | — | SBU |
| `DEST3524 CasaDaPraia 1972` | Casa Da Praia | 1972 | — |

Descarta código de digitalização, separa CamelCase, reconhece sigla de fundo no
prefixo e ignora pasta estrutural (`JPG`, `TIF`, `imagens`, `Acervo`) mesmo
quando ela é a folha. Ano só entre 1800 e 2099, para `Casa 0350` não virar ano.

Desligar: `"usar_pasta_como_pista": false`.

### Metadados dentro do arquivo

Regra da casa: **toda imagem que sai daqui leva o nome e o site da CAMP dentro
dela**. Nome de arquivo se perde, pasta se reorganiza, planilha fica para trás —
o metadado viaja junto com a imagem.

| Onde | O quê |
|---|---|
| `EXIF:Artist`, `XMP-dc:Creator`, `IPTC:By-line` | o arquiteto — autoria da **obra** |
| `XMP-dc:Publisher`, `Credit`, `Source` | a CAMP — instituição **guardiã**, sempre |
| `XMP-xmpRights:WebStatement`, `CreatorWorkURL` | o site |
| `EXIF:Copyright`, `XMP-dc:Rights`, `IPTC:CopyrightNotice` | autor / CAMP — licença |
| `Title`, `Description`, `Headline`, `City`, `State` | o que o carimbo disse |
| `XMP-dc:Subject`, `IPTC:Keywords` | projeto, arquiteto, tipo, cidade, ano, CAMP |
| `XMP-xmp:CreatorTool` | CAMP Vision 2 + build que gravou |

Distinção que importa para acervo: o **arquiteto** é Creator, a **CAMP** é
Publisher. Confundir os dois é atribuir a autoria da obra à instituição.

O crédito institucional vai **mesmo quando o carimbo não foi lido** — prancha
ilegível continua sendo patrimônio identificado. `Ano: 1968` vira
`XMP-dc:Date=1968`, não `1968:01:01`: registrar mês e dia seria inventar precisão
que o carimbo não tem.

A gravação é em lote, um processo do exiftool para o acervo inteiro. Mil pranchas
levam segundos. Acentuação vai em UTF-8 e volta íntegra.

Mudou o site ou a licença? Regrave sem reprocessar:

```bash
python cli.py /caminho/da/pasta --regravar-metadados catalogacao.csv
```

---

## Modo automático

O vigia varre a pasta montada, acha projetos prontos, lê, escreve a planilha e
avança o semáforo:

```
enviado_windows  ──vigia──▶  campvision_concluido  ──QNAP──▶  sincronizado
```

Quem manda é o `status.json` no disco, nunca estado em memória: reiniciar o Mac
não perde nem repete nada. O vigia **preserva os campos que as outras máquinas
escreveram** no mesmo arquivo.

```bash
python vigia.py --pasta /Volumes/acervos    # define a pasta (aceita URL smb://)
python vigia.py --status                   # a árvore que ele enxerga, sem processar
python vigia.py                            # painel ao vivo, até Ctrl+C
python vigia.py --uma-vez                  # processa e sai (para cron)
```

**Deixar rodando de vez.** É o uso normal: `python vigia.py` (ou o LaunchAgent)
varre a cada 30 s e processa o que aparecer. Duas proteções para isso funcionar
com o scanner despejando arquivo na pasta:

- **Arquivo recém-copiado espera.** Prancha mexida há menos de
  `espera_estabilidade_segundos` (45 s) fica de fora da rodada: o scanner pode
  ainda estar escrevendo, e ler JPG pela metade dá leitura errada e gasta
  chamada à toa. Ela entra na varredura seguinte.
- **Projeto que cresce volta para a fila.** Projeto já concluído que ganha
  pranchas novas é recolocado na fila automaticamente, e **só as novas vão à
  API** — o checkpoint segura as que já foram lidas. Desligar:
  `"reprocessar_se_houver_novas": false`.

O painel mostra situação, fila, barra da prancha atual, contadores do dia e custo
acumulado. Fora de um terminal ele se desliga sozinho e vira log corrido, sem
lixo de escape ANSI no arquivo.

**Ctrl+C encerra com elegância:** termina a prancha em andamento, grava o
checkpoint e sai. O projeto interrompido não avança de status e é retomado na
próxima subida.

A Fase 2 continua **desligada** por padrão mesmo aqui. O vigia automatiza a
leitura, não a decisão de mexer nos arquivos.

### Apontar para um share SMB

`--pasta` aceita a URL do Finder e traduz para o ponto de montagem:

```bash
python vigia.py --pasta "smb://Server-Camp._smb._tcp.local/Backup Servidor CAMP/Arquivos/99 - Saida Scanner Contex HD"
# vira /Volumes/Backup Servidor CAMP/Arquivos/99 - Saida Scanner Contex HD
```

O share precisa estar montado antes (Finder, Cmd+K). Se não estiver, ele diz
exatamente qual share falta em vez de gravar um caminho que não existe.

Caminho gravado por uma versão antiga, com `smb:` colado no diretório do app, é
corrigido sozinho ao carregar o config — com aviso no log dizendo o antes e o
depois. Se o share cair durante o
trabalho, o vigia avisa no log e segue tentando, sem morrer.

Em rede, use menos `trabalhadores` (2 ou 3): o gargalo passa a ser o SMB, não a
API.

### Rodar na raiz do acervo

Aponte para a raiz e ele acha os projetos em qualquer nível abaixo:

```
/Volumes/acervos/
├── Fundo OCG/
│   ├── 1968/TeatroDeSantos/JPG/      ← projeto
│   └── 1972/CasaDaPraia/JPG/         ← projeto
├── Fundo SBU/EletropauloCARMONA/     ← projeto (imagens soltas)
└── _catalogacao/acervo.csv           ← planilha única de tudo
```

**Como ele decide o que é projeto.** Cada acervo veio de um fluxo diferente, e
o nome das pastas não é confiável para isso. A classificação é de baixo para
cima:

- tem `status.json` ou `info_projeto.json` → **projeto**
- tem imagens direto → **pasta de imagem**
- só tem pastas de imagem abaixo → **projeto** (`Projeto/{JPG,TIF}` é um só)
- tem projetos abaixo → **agrupador** (fundo, ano), e a busca continua descendo

Isso resolve os dois formatos do acervo ao mesmo tempo:

```
BSG-EdificioPiracicaba-AnteProjeto-1979/      ← projeto
├── status.json  info_projeto.json
├── JPG/                                      ← lê daqui
└── TIF/                                      ← ignorado (matriz)

F001 - ARM - Arnaldo Martino/                 ← agrupador
└── P0001 - I Simpósio ... - 1979/            ← projeto (marcador)
    ├── status.json  info_projeto.json
    ├── catalogacao/                          ← saída, nunca relida
    └── 01 - Desenhos e Pranchas/
        ├── 01 - Arquivo Arquivístico (TIFF)/ ← ignorado
        └── 03 - Preview (JPG)/               ← lê daqui
```

**Qual pasta de imagem ele lê.** Desce a árvore inteira do projeto e lê **tudo
que não for matriz arquivística**. A matriz só é lida quando é a única cópia que
existe.

Uma pasta é matriz se o nome disser (`TIFF`, `Arquivo Arquivístico`, `matriz`)
**ou** se a maioria dos arquivos dentro dela for `.tif`. Os dois critérios
importam: pasta chamada `IGREJA PARÓQUIA MÃE DO SALVADOR` cheia de TIFF é matriz
do mesmo jeito, e pasta de nome esquisito cheia de JPG é conteúdo e tem que ser
lida.

Isto já foi feito por pontuação, ficando só com a pasta de maior nota — e
descartava em silêncio pastas de conteúdo com nome fora do padrão. Perder
prancha calado é pior que ler demais.

Checkpoint e `catalogacao/` ficam sempre na **raiz do projeto**, nunca dentro da
subpasta de imagem. Profundidade máxima em `profundidade_maxima` (padrão 5).

**Pasta sem `status.json` ganha um**, marcado como pronto e com
`criado_por: campvision2`. Projeto que chegou por fora do fluxo do Windows era
ignorado em silêncio; agora entra. Pasta sem imagem não ganha status — não é
projeto. Status que já existe nunca é mexido.

### Do 100 - Scanners para o ACERVOS_CAMP

Os scanners largam o material em `100 - Scanners/<fundo>/<material>/<projeto>/`
(a ordem de material e projeto tanto faz; `TIF/`, `JPG/`, `DNG/`, `PDF/` dentro são
aceitos). Para cada pasta, o vigia:

1. espera ela ficar **completa**: `manifesto.json` ou `status.json`
   `enviado_windows` da estação, ou nada mudar por `entrada_quieto_minutos` (10);
2. monta o **contexto**: fundo **pela tabela de autoridade** (código, sigla ou nome —
   nunca chuta; código e nome em conflito é erro), série pela pasta de material
   (sem ela: `99 - Não identificado`), projeto e ano pelo nome da pasta
   (`SBU-Taruma-1972` também), operador e estação pelo manifesto;
3. acha o projeto em `ACERVOS_CAMP` ou **pede o número ao painel** (`/reservar`;
   sem painel o projeto novo espera, nunca recebe número inventado);
4. **copia** com o nome CAMP `F0xx-P000x-AAAA-S0x-DNNNNN` para
   `<Fundo>/01 - Projetos/<F0xx-P000x - Nome>/<série>/`, criando o MODELO de pastas
   e os README; confere cada cópia por hash; TIF/JPG/DNG do mesmo documento
   ganham o mesmo código; arquivo que já está no acervo não é copiado de novo;
5. **lê o carimbo** uma vez por documento, da versão mais leve (DNG pelo preview,
   PDF pela primeira página);
6. grava **EXIF/XMP completo** em todas as versões — crédito
   "Acervo {Arquiteto}/CAMP - Casa da Arquitetura Moderna Paulista", código,
   fundo, operador — e confere arquivo por arquivo;
7. escreve `catalogacao/` (`catalogacao.csv`, `contatos.jpg`, `erros.json`,
   `lotes/`) e `info_projeto.json`;
8. marca `status.json` = `pronto` (por último) e avisa o painel;
9. **apaga o original** da entrada — só o que foi copiado, conferido e não mudou.

Qualquer falha antes do 8 deixa o projeto em `erro` com o motivo e **não apaga
nada**. Tudo vai para o **livro de registro**
(`ACERVOS_CAMP/_campvision/registro/AAAA-MM.jsonl` e `.csv`): copiado, lido,
exif_gravado, apagado_original, erro — com origem, destino, nome original e hash.

### A planilha única do acervo

Escrita em `_catalogacao/` ao fim de cada rodada, **só CSV** (XLSX foi abolido
em 07/10/2026): `acervo.csv` (uma linha por prancha, com as colunas **Revisar** e
**Campos a revisar** no lugar das cores), `projetos.csv` (fundo, cobertura,
campos a revisar, divergências) e `pendentes.csv`.

Cada projeto também recebe a sua catalogação em `catalogacao/`. Para ter só a da
raiz: `"escrever_por_projeto": false`. O `leituras.json` de cada projeto é
sempre escrito — é dele que a catalogação da raiz é remontada, sem gastar API.

Remontada dos `catalogacao/leituras.json` de cada projeto, **nunca da API**. Rode
quantas vezes quiser, a qualquer hora, sem custo:

```bash
python vigia.py --planilha-geral
```

### Mutirão: passar uma vez em tudo

Acervo com `status.json` e `info_projeto.json` de fluxos antigos não entra na
fila normal, porque o status deles não é `enviado_windows`. Para passar uma vez
no acervo inteiro e normalizar tudo:

```bash
python vigia.py --todos --estimativa   # quanto custa e quanto demora
python vigia.py --todos --uma-vez      # roda o mutirão
```

No modo `--todos` o único portão é a **fase**, não o status. Ou seja: processa
todo projeto que ainda não passou por esta versão, seja qual for o status antigo
dele — e o status antigo é preservado no arquivo, só ganha a fase carimbada.

É **idempotente**: rodar de novo não refaz nada, porque quem já tem
`fase: organizado_v2` sai da fila. Se o mutirão for interrompido no meio, é só
rodar de novo — ele continua de onde parou, projeto a projeto, e dentro de cada
projeto o checkpoint cuida das pranchas já lidas.

A estimativa conta as pranchas de verdade e devolve uma faixa de custo. Assim
que houver histórico de lotes reais, ela passa a usar o **custo médio por
prancha medido no seu próprio acervo** em vez de conta teórica, e diz de onde
veio o número. Para dimensionar antes disso: **mil pranchas ficam por volta de
US$ 20 a US$ 40**, em menos de meia hora com 4 em paralelo.

### Refazer projetos já processados

```bash
python vigia.py --refazer "F002"    # ou 'tudo' para o acervo inteiro
python vigia.py --todos --uma-vez
```

Tira a marca de fase dos projetos que casarem com o texto, devolvendo-os à fila.
**O checkpoint continua lá**: prancha já lida não é relida nem paga de novo — só
o que faltou entra na conta. É o jeito de reprocessar quando uma versão nova
passa a enxergar pastas que a anterior deixava de fora.

### O marcador de fase

Todo projeto que passa por esta versão recebe `"fase": "organizado_v2"` no
`status.json`, com a data. Para carimbar os que já foram processados antes:

```bash
python vigia.py --marcar-fase
```

Vai numa **chave própria**, não no `status`. Se substituísse o valor do semáforo,
o watcher do QNAP — que procura exatamente por `campvision_concluido` — pararia
de sincronizar, e você descobriria dias depois com os arquivos parados no Mac.

```json
{
  "status": "campvision_concluido",
  "criado_por": "campvision2",
  "campvision2_pranchas": 4,
  "campvision2_com_carimbo": 4,
  "fase": "organizado_v2",
  "fase_em": "2026-09-05T12:00:52"
}
```

### No Ubuntu

Veja [Servidor da CAMP (Ubuntu)](#servidor-da-camp-ubuntu): `sudo ./instalar.sh`
faz tudo. O `systemd/instalar.sh` antigo (serviço de usuário) foi substituído.

### Instalar como serviço do macOS

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
python vigia.py --pasta /Volumes/acervos
./launchagent/instalar.sh
```

Sobe no login, volta sozinho se cair. O script escreve a chave no plist com
permissão 600 — **um LaunchAgent não lê o seu `~/.zshrc`**, então exportar no
shell não basta.

```bash
launchctl list | grep campvision2      # está rodando?
tail -f ~/.campvision2/vigia.log       # acompanhar
./launchagent/instalar.sh --remover    # desinstalar
```

Para ver o painel, pare o serviço e rode `python vigia.py` na mão.

---

## Relatórios

### Diário

Todo dia na hora configurada (padrão 18:00) o vigia fecha o dia e escreve `.txt`
e `.html` em `~/.campvision2/relatorios/`: projetos, pranchas, percentual com
carimbo, erros, **campos esperando revisão**, tempo de máquina e custo — mais a
linha de cada projeto e destaque para o que falhou.

Para receber por email, no `~/.campvision2/config.json`:

```json
{
  "email_ativo": true,
  "email_para": "voce@exemplo.com",
  "email_de": "vigia@exemplo.com",
  "smtp_servidor": "smtp.exemplo.com",
  "smtp_porta": 587,
  "smtp_usuario": "vigia@exemplo.com"
}
```

A senha vai na variável `CAMPVISION_SMTP_SENHA`, nunca no config. Falha de email
não derruba o vigia: o arquivo é escrito de qualquer jeito.

### Geral

Reescrito junto com o diário, sempre refletindo o acervo inteiro: período,
projetos, pranchas catalogadas, taxa de carimbo lido, ritmo por dia, tempo de
máquina, **custo total e custo por prancha**, fila atual e projetos do maior para
o menor.

```bash
python vigia.py --relatorio-geral
python vigia.py --relatorio 2026-09-03   # regera um dia específico
```

---

## GitHub e auto-atualização

O remoto já vem configurado (`git@github.com:mullets/campvision2.git`). Crie o
repositório **vazio** em https://github.com/new — sem README e sem .gitignore,
senão o push conflita — e envie:

```bash
git push -u origin main
```

Ou `./publicar.sh mullets`, que antes de enviar confere se há segredo
versionado, roda os testes e commita o que estiver pendente.

## Na máquina dedicada

Veja [Servidor da CAMP (Ubuntu)](#servidor-da-camp-ubuntu).

## Referência

### Comandos

| Comando | O que faz |
|---|---|
| `python app.py` | janela: escolher pasta, ler, aplicar |
| `python cli.py PASTA` | lê uma pasta e escreve a planilha |
| `python cli.py PASTA --aplicar catalogacao.csv [--valendo]` | Fase 2 (sem `--valendo`, só simula) |
| `python cli.py PASTA --regravar-metadados catalogacao.csv` | regrava metadados sem reprocessar |
| `python vigia.py` | painel ao vivo |
| `python vigia.py --status` | árvore do acervo e o que está pendente |
| `python vigia.py --uma-vez` | processa o pendente e sai |
| `python vigia.py --planilha-geral` | planilha única do acervo |
| `python vigia.py --relatorio-geral` | relatório acumulado |
| `python vigia.py --marcar-fase` | carimba a fase em todos os projetos |
| `python vigia.py --identidade` | confere o crédito das imagens |
| `python vigia.py --criar-config` | cria o `config.json` com os padrões |
| `python vigia.py --info` | esquema real dos `info_projeto.json` do acervo |
| `python vigia.py --todos --estimativa` | conta pranchas e estima custo, sem chamar a API |
| `python vigia.py --todos --uma-vez` | mutirão: passa em tudo que não tem a fase |
| `python vigia.py --refazer TEXTO` | devolve projetos à fila (`tudo` = todos) |

### Configuração

Tudo em `~/.campvision2/config.json` — crie com `--criar-config`. O
`config.exemplo.json` lista todas as opções; os comentários de cada uma estão em
`nucleo/config.py`. As que mais importam:

| Chave | Padrão | O que faz |
|---|---|---|
| `modelo` | `claude-sonnet-5` | modelo de visão |
| `trabalhadores` | 4 | pranchas em paralelo |
| `confianca_minima_para_aceitar` | 0.75 | abaixo disso, faz o 2º passe |
| `ganho_minimo_2o_passe` | 1.3 | 2º passe só se o recorte ficar mais nítido |
| `confianca_para_guardar_regiao` | 0.85 | região só entra no cache se a leitura foi confiante |
| `falhas_de_cache_toleradas` | 2 | depois disso o cache de região se desliga |
| `consolidar_por_projeto` | true | normaliza grafias no fim do lote |
| `usar_pasta_como_pista` | true | pasta preenche campo vazio e confere |
| `exigir_status_json` | true | só processa pasta marcada como pronta |
| `criar_status_ausente` | true | cria status em pasta que não tem |
| `processar_tudo_sem_fase` | false | mutirão: portão é a fase, não o status (`--todos`) |
| `profundidade_maxima` | 5 | até onde desce na árvore |
| `formatos_saida` | `["csv"]` | só CSV; `"xlsx"` em config antigo é ignorado com aviso |
| `pasta_entrada` | `""` | `100 - Scanners` montado; vazio desliga o recebimento |
| `pasta_acervo_final` | `""` | `ACERVOS_CAMP` montado |
| `entrada_quieto_minutos` | `10` | pasta sem manifesto conta como completa depois disso |
| `apagar_original_apos_pronto` | `true` | apaga da entrada o que foi arquivado e conferido |
| `painel_url` | `""` | `http://192.168.15.60:8000`; token em `CAMP_PAINEL_TOKEN` |
| `heartbeat_segundos` | `60` | batimento para o painel |
| `escrever_por_projeto` | true | catalogação dentro de cada projeto, além da raiz |
| `espera_estabilidade_segundos` | 45 | arquivo mexido agora espera a próxima rodada |
| `reprocessar_se_houver_novas` | true | projeto que cresce volta para a fila |
| `intervalo_varredura_segundos` | 30 | de quanto em quanto tempo varre |
| `raiz_de_montagem` | `""` | onde os shares montam; vazio = `/Volumes` (macOS) ou `/mnt` (Linux) |
| `hora_relatorio` | `18:00` | quando fecha o dia |
| `auto_atualizar` | true | puxa código novo do GitHub |

### Custo

Sonnet 5 custa US$ 2 por milhão de tokens de entrada e US$ 10 de saída.

**Medido no acervo CAMP: ~US$ 0,04 por prancha**, ou seja **US$ 35 a 50 por mil
pranchas**. A primeira estimativa deste README dizia US$ 10 a 15 e estava cinco
vezes abaixo: a conta teórica supunha uma chamada por prancha, e na prática a
maioria precisa de duas — carimbo de acervo antigo raramente sai confiante no
primeiro passe.

O `relatorio.txt` de cada lote mede o **retorno do segundo passe**: quantas
pranchas releram, quantas de fato melhoraram e quanto subiu a confiança. Se
menos de 40% melhorarem, ele sugere baixar `confianca_minima_para_aceitar` — é
como se decide o limiar com dado em vez de palpite.

O `--estimativa` usa o custo **medido no seu próprio acervo** assim que houver
histórico de lotes; até lá, usa a referência acima. A janela e o painel mostram
o custo ao vivo; a tabela de preços fica em `nucleo/config.py`.

Para lotes grandes sem pressa, a Batch API tira 50% — o código já isola a chamada
em `ClienteAnthropic.chamar`.

### Onde mexer

| Quero… | Arquivo |
|---|---|
| acrescentar campo ao carimbo | `nucleo/esquema.py` (propaga sozinho) |
| mudar o que o modelo procura | constante `INSTRUCOES` em `nucleo/visao.py` |
| mudar convenção de nome/pasta | `montar_nome`, `montar_pasta` em `nucleo/aplicar.py` |
| mudar os metadados gravados | `montar_argumentos` em `nucleo/metadados.py` |
| mudar como a pasta é interpretada | `nucleo/caminho.py` |
| mapear chave nova do `info_projeto.json` | `PALAVRAS` em `nucleo/info_projeto.py` |
| mudar qual versão das imagens é lida | `PALAVRAS_MATRIZ` e `_e_matriz` em `nucleo/vigia.py` |

### Testes

```bash
python -m unittest discover -s tests -t .
```

233 testes, nenhum toca a rede: o cliente de API é falso e as pranchas são
geradas na hora.

### Segurança do lote

- **Checkpoint** (`campvision2_checkpoint.jsonl`) a cada prancha terminada: queda
  de energia, disco cheio ou cancelamento não perdem o que já foi lido nem custam
  API de novo. Apague o arquivo para reprocessar do zero.
- **Nada é sobrescrito** na Fase 2: nome que já existe ganha ` (2)`.
- **Erro de leitura não vira exceção**: vira linha na planilha com a coluna Erro
  preenchida, e o lote segue.

---

## Se der errado

| Sintoma | Causa |
|---|---|
| `ModuleNotFoundError: tkinter` | Python do Homebrew sem Tcl/Tk — instale pelo python.org ou `brew install python-tk` |
| `ModuleNotFoundError: anthropic` | ambiente virtual não ativado — falta `source .venv/bin/activate` |
| "Sem chave de API" | `ANTHROPIC_API_KEY` não chegou ao processo; num LaunchAgent, veja a seção do serviço |
| Build na barra de título não é o que você instalou | está rodando de outra pasta (Lixeira, Downloads antigo) |
| Lote não reprocessa nada | checkpoint anterior — apague `campvision2_checkpoint.jsonl` |
| Vigia não acha projeto nenhum | `python vigia.py --status` mostra a árvore inteira e o que está pendente |
| Projeto fundo demais na árvore | aumente `profundidade_maxima` (padrão 5) |
| "falta preencher identidade.site" | preencha `identidade_site` antes de publicar imagens |
| Metadados não gravados | `brew install exiftool` — sem ele a cópia acontece, o metadado não |
| Vigia no ar mas parado | ele diz o motivo no painel: share não montado, caminho malformado ou pasta inexistente |
| "caminho malformado (contém 'smb:')" | config gravado por versão antiga; rode `--pasta` de novo com a URL entre aspas |
| "não é um repositório git" | falta `git remote add origin ...` — auto-atualização desligada até lá |
| Não acho o `config.json` | ele só nasce com `python vigia.py --criar-config` ou no primeiro uso da janela |
