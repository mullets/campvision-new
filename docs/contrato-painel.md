# Contrato CAMP Vision 2 ↔ Painel de administração

Versão 1 — 07/10/2026. Vale para os dois repositórios: `campvision-new` (CV2) e
`camp-painel`. Mudou aqui, muda nos dois.

```
estações ──► QNAP 100 - Scanners ──► CV2 (.40) ──► QNAP ACERVOS_CAMP ──► Painel (.60) ──► site
                                      │   ▲                                 ▲
                                      │   └── GET contexto / POST reservar ─┤
                                      └────── POST heartbeat / POST aviso ──┘
```

Regras gerais:

- **Os arquivos no QNAP são a fonte da verdade.** O HTTP só acelera e informa;
  se uma chamada se perder, o painel ainda acha tudo varrendo a pasta.
- O painel **só lê** `ACERVOS_CAMP`. Nunca lê `100 - Scanners` e nunca escreve
  em pasta de projeto.
- O CV2 **nunca** inventa código de fundo nem número P: fundo vem da tabela do
  painel, P vem do `/reservar`.
- Toda chamada HTTP é só na rede local, com `X-Camp-Token` (obrigatório quando
  o painel tiver token configurado).

---

## 1. Pasta final (o que o CV2 escreve, o que o painel lê)

Rasa e fixa — o painel varre sem precisar descer mais que isto:

```
ACERVOS_CAMP/
  F002 - BSG Barretto Segnini/
    01 - Projetos/
      F002-P0002 - Residência X/            ◄── 1 projeto = 1 "lote" para o painel
        info_projeto.json
        status.json
        01 - Desenhos e pranchas/   F002-P0002-1975-S01-D00001.tif / .jpg / .dng
        02 - Documentos textuais/   ...
        03 - Fotografias/ 04 - Negativos/ 05 - Slides/
        06 - Materiais e especificações/ 07 - Publicações/ 99 - Não identificado/
        README.md
        catalogacao/
          catalogacao.csv
          contatos.jpg
          erros.json
          lotes/<lote_id>.json
    02 - Obras e documentação de obra/ ... 99 - Não identificado/   (nível do fundo)
```

Profundidade máxima para o painel: `<Fundo>/01 - Projetos/<Projeto>/`. O painel
**não** precisa descer em `catalogacao/` nem nas séries para achar projetos — só
para contar imagens do projeto que ele já achou.

## 2. `info_projeto.json` (CV2 escreve, painel lê)

Chaves no nível de cima, nomes exatamente estes:

```json
{
  "codigo": "F002-P0002",
  "nome": "Residência X",
  "fundo_codigo": "F002",
  "ano": "1975",
  "folhas_esperadas": 48,
  "estacao": "Contex 1",
  "tipo_estacao": "contex",
  "operador": "Beatriz",
  "operador_email": "beatriz@camp.arq.br",
  "teste": false,
  "atualizado_em": "2026-10-07T18:40:00-03:00",
  "gerado_por": "CAMP Vision 2 2026-10-07-01"
}
```

- `codigo` é obrigatório e tem que existir no painel (veio do `/reservar` ou já
  existia). Sem ele o painel ignora o projeto.
- `folhas_esperadas` = total do projeto (soma de todos os lotes recebidos), não
  só do último.
- `estacao`/`operador` = do **último** lote. O histórico completo está em
  `catalogacao/lotes/`.
- Lote de teste: `"teste": true` e o painel deixa fora do inventário.

## 3. `status.json` (CV2 escreve, painel lê)

```json
{
  "codigo": "F002-P0002",
  "status": "pronto",
  "status_em": "2026-10-07T18:40:00-03:00",
  "lote_atual": "2026-10-07-contex1-001",
  "lotes_prontos": ["2026-10-01-contex1-004", "2026-10-07-contex1-001"],
  "imagens": 96,
  "folhas": 48,
  "com_carimbo": 45,
  "a_revisar": 7,
  "erros_bloqueantes": 0,
  "versao_cv2": "2026-10-07-01"
}
```

Valores de `status`, nesta ordem:

| status | quer dizer | o painel faz |
|---|---|---|
| `processando` | CV2 copiando/lendo/gravando EXIF | mostra em "processando", **não** cria lote em revisão |
| `pronto` | tudo concluído: cópia conferida, EXIF, `catalogacao/` completa | cria/atualiza o lote em **revisão** |
| `erro` | parou; motivo em `catalogacao/erros.json` | mostra em erros, não avança |

- `pronto` é escrito **por último**, com gravação atômica (temporário + rename).
  Arquivo pela metade nunca existe.
- Lote novo num projeto já pronto volta o status para `processando` e depois
  `pronto` de novo. O painel deve tratar a volta para `processando` como
  "material chegando", sem apagar a revisão já feita.
- Chaves antigas (`enviado_windows`, `campvision_concluido`, `fase`) podem
  continuar no arquivo por compatibilidade; o painel olha só `status`.

## 4. `catalogacao/` (CV2 escreve; painel passa a ler)

- `catalogacao.csv` — uma linha por documento (código, arquivo, campos do
  carimbo, confiança, `revisar` sim/não). Cabeçalho fixo, UTF-8 com BOM.
