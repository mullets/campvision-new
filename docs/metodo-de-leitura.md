# CAMP Vision 2 — método de leitura de acervo

Especificação derivada do que funcionou na catalogação manual assistida dos fundos F016
(Bellucci, 118 pranchas + 58 fotografias) e F023 (Oswaldo Corrêa Gonçalves, 1.042 pranchas
classificadas), setembro de 2026.

Não é um apanhado de ideias: cada regra abaixo veio de um acerto medido ou de um erro cometido,
e os dois estão identificados no texto. O que não foi medido está marcado como **hipótese**.

---

## 0. O princípio

> **Uma leitura, página inteira, resolução média. O carimbo não é localizado — é lido junto com
> o resto.**

Todo o estágio "detectar carimbo → recortar → OCR no recorte" deve **deixar de existir**. Ele é
herança da arquitetura YOLO + Tesseract e é a origem direta dos builds 56 a 67: fusão de caixas,
candidato único sem verificação, quatro rotações, limiar de conteúdo 0.40, limiar mínimo
geométrico. Todos esses são bugs de um estágio que um modelo de visão torna desnecessário.

Pior: o recorte **destrói dado**. Nas pranchas do F016, informação essencial estava fora do
carimbo — data a lápis na margem (`14.10.83`), número de folha no contorno (`F.34`, `folha 22`),
código de série (`FL 1/6`), monograma de autoria nas perspectivas (`JB`/`JCB`), amostras de tinta
coladas com o nome do padrão escrito ao lado (`Perstorp Berilo`), e o nome do estabelecimento
vizinho usado para situar a obra (`VALE REAL`). Recortar o carimbo perde tudo isso.

---

## 1. Arquitetura em quatro passadas

A ordem é parte da especificação. Hoje o CV2 faz 1 e 2 juntos e não faz 3.

| # | passada | natureza | o que faz |
|---|---|---|---|
| 1 | **Preparo** | código | reduz, resolve orientação, calcula hashes |
| 2 | **Leitura** | modelo de visão | uma chamada por folha, folha inteira, JSON estrito |
| 3 | **Consolidação** | código | consenso por grupo, outliers, duplicatas, autoria divergente |
| 4 | **Derivação e saída** | código | regras sobre texto já lido, nomes finais, pacote de publicação |

**A passada 3 é a que não existe hoje e a que mais muda o resultado.** As passadas 1, 3 e 4 são
código determinístico — nenhuma chamada de modelo. O modelo entra só na 2.

---

## 2. Passada 1 — preparo

### 2.1 Resolução

**2000 px no lado maior, JPEG qualidade 78.** Fica em torno de 240 KB por folha.

Foi nessa resolução que as 118 pranchas do McDonald's foram lidas, com acerto em carimbo, datas
manuscritas, códigos de folha e monogramas. Não há ganho medido acima disso para leitura, e há
perda enorme de tempo: o TIFF `DEST352441_DEST352442_unido.tif` tem 18947×13398 (254 MP).

```
convert <origem> -resize 2000x2000\> -quality 78 <leitura.jpg>
```

O master (TIFF/DNG) nunca é alterado nem reduzido. A redução é artefato descartável de leitura.

**Hipótese não medida:** pranchas A0 com texto de carimbo muito pequeno podem exigir 3000 px.
Medir antes de mudar o padrão — subir resolução "por segurança" é o que tornava o lote de 6 horas.

### 2.2 Orientação — pela leitura, nunca pela geometria

Está registrado que o campo `Rotação` do CV2 não é confiável: Safra Mooca (13 folhas), Paróquia
BSG (10 folhas) e um slide do Barillari foram girados à mão, e as pranchas da Residência Cynthia
estavam digitalizadas pelo verso.

A pontuação geométrica **não serve** para escolher rotação — já foi medido: sai ~0.70 idêntico
nas 4 rotações, porque carimbo com grade interna é simétrico demais.

Procedimento:

1. Gerar as 4 rotações (0/90/180/270) da imagem **já reduzida**.
2. Para cada uma, extrair texto e pontuar: fração de tokens que são palavras de português ou
   números em formato plausível (data, escala `1:50`, código `F.34`).
3. Vence a rotação de maior pontuação. Empate técnico (diferença < 15%) → marcar
   `orientacao_incerta: true` e mandar para revisão humana.
