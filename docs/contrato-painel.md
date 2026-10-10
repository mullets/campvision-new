# Contrato CAMP Vision 2 ↔ Painel de administração

**Contrato 2.1 — 10/10/2026.** Este arquivo é a **fonte única** do contrato. O painel só lê este arquivo e não edita o repositório do CV2; o `docs/campvision.md` do camp-painel aponta para cá.

- Versão do contrato no código: `CONTRATO` em `nucleo/config.py`. O heartbeat leva `"contrato": "2.1"`.
- Exemplos **reais**, gerados pelo CV2, ficam em `docs/contrato/exemplos/`: `pacote_tainacan.json`, `erros.json`, `propostas.json`, `decisoes.json`, `relatorio.txt`, `orientacao.txt`, `status.json`, `info_projeto.json`, e `http.json` com os corpos de heartbeat, reservar, aviso, iniciado e concluido.
  - Para regenerar: `python -m tests.contrato_exemplos`.
- Regra: o teste `TestContrato` compara chaves e tipos do que o CV2 grava hoje com esses exemplos. **Mudou um campo, sobe `CONTRATO` e regenera os exemplos no mesmo commit.**
- O histórico (versão 1 de 07/10 e acréscimos das seções 9 a 16) continua abaixo.

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

## 4b. Livro de registro (CV2 escreve; painel lê para o "Histórico")

`ACERVOS_CAMP/_campvision/registro/AAAA-MM.jsonl` (+ espelho `AAAA-MM.csv`),
append-only, uma linha por ação por arquivo:

```json
{"quando": "2026-10-07T18:39:12-03:00", "acao": "copiado", "lote_id": "2026-10-07-contex1-001",
 "codigo_projeto": "F002-P0002", "codigo_documento": "F002-P0002-1975-S01-D00017",
 "arquivo_origem": "100 - Scanners/F002 BSG/Pranchas/DEST352844.tif",
 "arquivo_destino": "F002 - BSG Barretto Segnini/01 - Projetos/F002-P0002 - Residência X/01 - Desenhos e pranchas/F002-P0002-1975-S01-D00017.tif",
 "nome_original": "DEST352844.tif", "tamanho": 20811234, "sha256": "…",
 "operador": "Beatriz", "estacao": "Contex 1", "versao_cv2": "2026-10-07-01", "detalhe": ""}
```

`acao` ∈ recebido, copiado, conferido, renomeado, lido, exif_gravado,
apagado_original, erro, refeito, legado.

- O painel lê de forma incremental (guarda a posição em bytes do arquivo do mês)
  e mostra no "Histórico" do projeto e do documento.
- O painel **nunca** escreve nesta pasta. Linha nunca é alterada; correção é linha nova.
- `_campvision/` começa com `_`: a varredura de projetos do painel ignora.

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
- [ ] Livro de registro do item 4b, no QNAP.

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
- [ ] Ler `_campvision/registro/` (incremental) → "Histórico" do projeto/documento; ignorar `_campvision/` na varredura.

## 8. Teste de ponta a ponta

1. Estação manda lote de 3 folhas `teste: true` para `100 - Scanners`.
2. CV2: `/reservar` → pasta criada → `processando` → aviso → painel mostra em processamento.
3. CV2 termina → `pronto` → aviso → painel cria o lote em revisão com 3 folhas, contatos e 0 erros.
4. Desligar o painel, mandar outro lote: CV2 conclui; ao religar, o aviso
   pendente chega e o painel atualiza sem varredura completa.
5. Lote com fundo `F099`: CV2 não copia, painel não vê nada, erro no heartbeat/log do CV2.

## 9. Versão 2 — método de leitura (08/10/2026)

Implementa o método `claude/campvision-metodo-de-leitura.md` (projeto Tainacam).
O que muda para o painel:

### 9.1 `/api/estacoes/contexto` — titular e coautores do fundo

Para a checagem de **autoria divergente** (§4.4), cada fundo devolvido deve trazer
os coautores/sócios registrados:

```json
{"fundos": [{"codigo": "F002", "sigla": "BSG", "nome": "Barretto Segnini",
             "coautores": ["Joaquim Barretto", "Francisco Segnini Jr."]}]}
```

Sem `coautores`, o CV2 compara só com o nome do fundo. Período de atuação
(fundos de família, F015 × F016) ainda não é usado — fica para a v3.

### 9.2 Preview para o site

`ACERVOS_CAMP/_campvision/preview/<F0xx-P000x>/<código do documento>.jpg` —
~3000 px, JPEG 85, sRGB, **já girado/desespelhado**. Fica fora da pasta do
projeto para não entrar na contagem de imagens.

### 9.3 `catalogacao/pacote_tainacan.json`

