"""Montagem da unidade publicável: projeto + documentos + fundo (tickets CV-08, 11, 13,
15, 16, 19, 21, 22, 25, 26). Só código, nada de modelo. Vale para qualquer acervo:
o que muda por fundo vem dos parâmetros do fundo, nunca de regra escrita aqui.

- `congelar_canonicas`: grafia confirmada em lotes anteriores não é sequestrada
  por uma variante de OCR (CV-16, caso HOSWALDO);
- `melhor_versao`: entre duplicatas, publica a maior resolução (CV-11);
- `titulo_publicacao`: frase curta da folha, sem o nome do projeto (CV-21);
- `fontes_e_lacunas`: fontes que discordam deixam o canônico vazio (CV-13);
- `propostas`: N obras numa pasta (código de unidade) e fotos sem obra agrupadas
  por semelhança visual (CV-08, CV-22) — PROPOSTA, quem decide é gente;
- `decisoes`: decisão humana registrada como decisão, nunca apagando (CV-25);
- `pacote`: o `pacote_tainacan.json` no esquema D.3 (CV-19).
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

from . import estrutura, preparo as mod_preparo, programa as mod_programa
from .grupos import normalizar

CONGELA = 5             # leituras confirmadas para a grafia congelar
SIMILAR_CONGELADA = 0.88
CAMPOS_CONGELAVEIS = ("arquiteto", "escritorio", "cidade", "cliente")
DISTANCIA_MESMA_OBRA = 18   # dHash: fotos "da mesma obra" (mais frouxo que duplicata)
FOTOGRAFO_SEM_FONTE = "fotógrafo não identificado"


def _agora() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _ler(caminho: Path, padrao):
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return padrao


def _gravar(caminho: Path, dados) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    tmp = caminho.with_name(f".{caminho.name}.tmp")
    tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(caminho)


# ---------------------------------------------------------------- CV-16

def congelar_canonicas(leituras: dict, fundo_codigo: str, pasta_estado: Path) -> list[str]:
    """Aplica e atualiza as grafias congeladas do fundo. Devolve avisos."""
    caminho = pasta_estado / "canonicas" / f"{fundo_codigo}.json"
    banco: dict = _ler(caminho, {}) or {}
    avisos: list[str] = []
    for campo in CAMPOS_CONGELAVEIS:
        tabela: dict = banco.setdefault(campo, {})  # normalizado -> {"grafia", "n"}
        congeladas = {k: v for k, v in tabela.items() if v.get("n", 0) >= CONGELA}
        for codigo, l in leituras.items():
            valor = l.valores.get(campo, "")
            n = normalizar(valor)
            if not n:
                continue
            if n in congeladas:
                l.valores[campo] = congeladas[n]["grafia"]
                continue
            parecida = max(congeladas, key=lambda k: SequenceMatcher(None, k, n).ratio(), default="")
            if parecida and SequenceMatcher(None, parecida, n).ratio() >= SIMILAR_CONGELADA:
                grafia = congeladas[parecida]["grafia"]
                nota = f"{campo} lido '{valor}' tratado como '{grafia}' (grafia confirmada do fundo)"
                if nota not in l.ressalvas:
                    l.ressalvas.append(nota)
                l.valores[campo] = grafia
        # Atualiza contagens com o que este lote confirmou.
        for valor, vezes in Counter(l.valores.get(campo, "") for l in leituras.values()
                                    if l.valores.get(campo)).items():
            n = normalizar(valor)
            item = tabela.setdefault(n, {"grafia": valor, "n": 0})
            if item["n"] < CONGELA:  # congelada não muda de grafia
                item["grafia"] = max((item["grafia"], valor), key=lambda g: (g == valor and vezes > 1, len(g)))
            item["n"] += vezes
    try:
        _gravar(caminho, banco)
    except OSError as erro:
        avisos.append(f"não gravei as grafias congeladas: {erro}")
    return avisos


# ---------------------------------------------------------------- CV-11

def melhor_versao(leituras: dict, preparos: dict) -> None:
    """Duplicatas: a versão publicada é a de maior arquivo/resolução, não a primeira."""
    for codigo, l in leituras.items():
        if not l.duplicata_de or l.duplicata_de not in leituras:
            continue
        original = leituras[l.duplicata_de]
        meu = preparos.get(codigo, {}).get("tamanho", 0)
        dele = preparos.get(l.duplicata_de, {}).get("tamanho", 0)
        if meu > dele and not original.duplicata_de:
            original.duplicata_de, original.tipo_duplicata = codigo, l.tipo_duplicata
            l.duplicata_de, l.tipo_duplicata = "", ""
    # Indício: mesmo título + folha + data lidos (sem hash parecido).
    chaves: dict[tuple, str] = {}
    for codigo in sorted(leituras):
        l = leituras[codigo]
        chave = tuple(normalizar(l.valores.get(c, "")) for c in ("titulo_prancha", "folha", "data"))
        if l.duplicata_de or not all(chave):
            continue
        if chave in chaves:
            nota = f"possível duplicata de {chaves[chave]}: mesmo título, folha e data lidos"
            if nota not in l.ressalvas:
                l.ressalvas.append(nota)
        else:
            chaves[chave] = codigo


# ---------------------------------------------------------------- CV-21

def titulo_publicacao(leitura, nome_projeto: str, nome_pasta: str = "") -> str:
    """Frase curta da folha, sem o nome do projeto nem o da pasta; caixa normal."""
    titulo = (leitura.valores.get("titulo_prancha") or "").strip()
    if not titulo:
        return ""
    for nome in (nome_projeto, nome_pasta, leitura.valores.get("projeto", "")):
        if not nome or len(nome) < 4:
            continue
        padrao = r"\s*[-–—:,]?\s*".join(re.escape(p) for p in nome.split())
        titulo = re.sub(padrao, " ", titulo, flags=re.IGNORECASE)
    titulo = re.sub(r"\s{2,}", " ", titulo).strip(" -–—:,.")
    letras = [c for c in titulo if c.isalpha()]
    if letras and sum(c.isupper() for c in letras) / len(letras) > 0.8:
        titulo = titulo.lower()
        titulo = titulo[:1].upper() + titulo[1:]
    return titulo


# ---------------------------------------------------------------- CV-13

def fontes_e_lacunas(ctx, leituras: dict, info: dict | None = None) -> tuple[dict, dict, list[str]]:
    """(canônico, fontes, lacunas) do registro do projeto.

    Cada campo guarda o valor POR FONTE (pasta, info_projeto, carimbo). Fontes que
    discordam deixam o canônico vazio e escrevem a lacuna — o app não escolhe.
    """
    info = info or {}
    docs = [l for l in leituras.values() if l.modo == "prancha"]
    fontes: dict[str, dict[str, str]] = {}

    def moda(campo: str) -> str:
        vals = [l.valores.get(campo, "") for l in docs if l.valores.get(campo)]
        return Counter(vals).most_common(1)[0][0] if vals else ""

    anos_carimbo = Counter(l.ano_do_projeto for l in leituras.values() if l.ano_do_projeto)
    fontes["ano"] = {k: v for k, v in {
        "pasta": ctx.ano if ctx.ano and ctx.ano != "0000" else "",
        "info_projeto": str(info.get("ano") or ""),
        "carimbo": anos_carimbo.most_common(1)[0][0] if anos_carimbo else "",
    }.items() if v}
    for campo in ("cliente", "endereco", "cidade", "uf"):
        fontes[campo] = {k: v for k, v in {
            "info_projeto": str(info.get(campo) or ""), "carimbo": moda(campo)}.items() if v}
    fontes["projeto"] = {k: v for k, v in {
        "pasta": ctx.projeto, "carimbo": moda("projeto")}.items() if v}

    canonico: dict[str, str] = {}
    lacunas: list[str] = []
    for campo, por_fonte in fontes.items():
        valores = {normalizar(v) for v in por_fonte.values()}
        if campo == "ano":
            anos = sorted({int(v) for v in por_fonte.values() if re.fullmatch(r"\d{4}", v)})
            if anos and anos[-1] - anos[0] > 2:
                lacunas.append("ano: fontes discordam — " + ", ".join(f"{k} {v}" for k, v in por_fonte.items())
                               + "; campo deixado vazio de propósito")
                canonico[campo] = ""
            else:
                canonico[campo] = por_fonte.get("carimbo") or por_fonte.get("info_projeto") or por_fonte.get("pasta", "")
            continue
        if campo == "projeto":  # nome: pasta é pista, o carimbo vence se houver
            canonico[campo] = por_fonte.get("carimbo") or por_fonte.get("pasta", "")
            continue
        if len(valores) > 1 and not all(SequenceMatcher(None, a, b).ratio() >= 0.8
                                        for a in valores for b in valores):
            lacunas.append(f"{campo}: fontes discordam — "
                           + "; ".join(f"{k} '{v}'" for k, v in por_fonte.items()) + "; campo vazio")
            canonico[campo] = ""
        else:
            canonico[campo] = por_fonte.get("carimbo") or por_fonte.get("info_projeto", "")
    if any(l.conflito_endereco for l in docs):
        variantes = sorted({v for l in docs for v in l.endereco_variantes})
        lacunas.append("endereço com variantes que discordam: " + " | ".join(variantes))
    return canonico, fontes, lacunas


# ---------------------------------------------------------------- CV-08 / CV-22

def propostas(leituras: dict, preparos: dict) -> dict:
    """Obras distintas na mesma pasta e fotos sem obra agrupadas — só proposta."""
    from .grupos import unidade_e_revisao

    por_unidade: dict[str, list[str]] = {}
    for codigo in sorted(leituras):
        unidade, revisao = unidade_e_revisao(leituras[codigo])
        if unidade:
            chave = unidade.upper() + (f" {revisao}" if revisao else "")
            por_unidade.setdefault(chave, []).append(codigo)
    obras = []
    if len(por_unidade) >= 2:
        for chave, codigos in sorted(por_unidade.items()):
            obras.append({"codigo_unidade": chave, "documentos": codigos, "quantidade": len(codigos)})
    sem_unidade = [c for c in sorted(leituras) if c not in {x for v in por_unidade.values() for x in v}]

    fotos = [c for c in sorted(leituras) if leituras[c].modo == "fotografia"
             and not leituras[c].valores.get("projeto") and preparos.get(c, {}).get("hash_perceptual")]
    grupos_foto: list[list[str]] = []
    for c in fotos:
        h = preparos[c]["hash_perceptual"]
        for g in grupos_foto:
            if any(mod_preparo.distancia(h, preparos[o]["hash_perceptual"]) <= DISTANCIA_MESMA_OBRA for o in g):
                g.append(c)
                break
        else:
            grupos_foto.append([c])
    nao_identificadas = []
    for g in grupos_foto:
        fotos_g = [leituras[c].foto or {} for c in g]
        assunto = Counter(f.get("assunto", "") for f in fotos_g if f.get("assunto")).most_common(1)
        textos = sorted({t for f in fotos_g for t in f.get("texto_na_imagem", [])})
        nao_identificadas.append({
            "documentos": g,
            "titulo_proposto": f"{(assunto[0][0] if assunto else 'Obra').strip().capitalize()} (obra não identificada)",
            "pistas_visiveis": textos,  # número de fachada, placa, construtora
        })
    return {"obras_na_pasta": obras, "sem_codigo_de_unidade": sem_unidade if obras else [],
            "fotos_sem_obra": nao_identificadas if len(fotos) > 0 else []}


# ---------------------------------------------------------------- CV-25

def decisoes(catalogacao: Path) -> dict[str, dict]:
    """{código: decisão mais recente} de catalogacao/decisoes.json."""
    por_codigo: dict[str, dict] = {}
    for d in _ler(catalogacao / "decisoes.json", []) or []:
        if isinstance(d, dict) and d.get("codigo"):
            por_codigo[d["codigo"]] = d
    return por_codigo


def registrar_decisao(catalogacao: Path, codigo: str, acao: str, motivo: str, por: str, livro=None,
                      codigo_projeto: str = "") -> dict:
    if acao not in ("retirar", "publicar", "nota"):
        raise ValueError("ação deve ser retirar, publicar ou nota")
    if not motivo or not por:
        raise ValueError("decisão precisa de motivo e de quem decidiu")
    registro = {"codigo": codigo, "acao": acao, "motivo": motivo, "por": por, "em": _agora()}
    lista = _ler(catalogacao / "decisoes.json", []) or []
    lista.append(registro)
    _gravar(catalogacao / "decisoes.json", lista)
    if livro is not None:
        livro.anotar("decisao", codigo_projeto=codigo_projeto, codigo_documento=codigo,
                     detalhe=f"{acao}: {motivo} (por {por})")
    return registro


# ---------------------------------------------------------------- CV-19

def capa_sugerida(leituras: dict) -> str:
    candidatos = [c for c in sorted(leituras) if not leituras[c].duplicata_de
                  and not leituras[c].autoria_divergente]
    for preferido in ("Perspectiva", "Fachada", "Elevação", "Implantação"):
        for c in candidatos:
            if preferido in (leituras[c].valores.get("tipo") or "") and "-S01-" in c:
                return c
    for serie in ("-S01-", "-S03-"):
        for c in candidatos:
            if serie in c:
                return c
    return candidatos[0] if candidatos else ""


def pacote(codigo: str, nome: str, ctx, leituras: dict, preparos: dict, mapa: dict,
           catalogacao: Path, info: dict | None = None, nome_pasta: str = "",
           execucao: dict | None = None) -> dict:
    """pacote_tainacan.json (Anexo D.3). Os IDs de termo de série/tipo NÃO são
    inventados: o painel converte; taxonomia de valor único vai como NÚMERO."""
    canonico, fontes, lacunas = fontes_e_lacunas(ctx, leituras, info)
    sugestao = mod_programa.sugerir(canonico.get("projeto") or nome)
    decididas = decisoes(catalogacao)
    origem_por_codigo: dict[str, list[dict]] = {}
    arquivos_por_codigo: dict[str, list[str]] = {}
    for item in mapa.values():
        stem = Path(item["destino"]).stem
        arquivos_por_codigo.setdefault(stem, []).append(item["destino"])
        origem_por_codigo.setdefault(stem, []).append(
            {"arquivo_origem": item.get("origem", ""), "nome_original": item.get("nome_original", ""),
             "origem_formato": item.get("origem_formato", "")})
    fotografo = str((info or {}).get("fotografo") or (info or {}).get("credito_fotografia") or "")

    documentos, retirados, avisos = [], [], []
    for doc in sorted(leituras):
        l = leituras[doc]
        serie = doc.split("-")[3] if doc.count("-") >= 4 else ctx.serie
        bloqueios = []
        if l.autoria_divergente:
            bloqueios.append("autoria divergente")
        if l.suspeita_grupo:
            bloqueios.append("projeto divergente")
        if l.duplicata_de:
            bloqueios.append(f"duplicata de {l.duplicata_de}")
        if l.e_documento and serie == "S01":
            bloqueios.append("ficha de documentação (não é obra)")
        if getattr(ctx, "teste", False):
            bloqueios.append("lote de teste (CV-24)")
        decisao = decididas.get(doc)
        if decisao and decisao["acao"] == "retirar":
            bloqueios.append(f"retirado por decisão de {decisao['por']}: {decisao['motivo']}")
        rastreio = {campo: (l.onde.get(campo) or ("consenso do grupo" if campo in l.outliers else "leitura"))
                    for campo, valor in l.valores.items() if valor}
        item = {
            "codigo": doc, "projeto_codigo": codigo, "fundo_codigo": ctx.fundo.codigo, "serie": serie,
            "modo": l.modo,
            "titulo": l.titulo_publicacao or titulo_publicacao(l, nome, nome_pasta),
            "titulo_lido": l.valores.get("titulo_prancha", ""),
            "tipo": l.valores.get("tipo", ""), "tipo_de_desenho": l.valores.get("tipo", ""),
            "folha": l.valores.get("folha", ""), "escala": l.valores.get("escala", ""),
            "codigo_unidade": l.valores.get("codigo_unidade", ""), "revisao": l.valores.get("revisao", ""),
            "data_lida": l.data_lida, "data_iso": l.data_iso, "data_sugerida": l.data_sugerida,
            "data_outlier": l.data_outlier,
            "ano": l.valores.get("ano", ""), "ano_do_projeto": l.ano_do_projeto,
            "metadados": {k: v for k, v in l.valores.items() if v},
            "rastreio": rastreio,  # CV-15: de onde veio cada campo
            "alternativas": l.alternativas, "onde": l.onde,
            "transcricao_integral": l.transcricao_integral, "materiais_citados": l.materiais_citados,
            "foto": l.foto, "textual": l.textual,
            "pessoas_identificadas": l.pessoas_identificadas,
            "fotografo": (fotografo or FOTOGRAFO_SEM_FONTE) if l.modo == "fotografia" else "",
            "credito": ctx.fundo.credito,
            "preview": preparos.get(doc, {}).get("preview", ""),
            "arquivos": sorted(arquivos_por_codigo.get(doc, [])),
            "arquivo": (preparos.get(doc, {}).get("preview", "") or ""),
            "arquivo_origem": origem_por_codigo.get(doc, []),
            "origem_formato": l.origem_formato,
            "rotacao_aplicada": l.rotacao_aplicada, "confianca_rotacao": l.confianca_rotacao,
            "espelhada": l.espelhada, "orientacao_incerta": l.orientacao_incerta,
            # JPG e preview do acervo já gravados na orientação certa (nada a girar no painel)
            "orientacao_corrigida": bool(preparos.get(doc, {}).get("jpg_orientado")),
            "serie_incerta": l.serie_incerta,
            "duplicata_de": l.duplicata_de or None, "tipo_duplicata": l.tipo_duplicata,
            "autoria_divergente": l.autoria_divergente, "projeto_divergente": l.suspeita_grupo,
            "fora_do_periodo": l.fora_do_periodo,
            "prancha_original": (origem_por_codigo.get(doc) or [{}])[0].get("nome_original", ""),
            "confianca": round(l.confianca_media, 2) if hasattr(l, "confianca_media") else None,
            "decisao": decisao, "publicavel": not bloqueios, "bloqueios": bloqueios,
            "ressalvas": l.ressalvas,
            "versao_cv2": l.versao_cv2, "versao_prompt": l.versao_prompt, "modelo": l.modelo,
        }
        # Autoria divergente e retirada por decisão NÃO vão no pacote de publicação (CV-06/25).
        if l.autoria_divergente or (decisao and decisao["acao"] == "retirar"):
            retirados.append(item)
        else:
            documentos.append(item)
        if l.duplicata_de:
            avisos.append(f"{doc} é {l.tipo_duplicata} de {l.duplicata_de} — publicar {l.duplicata_de}")

    prop = propostas(leituras, preparos)
    if prop["obras_na_pasta"]:
        avisos.append(f"{len(prop['obras_na_pasta'])} obras distintas pelo código de unidade — "
                      "ver propostas.obras_na_pasta e separar em projetos")
    fundo = ctx.fundo
    return {
        "versao": 2, "gerado_em": _agora(), "teste": bool(getattr(ctx, "teste", False)),
        "projeto_codigo": codigo, "projeto_nome": nome, "fundo_codigo": fundo.codigo,
        "fundo": {"codigo": fundo.codigo, "sigla": fundo.sigla, "nome": fundo.nome,
                  "coautores": list(fundo.coautores),
                  "periodo_atuacao": list(fundo.periodo) if fundo.periodo else None},
        "projeto": {
            "codigo": codigo, "titulo": nome, "ano": canonico.get("ano", ""),
            "cidade": canonico.get("cidade", ""), "uf": canonico.get("uf", ""),
            "cliente": canonico.get("cliente", ""), "endereco": canonico.get("endereco", ""),
            "fontes": fontes, "lacunas": lacunas,
            "programa_sugerido": [sugestao["programa"]] if sugestao["programa"] else [],
            "natureza_sugerida": [sugestao["natureza"]] if sugestao["natureza"] else [],
            "sugestao": {**sugestao, "e_sugestao": True},
            "capa_sugerida": capa_sugerida(leituras),
            "descricao_sugerida": "",  # só fatos dos carimbos; nada de biografia (CV-15)
        },
        "documentos": documentos,
        "itens": documentos,  # nome antigo, mantido para o painel atual
        "retirados": retirados,
        "propostas": prop,
        "avisos": avisos,
        # Ticket 89: versão, modelo, prompts, chamadas, tokens, custo e tempo do lote/releitura.
        "execucao": execucao or {},
    }