4. **Espelhamento:** se a melhor pontuação das 4 rotações ficar abaixo do piso, testar as 4
   rotações da imagem espelhada. Texto espelhado não pontua como português — é esse o sinal.

O preview que vai para o site sai **já girado** e com o nome final do código, como você pediu.

### 2.3 Hashes, na entrada

Calcular na passada 1, não depois:

- `md5` do arquivo — duplicata byte a byte. Já foram achadas em A. Abreu, José Baia, Araruama,
  Banespa A-9, Safra D27/D28 e Barillari CD03.
- **hash perceptual** — mesma folha digitalizada duas vezes com bytes diferentes. É o caso das
  pranchas 0085 e 0086 do McDonald's, e foi detectado por olho, não por código.

Nada é apagado. Marca-se `duplicata_de` e `tipo_duplicata` (`exata` | `perceptual`), e só a
única vai para o site.

### 2.4 Chave de identidade

**Chavear sempre por `pasta + nome do arquivo`, nunca pelo nome só.** Colisão confirmada:
`Jose-Carlos-Bellucci-2025-02-04-0001.jpg` existe em Abílio Diniz (3.464.390 bytes) e em Giorgi
(3.464.394 bytes) — arquivos diferentes, obras diferentes, mesmo nome.

### 2.5 Higiene de entrada

- **Limpar o EXIF herdado do scanner.** Cinco pranchas do F016 subiram com o título interno
  "Acervo dos Arquitetos — Ruth Verde Zein", sobra de um lote anterior, e o WordPress preferiu
  esse título ao nome do arquivo. Se não for limpo na origem, volta em todo lote.
- **Ignorar lotes de teste.** Pastas com nome `teste`, `asd`, `Sei la` entram no inventário como
  se fossem projeto. Respeitar `teste: true` no `info_projeto.json`.

---

## 3. Passada 2 — leitura

Uma chamada por folha. Página inteira. Saída em JSON estrito. Sem recorte, sem localização
prévia de região.

### 3.1 O prompt (prancha)

```
Você vai ler UMA prancha de arquitetura digitalizada e TRANSCREVER o que está
escrito nela. Você não interpreta, não completa e não supõe.

REGRAS

1. Transcreva literalmente: grafia, acentuação e pontuação como estão na folha,
   inclusive erros e abreviações. "Res." não se torna "Residência".
2. Leia a folha INTEIRA, não só o carimbo: legendas, anotações manuscritas,
   número de folha no contorno, selos, datas a lápis na margem, códigos de
   série, amostras de material com o nome escrito ao lado.
3. Campo que você não conseguir ler fica null. NUNCA preencha por
   plausibilidade, simetria com outros campos ou conhecimento de arquitetura.
4. Se houver duas leituras possíveis para o mesmo texto, devolva a mais
   provável em "valor" e a outra em "alternativas". Dígito ambíguo (3/5/8,
   1/7, 0/6) é o caso mais comum — sempre devolva a alternativa.
5. NÃO use conhecimento externo. Se o nome do arquiteto não está escrito na
   folha, o campo é null, mesmo que você reconheça a obra.
6. Não descreva o projeto, não classifique estilo, não diga em que ano
   "deve" ter sido feito.

Devolva SOMENTE o JSON do esquema, sem texto antes ou depois.
```

### 3.2 Esquema de saída

Todo campo é um objeto `{valor, confianca, alternativas, onde}` — nunca um escalar solto.
`confianca` é 0 a 1 e vem do modelo. `onde` diz em que parte da folha foi lido
(`carimbo` | `margem` | `contorno` | `manuscrito` | `legenda` | `corpo`), porque dado de
carimbo e dado manuscrito merecem peso diferente na consolidação.

