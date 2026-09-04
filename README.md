# CAMP Vision 2

Catalogação de pranchas de arquitetura a partir do carimbo, usando um modelo de
visão. Reescrita enxuta do CAMP Vision.

## O que mudou, e por quê

O app antigo gastou meses em uma pergunta só: **onde está o carimbo?** YOLO,
busca por contorno, fusão de caixas, quatro rotações, limiar de conteúdo,
resolução de busca separada da de verificação — cinco causas raiz diferentes
para o mesmo arquivo de teste. Nada disso existe aqui. O mesmo modelo que lê o
carimbo é quem o localiza, e ele lê texto girado, manuscrito e tabela
institucional sem detector nenhum.

**O que morreu:** YOLO/ultralytics, torch, OpenCV, Tesseract, o pré-processamento
de OCR, o banco SQLite de conhecimento, a quarentena de grafias, `unificar_grafias`,
a moda de ano por grupo, a correção de orientação por heurística, `imagecodecs`
e o pin `numpy<2`. Dependências: **anthropic, Pillow, openpyxl**. Só.

**O que sobreviveu, porque provou valor:** log com o nome do arquivo em toda
linha (`nucleo/registro.py`), cancelamento que cancela de verdade, ordenação da
planilha por projeto e folha, e a atribuição institucional no Copyright.

## As duas fases

**Fase 1 — Ler.** Lê os carimbos e escreve `catalogacao.xlsx`, `catalogacao.csv`
e `relatorio.txt` na própria pasta. **Não move, não renomeia, não apaga nada.**

**Fase 2 — Aplicar.** Você revisa a planilha; ela vira a fonte da verdade. O app
copia os arquivos para `<pasta>_catalogado/{ano}/{projeto}/` com o nome final e
grava EXIF. Os originais ficam onde estão.

Separar as duas é o ponto do redesenho: leitura errada vira célula errada na
planilha, corrigida em cinco segundos — nunca mais uma prancha em pasta errada.

## Como a leitura funciona

1. **Cache de região.** Pranchas do mesmo projeto têm o carimbo no mesmo lugar.
   Achou na primeira, as próximas vão direto ao recorte: 1 chamada em vez de 2.
2. **Página inteira reduzida.** O modelo devolve os campos **e** a região do
   carimbo em coordenadas normalizadas.
3. **Segundo passe.** Se a confiança média ficou abaixo de 0.75, recorta aquela
   região na resolução **original** e relê só ela.
4. **Consolidação por projeto.** No fim do lote, uma chamada de texto por grupo
   com todos os carimbos lado a lado decide a grafia canônica. É isto que
   resolve o caso `HOSWALDO` sem quarentena nem contagem — e a consolidação
   nunca aplica valor que nenhuma prancha leu.

Cada campo vem com **confiança própria**. Na planilha, célula vermelha é abaixo
de 0.60, amarela abaixo de 0.85, cinza é campo ausente no carimbo. Você revisa
o vermelho, não o lote.

## Montar o ambiente

Nada aqui exige AVX2, GPU ou compilação — roda igual no Mac Pro 2013 e no
MacBook Pro 2011.

### 1. Python 3.10 ou mais novo

O Python que vem com o macOS não serve. Confira o que você tem:

```bash
python3 --version
```

Se for menor que 3.10, instale pelo site oficial (python.org, instalador
universal2, funciona de Monterey em diante). **Prefira o instalador do
python.org ao Homebrew**: ele já traz o Tcl/Tk, e sem isso a janela do app não
abre. Se você usa Homebrew mesmo assim, precisa também de `brew install
python-tk`.

Teste rápido de que a interface vai funcionar:

```bash
python3 -m tkinter        # tem que abrir uma janelinha de teste
```

### 2. Pasta do projeto e ambiente virtual

Descompacte o `campvision2.zip` num lugar **fixo e fora da Lixeira** — por
exemplo `~/Aplicativos/campvision2`. (Sim, isso já aconteceu: builds rodando da
Lixeira por semanas. A barra de título mostra o build; confira que bate com o
que você instalou.)