- `contatos.jpg` — folha de contatos com miniaturas numeradas pelo D.
- `erros.json` — erros por arquivo, no vocabulário do painel:

```json
[
  {
    "arquivo": "F002-P0002-1975-S01-D00017.tif",
    "categoria": "autoria divergente",
    "gravidade": "bloqueia",
    "origem": "CAMP Vision",
    "detalhe": "carimbo diz Joaquim Barretto Arquitetos Associados"
  }
]
```

  `categoria` ∈ autoria divergente, projeto errado, duplicata, orientação,
  espelhado, código, metadado, crédito, arquivo corrompido.
  `gravidade` ∈ bloqueia, corrigir, aviso.

- `lotes/<lote_id>.json` — histórico de cada recebimento (origem em
  `100 - Scanners`, nome original → nome final, contagens, EXIF).

## 5. HTTP — CV2 chama o painel

Base: `http://192.168.15.60:8000`. Cabeçalho `X-Camp-Token`.

### Já existem no painel

| Chamada | Quando o CV2 usa |
|---|---|
| `GET /api/estacoes/contexto?fundo=F002` | ao receber lote: valida fundo, pega nome do fundo; cache local para quando o painel estiver fora |
| `POST /api/estacoes/projetos/reservar` | projeto novo: `fundo_codigo`, `titulo`, `ano`, `cidade`, `identificacao_original` (nome da pasta no scanner), `operador`, `chave_reserva` (uuid hex 32, gravada no estado do lote **antes** da chamada) |
| `POST /api/estacoes/heartbeat` | a cada 60 s |

Heartbeat do CV2:

```json
{
  "estacao_id": "campvision2",
  "tipo_estacao": "campvision",
  "app": "CAMP Vision 2",
  "versao": "2026-10-07-01",
  "estado": "processando",
  "projeto": "F002-P0002",
  "progresso": {"feitos": 31, "total": 48},
  "fila": 3,
  "hoje": {"projetos": 2, "imagens": 140, "erros": 1, "custo_usd": 1.84},
  "montagens": {"entrada": true, "acervo": true}
}
```

`estado` ∈ `vigiando`, `processando`, `pasta indisponível`, `erro`.

### Novo no painel

`POST /api/campvision/aviso` — "acabei de mudar este projeto, olhe só ele".

```json
{"codigo": "F002-P0002", "pasta": "F002 - BSG Barretto Segnini/01 - Projetos/F002-P0002 - Residência X", "status": "pronto"}
```

Resposta `202`. O painel relê **só essa pasta** (info, status, catalogacao/) em
vez de varrer o QNAP inteiro. O CV2 chama em toda troca de `status`.

### Quando o painel está fora

O CV2 guarda avisos e heartbeats perdidos em fila local e reenvia; o aviso mais
recente de cada projeto basta. Nada no CV2 espera o painel para continuar —
exceto o `/reservar`, sem o qual o projeto novo fica `processando` aguardando
número (e o lote **não** é copiado com código inventado).

## 6. Lado do CV2 — o que fazer

- [ ] Gravar `info_projeto.json` e `status.json` neste formato na pasta do projeto.
- [ ] `processando` no início, `pronto`/`erro` no fim, atômico.
- [ ] `catalogacao/erros.json` com as categorias do painel; `contatos.jpg`.
- [ ] Cliente HTTP com token, fila de reenvio, timeout curto.
- [ ] `/contexto` para fundos, `/reservar` para P, `/heartbeat` a cada 60 s,
      `/api/campvision/aviso` a cada troca de status.
- [ ] Estrutura de pasta do item 1, rasa.

## 7. Lado do painel — o que fazer

- [ ] Só criar lote em revisão com `status == "pronto"`; `processando` e `erro`
      aparecem nas filas sem virar revisão.
- [ ] Ler `status.json` novo (`lote_atual`, contagens, `erros_bloqueantes`).
- [ ] Endpoint `POST /api/campvision/aviso` relendo uma pasta só.
- [ ] Varredura limitada a `<Fundo>/01 - Projetos/<Projeto>/` com tempo máximo;
      a varredura completa vira só reconciliação noturna.
- [ ] Ler `catalogacao/erros.json` → "Erros relatados" (origem CAMP Vision).
- [ ] Ler `catalogacao.csv` e `contatos.jpg` → revisão pós-CAMP Vision.
- [ ] Cadastrar a estação `campvision2` (.40) e o token.
- [ ] Mostrar o heartbeat do CV2 no topo (já existe para estações).

## 8. Teste de ponta a ponta

1. Estação manda lote de 3 folhas `teste: true` para `100 - Scanners`.
2. CV2: `/reservar` → pasta criada → `processando` → aviso → painel mostra em processamento.
3. CV2 termina → `pronto` → aviso → painel cria o lote em revisão com 3 folhas, contatos e 0 erros.
4. Desligar o painel, mandar outro lote: CV2 conclui; ao religar, o aviso
   pendente chega e o painel atualiza sem varredura completa.
5. Lote com fundo `F099`: CV2 não copia, painel não vê nada, erro no heartbeat/log do CV2.