```json
{
  "legivel": true,
  "tem_carimbo": true,
  "campos": {
    "projeto_carimbo":   {"valor": null, "confianca": 0.0, "alternativas": [], "onde": null},
    "cliente":           {"valor": null, "confianca": 0.0, "alternativas": [], "onde": null},
    "arquiteto":         {"valor": null, "confianca": 0.0, "alternativas": [], "onde": null},
    "escritorio":        {"valor": null, "confianca": 0.0, "alternativas": [], "onde": null},
    "endereco":          {"valor": null, "confianca": 0.0, "alternativas": [], "onde": null},
    "cidade":            {"valor": null, "confianca": 0.0, "alternativas": [], "onde": null},
    "uf":                {"valor": null, "confianca": 0.0, "alternativas": [], "onde": null},
    "data":              {"valor": null, "confianca": 0.0, "alternativas": [], "onde": null},
    "escala":            {"valor": null, "confianca": 0.0, "alternativas": [], "onde": null},
    "nome_prancha":      {"valor": null, "confianca": 0.0, "alternativas": [], "onde": null},
    "numero_folha":      {"valor": null, "confianca": 0.0, "alternativas": [], "onde": null},
    "total_folhas":      {"valor": null, "confianca": 0.0, "alternativas": [], "onde": null},
    "codigo_serie":      {"valor": null, "confianca": 0.0, "alternativas": [], "onde": null},
    "revisao":           {"valor": null, "confianca": 0.0, "alternativas": [], "onde": null}
  },
  "transcricao_integral": "",
  "materiais_citados": [],
  "anotacoes_manuscritas": [],
  "observacoes_de_leitura": []
}
```

Três campos que o CV2 não tem hoje e que resolvem problemas conhecidos:

- **`escritorio` separado de `arquiteto`.** O carimbo do F016 traz
  `ARQUITETO JOSE CARLOS BELLUCCI / BELLUCCI arquitetura SC` — são duas coisas. Sua anotação
  registra que a coluna "Escritório" do CSV vinha com `F002`, o código da pasta. Os três campos
  pedidos — `projeto_carimbo`, `projeto_pasta`, `escritorio_carimbo` — nunca devem se misturar;
  a reconciliação é da passada 3.
- **`revisao`.** `McD-1/R-1` é a reforma de 1983 do McDonald's da Paulista; `McD-1` é o projeto
  de 1980. Sem esse campo as duas viram a mesma coisa, e foi assim que o P0001 original nasceu
  com as 118 pranchas juntas.
- **`transcricao_integral`.** O texto corrido da folha. É barato de guardar e é a fonte para
  qualquer regra futura — foi dele que saíram os materiais que você elogiou, e é o que permite
  reclassificar um fundo inteiro sem reprocessar imagem.

### 3.3 `materiais_citados` — por que é transcrição e não conhecimento

A lista do F016 — `embuia`, `freijó`, `mogno Arteflex`, `Formica cerejeira`,
`terracota-843 rústico texturizado`, `Formiplac Itaipú-355`, `granito Ju-Paraná`, `aço inox #14`,
`alumínio anodizado bronze (perfis Alcan)`, `PVA Sherwin Williams/Excello`,
`Perstorp Berilo/Amendoim/Champagne/Iguaçu`, `Ideal Standard Carina/Paloma`, `Deca Tapajós` —
está **escrita nas pranchas**. Nenhum item veio de conhecimento sobre materiais de 1980.

A regra no prompt é literal: *transcreva o que está escrito; se não está escrito, não existe.*
É isso que faz a saída ser publicável.

---

## 4. Passada 3 — consolidação por grupo

**Esta é a passada que não existe e é a mais importante.** Hoje o CV2 processa arquivo por arquivo
em threads paralelas, com resultado independente por arquivo. Isso é incapaz de produzir o que
produziu a leitura manual, porque metade do que eu soube veio de **ler em ordem**:

- `McD-1/R-1` é reforma porque existe um `McD-1` antes dela na série.
- A série São Bento é numerada 1 a 10 — a folha 7 é a sétima porque as outras nove estão ali.
- As pranchas **0103, 0111 e 0112** foram lidas como "85". A 0113, da mesma série McD-9/JK, é
  `14.06.83`, e todas as outras da série são de 1983. **Leitura mais provável: 83.** Nenhuma foi
  publicada como 1985.

Esse último caso é a especificação inteira num exemplo: a leitura individual estava
razoavelmente confiante e **errada**, e só o grupo revelou isso.

### 4.1 Consenso

Para cada grupo (projeto detectado, não pasta — ver 4.2), e para cada campo de
`projeto / cliente / arquiteto / escritorio / endereco / cidade / uf / ano`:

```
valores = leituras não-nulas do campo no grupo
if valores está vazio:            campo_do_grupo = null
else:
    agrupar por valor normalizado (minúsculo, sem acento, sem pontuação)
    consenso = grupo mais frequente        # moda, não média
    campo_do_grupo = grafia mais completa dentro do grupo vencedor
    para cada leitura fora do consenso:
        marcar outlier_<campo> = true
        guardar valor_lido_original        # nunca sobrescrever em silêncio
```

