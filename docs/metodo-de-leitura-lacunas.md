# Método de leitura: o que o CAMP Vision faz hoje × o que a especificação pede

Referência: `docs/metodo-de-leitura.md` (setembro de 2026). Levantado em 08/10/2026 sobre o `main` (commit 5341741) lendo o código;
nada foi executado contra o modelo (não há chave da API fora do servidor). "Não encontrei" = busca textual no código, não prova de ausência.

| § | Especificação | Hoje (fato do código) | Estado |
|---|---|---|---|
| 0 | Uma leitura, página inteira; carimbo não é localizado nem recortado | Lê a página reduzida e devolve a região do carimbo; `usar_cache_de_regiao=True` manda direto o recorte da prancha anterior; 2º passe relê o recorte (`margem_recorte=0.06`, `ganho_minimo_2o_passe=1.3`) | **Falta** (é o oposto) |
| 2.1 | 2000 px, JPEG q78 | `lado_maximo_envio=1568`, `qualidade_jpeg_envio=85` | Falta; **medir antes** (hipótese A0) |
| 2.2 | Orientação pela leitura: 4 rotações pontuadas, empate = `orientacao_incerta`, espelhamento | O modelo devolve `rotacao`; sem pontuação por rotação, sem `orientacao_incerta`, sem espelhamento | **Falta** |
| 2.3 | md5 + hash perceptual na entrada; `duplicata_de`, `tipo_duplicata` | Só sha256 na cópia/verificação (`entrada.py`); sem hash por folha | **Módulo pronto** na branch (`nucleo/duplicatas.py`); falta ligar |
| 2.4 | Chave `pasta + nome` | `chave_de_identidade` no módulo | Módulo pronto; falta ligar |
| 2.5 | Limpar EXIF herdado; ignorar lote de teste | Ignora `teste` (`entrada.py`). `metadados.py` grava com `-overwrite_original`; não encontrei limpeza das tags herdadas | **Verificar** quais tags de título são gravadas/limpas |
| 3 | Prompt de folha inteira; campos `{valor, confianca, alternativas, onde}`; `transcricao_integral`, `materiais_citados`, `anotacoes_manuscritas`, `revisao`, `codigo_serie` | 17 campos com `{valor, confianca}`; sem `alternativas`, `onde`, `revisao`, `codigo_serie`, `transcricao_integral`, `materiais_citados`; o prompt manda "ler carimbos" e "não confundir com legenda de material" | **Falta** |
| 4.1 | Consenso em CÓDIGO (moda), `outlier_<campo>`, valor lido preservado | `grupos.consolidar` chama o MODELO (texto) por grupo; guarda `lidos_originais` e `suspeita_grupo`; descarta valor que nenhuma prancha leu | **Módulo pronto** (`nucleo/consenso.py`, 12 testes) atrás de `consolidacao = "codigo"` (padrão continua `"modelo"`); falta validar em lote real e então trocar o padrão |
| 4.1 | Grafia canônica congela; quarentena | Sem vocabulário persistente entre lotes | **Falta** |
| 4.2 | Ano da pasta = moda do grupo; "Ano desconhecido" | `ano_do_projeto` decidido pelo modelo na consolidação | Falta |
| 4.3 | Pasta não é projeto | Agrupa pelo `projeto` lido (similaridade de texto), sem usar a pasta | **Conforme** |
| 4.4 | Autoria divergente bloqueia publicação | Sem bloqueio; só `suspeita_grupo`. Depende da tabela de autoridade (titular, coautores, período) | **Falta** |
| 4.5 | Contagem origem × saída; espaço em disco | `_contagem_esperada` valida lote completo só com manifesto; não encontrei checagem de espaço nem relatório da diferença | Parcial |
| 5.2 | Tipo de desenho: 15 detectores | O `tipo` vem do modelo, texto livre | **Módulo pronto** (`nucleo/derivacao.py`); falta ligar |
| 5.3 | Programa/uso e natureza: 17 passos ordenados + guardas | Nada. Existe a regra em JavaScript nos documentos do projeto (`regras-inferencia-programa-uso-natureza.js`) | **Falta** (portar) |
| 5.5 | Documentação não é obra | `e_ficha_de_documentacao` no módulo | Módulo pronto; falta usar na classificação |
| 6 | Fotografia: prompt próprio; JPG+RAW (só JPG sobe); crédito só humano | Série S03 pela pasta (`estrutura.py`); sem prompt próprio; não verifiquei se foto passa pelo leitor de carimbo | **Verificar / Falta** |
| 8.2 | `pacote_tainacan.json` (ID de termo como número) | Não encontrei | **Falta** |
| 8.3 | `contatos.jpg` por projeto, miniaturas numeradas | Existe (`formatos.folha_de_contatos`): um por lote, até 1000 miniaturas numeradas pelo código | Parcial |
| 8.4 | Preview do site 3000 px, JPEG 85, sRGB, já girado e com o nome final | Não verifiquei | **Verificar** |
| 8.5 | Relatório de fim de lote com as contagens | `relatorio_diario.py` existe | Parcial |
| 9 | Protocolo de validação (back-test, reconferência, recontagem, amostra) | Nada | **Falta** |
| 10 | Checklist de aceite do lote | Nada como portão | **Falta** |

## Interpretações e medidas desta rodada (não vêm da especificação)
- **Convenção da tabela de gatilhos (§5.2):** `termo*` é prefixo de palavra; `termo` sem asterisco é a palavra inteira. É isso que explica a tabela listar `foto, fotos` e
  `forma/formas` separados, e "Esquadrias de Ferro" (plural; gatilho `esquadria`) estar entre os 254 títulos sem tipo. É uma INTERPRETAÇÃO coerente com os documentos do F023; confirmar com o Rafa.
- **Hash perceptual (§2.3), medido só em folhas SINTÉTICAS** (hash de 256 bits, distância de Hamming): mesma folha com recompressão JPEG 3 a 6, reduzida a 90% 5, a 50% 9,
  a 80% + q60 13, deslocada 3 px 19, girada 1° 18; folhas DIFERENTES com a mesma moldura e carimbo: mínimo 34 (276 pares). Um limiar baixo único perde o rescan real; um alto arrisca esconder folha de verdade.
  Por isso há dois níveis (`LIMIAR_MARCA=8` marca; até `LIMIAR_REVISAO=24` só sugere revisão humana). **Falta medir em pares reais** (as pranchas 0085/0086 do McDonald's são o caso-guia).
- `e_ficha_de_documentacao` usa só a lista da §5.5. "Documentação" (com ç) NÃO está na lista; estender é decisão do Rafa e pede back-test.
- **Consenso em código (§4.1), decisão de projeto:** o consenso é calculado para todos os campos, mas NÃO sobrescreve a leitura de cada folha em dois: `ano` (cada prancha mantém o seu; a moda vai em `ano_do_projeto`, §4.2)
  e `arquiteto` (a especificação diz que o consenso esconderia uma prancha intrusa num grupo de 30, §4.4; a folha de outro arquiteto continua com o que foi lido e fica marcada outlier).
  Os demais campos recebem o valor do grupo, com o lido guardado em `lidos_originais` e o outlier em `outliers`/`ressalvas`. O modo `codigo` NÃO detecta folha de OUTRO projeto dentro do grupo
  (`suspeita_grupo` fica falso) e não tem quarentena nem canônica congelada: são tickets próprios.