Um item por documento: `codigo`, `serie`, `modo` (prancha|fotografia), `titulo`,
`tipo_de_desenho`, `ano`, `ano_do_projeto`, `metadados`, `alternativas`, `onde`,
`transcricao_integral`, `materiais_citados`, `foto`, `credito`, `preview`,
`arquivos`, `publicavel`, `bloqueios`, `ressalvas`.

O CV2 **não** inventa ID de taxonomia: o painel converte `serie`/`tipo_de_desenho`
no ID do Tainacan e grava como **número** (`{"values": 26}`; string ou lista dá 400).
Publicar só itens com `publicavel: true`.

### 9.4 Aceite do lote

`catalogacao/lotes/<lote>.json` ganha `aceite`: `contagem_origem_igual_saida`,
`sem_autoria_divergente`, `orientacao_incerta_zerada`, `exif_conferido`,
`publicavel` e `pendencias`. O `status` continua `pronto` quando o trabalho do CV2
acabou; o painel usa `aceite.publicavel` + `erros_bloqueantes` para liberar.

### 9.5 Erros novos em `erros.json`

`autoria divergente` (bloqueia, agora contra titular + coautores),
`duplicata` (exata ou perceptual, corrigir), `espelhado` (aviso, preview já
corrigido), `orientação` (aviso: orientação incerta, conferir na folha de
contatos), `metadado` com ressalvas de leitura (ex.: "ano lido 1985, a série
indica 1983 — conferir no original").

## 10. Procedimento padrão (versão 2026-10-08-05)

Novidades no `pacote_tainacan.json`, por item:

- `arquivo_origem`: lista `{arquivo_origem, nome_original}` (pasta + nome original de cada versão). É a chave para reler sem pagar de novo.
- `origem_formato`: formato de onde a leitura saiu (`tif`, `jpg`, `pdf`, `dng`, `nef`, `cdr`).
- `serie_incerta`: a triagem não decidiu a série; olhar na folha de contatos.
- `pessoas_identificadas`: nomes vindos só das `chaves_de_identidade` do fundo, aplicadas por código.
- `textual`: na série S02, tipo documental, remetente, destinatário, data e assunto.
- `fora_do_periodo`: o ano cai fora do período de atuação do fundo.
- `bloqueios` agora inclui `projeto divergente`.

Novos arquivos em `catalogacao/`: `relatorio.txt` (as 8 partes da etapa 8), `orientacao.txt` (giro aplicado folha por folha) e `catalogacao_ERRO.txt` (só quando o lote falha).

Os parâmetros por fundo são escritos por humano em `ACERVOS_CAMP/_campvision/fundos/F0xx.json`; o modelo em branco está em `docs/fundos/MODELO.json` (copie com o código do fundo no nome: `F007.json`). O painel também pode mandar `periodo_atuacao` e `chaves_de_identidade` em `/api/estacoes/contexto`.

## 11. Tickets CV (versão 2026-10-08-07): pacote_tainacan v2

O `pacote_tainacan.json` passa a seguir o esquema D.3, com `"versao": 2`.

- `fundo`: código, sigla, nome, coautores e período de atuação.
- `projeto`: título, ano, cidade, UF, cliente e endereço.
  - `fontes` guarda o valor de cada fonte (pasta, info_projeto, carimbo).
  - `lacunas`: quando as fontes discordam, o campo fica vazio e o motivo vai aqui (CV-13).
  - `programa_sugerido`, `natureza_sugerida` e `sugestao`: são sempre sugestões; quem grava é gente.
  - `capa_sugerida`.
- `documentos` (o antigo `itens` continua igual): `titulo` já limpo (CV-21) e `titulo_lido` literal; `codigo_unidade`, `revisao`; `data_lida`, `data_iso`, `data_sugerida`, `data_outlier`; `rastreio` (de onde veio cada campo); `fotografo` (do `info_projeto.json`, senão "fotógrafo não identificado"); `confianca_rotacao`; `projeto_divergente`; `decisao`.
- `retirados`: folhas com autoria divergente ou retiradas por decisão humana. Não entram em `documentos` (CV-06 e CV-25).
- `propostas`: obras distintas na pasta pelo código de unidade (CV-08) e fotos sem obra agrupadas por semelhança (CV-22). A mesma coisa vai em `catalogacao/propostas.json`.
- `teste: true`: lote de teste; nenhum documento é publicável.

Em `catalogacao/`, `decisoes.json` é gravado por `vigia.py --decisao CODIGO --acao retirar|publicar|nota --motivo "..." --por NOME`. A decisão entra no livro como `decisao`, e nada é apagado.

**Lado do site (CV-01):** criar na coleção Documentos (8013) um metadado de texto "Arquivo de origem" e gravar nele `documentos[].arquivo_origem` (pasta + nome original). O CV2 já manda esse dado; falta o importador do painel gravar.

`reservar` agora envia `proximo_p_local` (o maior P no acervo + 1). Se o painel devolver um P que já é de outra pasta, o lote espera (CV-27).

## 12. Pedido de releitura — "Ler dados agora" (versão 2026-10-09-04)

Segue o §14 de `docs/campvision.md` do camp-painel, que já está implementado lá. O fluxo continua de mão única.

1. A cada rodada o CV2 consulta `GET /api/estacoes/pedidos-releitura?estacao=campvision2`, que devolve `{"pedidos":[{id, escopo: "folha"|"projeto", projeto_codigo, item_codigo, motivo, ...}]}`. Se o painel não tiver essa rota (404), o CV2 ignora.
2. Antes de ler, o CV2 manda `POST .../{id}/iniciado` com `{"estacao":"campvision2"}`. Se a resposta for **409**, o pedido foi cancelado: o CV2 não lê, não gasta API e registra `releitura_cancelada` no livro.
3. Relê só o escopo pedido: a folha `item_codigo` ou o projeto inteiro. Regrava `catalogacao/leituras.json`, o CSV, o `pacote_tainacan.json` e o `erros.json` com a consolidação do grupo refeita.
   - A versão anterior fica em `catalogacao/releituras/AAAAMMDD-HHMMSS/`: `antes_leituras.json`, `antes_pacote.json`, `antes_catalogacao.csv`, o `leituras.json` novo e o `comparacao.json` (antes × agora, campo a campo).
   - Uma leitura que falhou não substitui a que já existe.
4. No fim manda `POST .../{id}/concluido` com `{"ok", "mensagem", ...resumo}`. Com `ok:true` o painel reimporta o pacote, protegendo o que já foi revisado ou publicado.

Pela linha de comando: `vigia.py --reler F0xx-P000x [--escopo vazios|projeto|documentos] [--documentos COD,COD]`. O escopo `vazios`, que relê só as folhas com campo vazio ou erro, existe apenas na linha de comando. No livro de registro a ação fica como `relido`.

## 13. Número P escrito na estação (versão 2026-10-09-05)

Decisão do Rafa em 09/10: o "P0001" que a estação escreve (no `info_projeto.json` ou numa pasta "P0001 - Nome") **não é oficial**. O código oficial vem sempre da reserva no painel. O número da estação vira pista (`contexto.p_estacao`) e vai na reserva como `identificacao_original: "P0001 - Nome"`, para ajudar quem decide. Um código completo `F0xx-P000x` no info/manifesto continua valendo como oficial.


## 14. Execução: versão, modelo e custo (contrato 2.1, ticket 89)

`pacote_tainacan.json` ganha `execucao`, e `catalogacao/lotes/<lote>.json` também:

```json
{"versao_cv2": "2026-10-10-02+ab12cd3", "contrato": "2.1", "modelo": "claude-…",
 "versao_prompt": {"prancha": "1a2b3c4d", "fotografia": "…", "textual": "…"},
 "chamadas": 12, "tokens_entrada": 36000, "tokens_saida": 9000, "custo_usd": 0.42, "tempo_s": 95.3}
```

- Cada documento do pacote traz `versao_cv2`, `versao_prompt` (por exemplo `prancha:1a2b3c4d`) e `modelo` da leitura que vale. Uma releitura atualiza esses campos.
- `relatorio.txt` ganha a linha `Execução: …` com os mesmos números.
- `versao_prompt` é o hash do texto do prompt: mudou o prompt, muda a versão.
- `vigia.py --avaliar gabarito.csv` (colunas `arquivo_origem`, `campo`, `valor_correto`, exportado pelo painel) relê as folhas do gabarito **sem gravar nada no acervo** e mostra o placar por campo e o custo.

## 15. Quarentena da entrada e réplica (contrato 2.1, ticket 87)

- O original em `100 - Scanners` vai para `100 - Scanners/_conferidos/AAAA-MM-DD/<lote>/` em vez de ser apagado. No livro: `quarentena`.
- Só é apagado (livro: `apagado_original`) quando duas condições valem:
  - a réplica confirmou: a sentinela `ACERVOS_CAMP/_campvision/sentinela.json`, regravada a cada rodada, aparece em `caminho_replica` com data igual ou posterior à quarentena;
  - passaram `quarentena_dias` (padrão 7). Com o disco acima de 85%, o que já tem réplica pode sair antes.
- Sem `caminho_replica`, nada é apagado.
- O heartbeat ganha `"quarentena": {"arquivos", "gb", "ultima_replica", "aviso"}`. O `aviso` diz "quarentena sem réplica: X GB" quando não há réplica confirmada.
- `vigia.py --diagnostico` mostra a quarentena e a data da última réplica confirmada.

## 16. Aviso importa as folhas e orientação corrigida

- `POST /api/campvision/aviso` de um lote em revisão faz o painel importar o pacote (camp-painel c705124), com as mesmas garantias da importação manual.
- `documentos[].orientacao_corrigida = true`: o JPG do acervo já está girado e desespelhado. O painel não marca a folha como espelhada nem girada.