Duas proteções que vêm de erros reais:

- **A grafia canônica congela.** `HOSWALDO CORREA GONÇALVES` (um `H` espúrio de OCR, uma letra
  mais longa) sequestrou a canônica de um arquiteto já confirmado centenas de vezes, porque a
  regra era "a grafia mais completa vence". Depois que um valor passa o limiar de confiança, ele
  **para** de ser substituível. Só muda enquanto está em quarentena.
- **Quarentena.** Valor visto pela primeira vez tem contagem 1 e **não** corrige outras leituras
  até ser confirmado por uma segunda. Isso impede que um erro de OCR isolado vire vocabulário.

### 4.2 Ano da pasta ≠ ano da prancha

Erro real já corrigido, mas que a especificação tem de carregar: o mesmo projeto ficou
fragmentado em 9 pastas de ano diferentes (1968/1959/1966/1927/1997/…) porque a pasta usava o ano
lido em **cada** prancha.

O ano da pasta é a **moda do grupo**. O ano de cada prancha fica intacto no CSV e no EXIF. Grupo
sem nenhum ano legível → `Ano desconhecido`, nunca um chute.

### 4.3 Pasta não é projeto

Medido e confirmado: a pasta "SENAC" do F023 tem 2 pranchas e elas foram catalogadas em **dois
projetos diferentes** (P0096 e P0098). A relação pasta↔projeto **não é 1:1**.

Consequência direta: qualquer tentativa de casar dado por "pasta → projeto, ordinal do arquivo →
número D" atribui valor errado. Foi testada e descartada. O agrupamento tem de sair do
`projeto_carimbo` lido, com a pasta como pista secundária.

### 4.4 Autoria divergente — a checagem de prioridade máxima

Dentro de uma pasta já foram encontradas folhas de **outra obra** (Araruama dentro de A. Abreu;
Jatiúca I dentro de José Baia) e de **outro arquiteto** (uma prancha de Salvador Candia dentro do
Safra Mooca). Publicar qualquer uma dessas é **atribuição falsa**.

Regra: se o `arquiteto` lido numa folha não é o titular do fundo nem um coautor já registrado,
a folha é **bloqueada para publicação** e marcada `autoria_divergente: true`. Nunca silenciosa,
nunca corrigida pelo consenso — o consenso é exatamente o que esconderia uma prancha intrusa num
grupo de 30.

Caso relacionado, do mesmo fundo: a relação de volumes de 1995 atribui obras sem autoria a José
Carlos Bellucci, e "R. Cardoso de Almeida 1969" é do **pai**, José Augusto (F015). Fundo de
família precisa da tabela de autoridade com **período de atuação** de cada um — item 1 da sua
própria lista de prioridades.

### 4.5 Contagem esperada contra presente

O lote **nunca termina sem catalogação**. Conta de arquivos na origem contra registros na saída;
diferença é erro de execução e tem de aparecer no relatório, não ser descoberta depois.

Precedente: um lote de 147 arquivos exportou 17 porque o disco encheu na Fase 2 e derrubou o
SQLite em cascata (264 ocorrências de `unable to open database file`). A verificação de espaço em
disco antes de começar já existe (build 62) — a de contagem no fim, não.

---

## 5. Passada 4 — derivação a partir do texto lido

Aqui não há modelo. São regras determinísticas sobre o texto que a passada 2 já transcreveu.

### 5.1 A distinção que autoriza tudo isso

Derivar o tipo de desenho do nome da prancha **não é adivinhação**: o nome da prancha é
transcrição do que está escrito na própria folha. Inferir dali é ler a fonte.

Inferir biografia, contexto, autoria ou "ano provável pelo estilo" **é** adivinhação, e está
proibido — a biografia publicada do Chu Ming gerou reclamação da família e o fundo inteiro saiu
do ar.

### 5.2 Tipo de desenho — regra validada em produção

15 detectores por palavra-chave sobre o texto minúsculo e sem acento. Independentes, podem
coocorrer. O resultado é a junção dos detectados, separados por ` / `, **limitado a 3**, nesta
ordem canônica:

> Implantação → Planta → Corte → Elevação → Fachada → Detalhe → Estrutura → Instalações →
> Mobiliário → Perspectiva → Croqui → Fotografia → Levantamento → Documento → Tabela