```bash
cd ~/Aplicativos/campvision2
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

O ambiente virtual isola isto do Python onde mora o CAMP Vision antigo — os
dois podem conviver na mesma máquina sem conflito de versão, que era metade da
dor de cabeça com `numpy` e `imagecodecs`.

Toda vez que for usar, ative de novo:

```bash
cd ~/Aplicativos/campvision2 && source .venv/bin/activate
```

### 3. Chave da API

Crie a chave no console da Anthropic e coloque no seu perfil do shell, para
não ter que exportar toda vez:

```bash
echo 'export ANTHROPIC_API_KEY="sk-ant-..."' >> ~/.zshrc
source ~/.zshrc
```

Confira: `echo $ANTHROPIC_API_KEY` tem que devolver a chave.

**Atenção para o modo automático:** um LaunchAgent **não lê o seu `~/.zshrc`**.
Se for rodar o `cli.py` pelo watcher, declare a chave no próprio plist
(`EnvironmentVariables`) ou preencha `api_key` em
`~/.campvision2/config.json` — este arquivo só grava a chave se você a escrever
lá manualmente; o app nunca a persiste sozinho.

### 4. exiftool (opcional, só para a Fase 2)

Sem ele o app funciona normalmente, só pula a gravação de metadados na imagem:

```bash
brew install exiftool
```

### 5. Conferir que está tudo de pé

```bash
python -m unittest discover -s tests -t .   # 27 testes, sem rede
python app.py                                # abre a janela
```

### Uso

```bash
python app.py                          # janela
python cli.py /caminho/da/pasta        # modo automático, para o LaunchAgent
```

### Se der errado

| Sintoma | Causa |
|---|---|
| `ModuleNotFoundError: tkinter` | Python do Homebrew sem Tcl/Tk — instale pelo python.org ou `brew install python-tk` |
| "Sem chave de API" | `ANTHROPIC_API_KEY` não chegou ao processo; num LaunchAgent, veja o passo 3 |
| `ModuleNotFoundError: anthropic` | ambiente virtual não ativado — falta o `source .venv/bin/activate` |
| Build na barra de título não é o que você instalou | está rodando de outra pasta (Lixeira, Downloads antigo) |
| Lote não reprocessa nada | checkpoint da execução anterior — apague `campvision2_checkpoint.jsonl` da pasta |

## Custo

Sonnet 5 custa US$ 2 por milhão de tokens de entrada e US$ 10 de saída. Uma
prancha usa ~2.300 tokens de imagem por passe. Na prática, com o cache de região
funcionando, dá **algo em torno de US$ 10 a 15 por mil pranchas**. A janela
mostra a estimativa ao vivo durante o lote; a tabela de preços fica em
`nucleo/config.py` e você atualiza lá se mudar.

Para lotes grandes sem pressa, a Batch API tira 50% — vale plugar depois, o
código já isola a chamada em `ClienteAnthropic.chamar`.

## Mexer no que importa

- **Acrescentar campo ao carimbo:** só `nucleo/esquema.py`. Ele se propaga
  sozinho para o schema da API, a planilha e a consolidação.
- **Mudar a convenção de nome/pasta:** `montar_nome` e `montar_pasta` em
  `nucleo/aplicar.py`.
- **Ajustar as instruções de leitura:** a constante `INSTRUCOES` em
  `nucleo/visao.py`. É onde o comportamento do modelo mora — mais barato de
  iterar que qualquer código.

## Testes

```bash
python -m unittest discover -s tests -t .
```

27 testes, nenhum toca a rede: o cliente de API é falso e as pranchas são
geradas na hora.

## Segurança do lote

- **Checkpoint** (`campvision2_checkpoint.jsonl`) a cada prancha terminada: queda
  de energia, disco cheio ou cancelamento não perdem o que já foi lido nem
  custam API de novo. Apague o arquivo para reprocessar do zero.
- **Nada é sobrescrito** na Fase 2: nome que já existe ganha ` (2)`.
- **Erro de leitura não vira exceção**: vira linha na planilha com a coluna Erro
  preenchida, e o lote segue.