| termo | gatilhos |
|---|---|
| Implantação | implanta\*, situação, locação, urbanístic\*, urbaniz\*, loteamento |
| Planta | planta\*, pav/pavimento, térreo, andar, cobertura, lay-out, subsolo, mezanino, sobreloja |
| Corte | corte\*, seção |
| Elevação | elevaç\* |
| Fachada | fachada\* |
| Detalhe | detalhe\*, ampliaç\*, esquadria |
| Estrutura | estrutur\*, forma/formas, armaç\*, fundaç\*, viga\*, pilar\*, laje, concreto, sapata, baldrame |
| Instalações | instalaç\*, hidráulic\*, elétric\*, sanitári\*, esgoto, pluviais, telefon\*, ar condicionado, climatiz\*, gás, luminotéc\* |
| Mobiliário | mobili\*, marcenaria, móveis, armário, bancada |
| Perspectiva | perspectiva |
| Croqui | croqui, esboço |
| Fotografia | foto, fotos, fotografia |
| Levantamento | levantamento, curvas de nível, topografi\* |
| Documento | memorial, carta, correspondênc\*, contrato, ofício |
| Tabela | tabela, quadro de, planilha |

**Supressão:** Implantação detectado remove Planta — uma implantação já é uma planta.
`Planta de Situação` → `Implantação`, não `Implantação / Planta`.

**Abstenção:** nenhum gatilho → não grava nada. Nunca chutar.

Resultado em produção: 1.042 de 1.934 gravados, 0 erros de escrita, 0 divergências na
reconferência. Os 892 que ficaram em branco ficaram em branco de propósito — 501 porque o título
é só `Prancha 12`, 254 porque nomeiam o assunto e não o tipo (`Esquadrias de Ferro`,
`Cabine de Transformação`, `Luncheonete`). **Nenhum dos dois é inferível, e é exatamente aí que o
CV2 ganha:** a prancha "Luncheonete" ficou sem tipo pela regra de título, e o CV2 leu uma delas
como `Perspectiva`.

### 5.3 Ordem é a regra — do específico ao genérico

Na classificação de programa/uso, a ordem é o que faz funcionar. Resumo do que foi validado:

1. habitação específica (casa de veraneio, de praia, de campo, loteamento, conjunto)
2. **uso misto antes de edifício residencial**
3. prédio/edifício **de apartamentos** (plural) → edifício residencial
4. apartamento singular → unidade
5. hospedagem (apart-hotel antes de hotel)
6. banco/agência
7. laboratório; depósito/armazém/galpão
8. **indústria antes de escritório**
9. escritório
10. comércio; 11. saúde; 12. educação; 13. religioso; 14. urbanismo; 15. cultura/esporte
16. genéricos por último (edifício comercial, residência)
17. **SESC/SENAC no fim**, para o objeto nomeado (ginásio, quadra) vencer

Duas guardas de desambiguação, de casos reais:
- título com "residência" **e** fazenda → residência vence
- título com "residência" **e** banco/agência → **não preenche**, é ambíguo
  (`F010-P0197 Residência Banco Cidade - Agência Matriz` — provável erro na geração do título)

### 5.4 Os 9 bugs que o back-test pegou — a classe de erro que sempre volta

Todos achados **antes** de gravar, rodando as regras contra o que já estava curado:

1. `casa` casando com "Santa **Casa** de Misericórdia" → guarita de hospital virava residência
2. `quadra` casando com "**Quadra**-3" (lote urbano) → virava quadra esportiva
3. `cimento` casando com "abaste**cimento**" → posto de gasolina virava fábrica
4. "Jardim Botânico" é bairro de Ribeirão Preto, não jardim botânico
5. `casa de campo` apontada para o id de "Sede de fazenda" — erro de id, não de regex
6. `drogacenter` vencendo `depósito` → "Depósito Drogacenter" virava loja (era ordem)
7. `aeroporto` casando com "Ed. Comercial TAM, **Aeroporto**" — o aeroporto era o endereço
8. "Prédio de Apartamentos" → unidade em vez de edifício
9. `instituto` → instituto de pesquisa, mas os 3 casos reais eram de saúde

**Todos são substring casando dentro de outra palavra, ou ordem errada.** Dois remédios
obrigatórios: usar limite de palavra (`\bcimento\b`) e testar a ordem, não só o conjunto.

### 5.5 Filtro que falta: documentação não é obra

**Lição custou um erro publicado.** `F023-P0181 "Documentos do escritório"` recebeu
Escritórios/Obra nova pela regra de `escritorio`. É ficha de documentação, não obra.

O inventário mistura fichas de obra com fichas de documentação, e as regras de objeto não
distinguem as duas. **Antes de qualquer classificação, filtrar fora os títulos que descrevem
documento:** `documento`, `currículo`, `caderno`, `pasta`, `recortes`, `correspondência`,
`memorial`, `contrato`.

---

## 6. Fotografias — sem carimbo, regra diferente

As 58 fotografias do F016 não têm carimbo. O título saiu do que se vê, mais o projeto a que a
pasta pertence. É outra série no Tainacan (`Fotografias`, termo 57, não `Desenhos e Pranchas`,
termo 17).

### 6.1 O prompt (fotografia)

```
Você vai descrever UMA fotografia de acervo de arquitetura.

REGRAS

1. Descreva SOMENTE o que está visível no quadro.
2. NÃO nomeie pessoas. NÃO atribua autoria da fotografia. NÃO diga onde foi
   tirada, a não ser que haja indicação visual inequívoca (placa, letreiro,
   fachada identificada).
3. NÃO estime data, estilo, nem autoria do edifício fotografado.
4. Transcreva qualquer texto legível na imagem (placa, letreiro, legenda
   escrita no slide, anotação na moldura) em "texto_na_imagem".
5. Se a imagem for um negativo, verso, cartela de teste ou folha de contato,
   diga isso em "tipo_de_imagem" e não descreva como se fosse a obra.

Devolva SOMENTE o JSON do esquema.
```

```json
{
  "tipo_de_imagem": "fotografia | negativo | verso | cartela | folha_de_contato",
  "assunto": "",
  "enquadramento": "externa | interna | detalhe | aérea | maquete | pessoas",
  "elementos_visiveis": [],
  "texto_na_imagem": [],
  "legenda_proposta": "",
  "confianca": 0.0
}
```

### 6.2 Crédito de fotógrafo

**Nunca do modelo.** Só de fonte humana. Sem fonte, "fotógrafo não identificado".

O caso que fixa a regra: a fotógrafa das fotos de 2005 do Sawaya é **Lucia Hashizu**, não
"Lucia Sawaya" — um palpete por semelhança de nome teria publicado crédito falso.

### 6.3 Pares JPG + RAW

Fotografia de slide vem como JPG + DNG. O DNG é master (43 GB no F016) e fica no arquivo; sobe
só o JPG. Vale também para NEF.

---

## 7. Regras de conteúdo invioláveis

Estas não são preferências de catalogação. São o que impede o acervo de sair do ar.

1. **Só fatos lidos no documento.** Nada de biografia, contexto histórico ou atribuição de
   estilo. A biografia publicada do Chu Ming gerou reclamação da família e o fundo inteiro foi
   retirado.
2. **Crédito de fotógrafo vem de fonte humana**, nunca de palpite.
3. **Campo sem evidência fica vazio.** Vazio não custa nada; errado publicado custa um fundo.
4. **Dúvida vai junto do dado, não é resolvida em silêncio.** O padrão é o das datas "85": o
   valor mais provável é gravado **e** a ressalva fica anexada —
   *"conferir no original antes de publicar qualquer data de 1985"*.
5. **Autoria divergente bloqueia publicação.** Prioridade máxima entre as checagens.
6. **Nada é apagado.** Duplicata é marcada, não removida. Registro errado vai para a lixeira,
   e esvaziá-la é ato humano.

---

## 8. Saída

### 8.1 Por folha

Além dos campos do esquema: `arquivo_origem` (pasta + nome, a chave de identidade),
`md5`, `hash_perceptual`, `duplicata_de`, `tipo_duplicata`, `rotacao_aplicada`,
`orientacao_incerta`, `espelhada`, `autoria_divergente`, e `outlier_<campo>` para cada campo que
discordou do consenso.

### 8.2 `pacote_tainacan.json`

Pedido seu (item 7 das prioridades). Deve sair pronto para consumo, com um detalhe que custou
tempo na importação: **o ID de termo de taxonomia vai como número**, não string nem lista.
`{"values": 26}` grava; `{"values": "26"}` e `{"values": [26]}` devolvem
`400 Valores inválidos`.

### 8.3 Folha de contatos

Um `contatos.jpg` por projeto, com miniaturas **numeradas**, para conferência humana de ordem e
orientação de uma vez só em vez de folha a folha. Pedido seu, e é o instrumento que torna a
revisão de orientação viável em escala.

### 8.4 Preview para o site

Lado maior ~3000 px, JPEG 85, sRGB — 0,3 a 1,2 MB por folha. Já girado e já com o nome final do
código. (`drive.google.com/thumbnail?id=<ID>&sz=w3000` entrega exatamente esse padrão sem
reencode, se a origem for Drive.)

### 8.5 Relatório de fim de lote

Arquivos na origem × registros na saída, carimbo lido, folha identificada, projetos distintos,
duplicatas, autoria divergente, orientação incerta, erros. **Um lote nunca termina sem
catalogação.**

---

## 9. Protocolo de validação — a parte que não é opcional

Esta é a prática que mais mudou resultado, e é barata porque a base curada já existe.

### 9.1 Back-test antes de gravar qualquer regra

Rodar a regra contra os registros que **já têm** o campo preenchido por mão humana e comparar.
Medido na rodada de programa/uso: **89,7% de acerto exato** (469 de 523 onde alguma regra
dispara), **93,8%** em natureza do trabalho, sobre 804 e 691 fichas respectivamente.

Classificar as divergências em três baldes, porque significam coisas diferentes:

| balde | medido | o que fazer |
|---|---|---|
| acerto exato | 89,7% | nada |
| ramo certo, granularidade errada | 6,9% (36 casos) | aceitável; revisar o mapa de ids |
| categoria diferente | 3,6% (19 casos) | **examinar um por um** — foi aqui que saíram os 9 bugs |

Critério de liberação sugerido: **≥ 85% de acerto exato e 100% das divergências de categoria
examinadas à mão.** Abaixo disso a regra não grava.

### 9.2 Reconferência depois de gravar

Recalcular o valor a partir da fonte e comparar com o que está no banco, para **todos** os
registros gravados. Na rodada do tipo de desenho: 1.042 recalculados, **0 divergências**.

### 9.3 Recontagem independente

Varrer a coleção do zero depois de gravar, sem reaproveitar contagem nenhuma, e checar se o
número bate com o previsto. Foi assim que se confirmou 1.934 → 892 e 846 → 305.

### 9.4 Amostra humana

Item por item, alguns de cada fundo. Foi uma amostra dessas que achou o
`F023-P0181 "Documentos do escritório"` classificado como obra.

---

## 10. Checklist de aceite do lote

Um lote só é publicável se:

- [ ] contagem de origem = contagem de saída
- [ ] nenhuma folha com `autoria_divergente` na fila de publicação
- [ ] duplicatas marcadas e só a única publicada
- [ ] `orientacao_incerta` zerado ou revisado na folha de contatos
- [ ] nenhum campo preenchido por plausibilidade (toda gravação rastreável a `onde`)
- [ ] fichas de documentação não classificadas como obra
- [ ] back-test da regra ≥ 85% e divergências de categoria examinadas
- [ ] EXIF limpo, sem título herdado de lote anterior
- [ ] relatório de fim de lote gerado

---

## 11. O que foi medido e o que é hipótese

**Medido:** 2000 px basta para carimbo, data manuscrita e código de folha (118 pranchas);
pontuação geométrica não distingue rotação (~0.70 nas 4); pasta↔projeto não é 1:1 (SENAC);
89,7% e 93,8% de acerto das regras de texto; 1.042 gravações com 0 divergência na reconferência;
colisão de nome entre pastas; EXIF contaminado em 5 de 118.

**Hipótese, medir antes de adotar:** que 2000 px baste para A0 com carimbo muito pequeno; que a
leitura de folha inteira por modelo de visão saia mais barata que detecção + OCR em escala de
milhares (o custo por folha precisa ser medido num lote real de ~150 antes de comprometer o
desenho); que `transcricao_integral` guardada permita reclassificar sem reprocessar imagem — é o
objetivo, mas nunca foi exercitado.

**Não transferível:** a paciência de ler 118 folhas em ordem é barata para um modelo numa
conversa e caríssima em chamadas de API. É por isso que o desenho correto é **uma** leitura de
folha inteira na passada 2, e a passada 3 ser **código**, não modelo.
