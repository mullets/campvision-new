"""Releitura de um projeto já no acervo, a pedido (painel ou linha de comando).

O fluxo continua de mão única: o painel REGISTRA o pedido e o CV2 PERGUNTA
(GET /api/estacoes/pedidos-releitura), igual ao heartbeat. O CV2 relê, grava o
resultado AO LADO do que existe e responde (POST .../{id}/concluido).

Regras:
- contrato do painel (docs/campvision.md §14 do camp-painel): o CV2 regrava
  `catalogacao/leituras.json`, o CSV e o `pacote_tainacan.json` só para as
  folhas relidas, e o painel reimporta protegendo o que gente já revisou;
- antes de regravar, a versão anterior fica em catalogacao/releituras/<quando>/
  (`antes_leituras.json`, `antes_pacote.json`) junto com `leituras.json` novo e
  `comparacao.json` (campo a campo: antes × agora) — nada se perde;
- escopo: "projeto" (todas as folhas), "folha"/"documentos" (códigos) ou
  "vazios" (folhas com erro, sem carimbo, ou com campo do modelo vazio);
- sem a imagem de leitura em cache, ela é refeita a partir do master no acervo
  (mesmo preparo: orientação pelo texto, 2000 px).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from . import formatos, planilha as mod_planilha, preparo as mod_preparo, renomear as mod_renomear
from .esquema import CAMPOS_DO_MODELO, Leitura

_log = logging.getLogger("cv2.releitura")
ESCOPOS = ("projeto", "documentos", "folha", "vazios")


def _agora() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _vazia(l: Leitura) -> bool:
    if l.erro or (l.modo == "prancha" and not l.carimbo_encontrado):
        return True
    if l.modo != "prancha":
        return not l.valores.get("titulo_prancha")
    return any(not l.valores.get(c.nome) for c in CAMPOS_DO_MODELO
               if c.nome in ("projeto", "arquiteto", "data", "titulo_prancha"))


def _masters(pasta_projeto: Path) -> dict[str, list[Path]]:
    from . import estrutura

    por_codigo: dict[str, list[Path]] = {}
    for arquivo in pasta_projeto.rglob("*"):
        if arquivo.is_file() and "catalogacao" not in arquivo.relative_to(pasta_projeto).parts \
                and estrutura.CODIGO_DOCUMENTO.match(arquivo.stem) and formatos.suportado(arquivo):
            por_codigo.setdefault(arquivo.stem, []).append(arquivo)
    return por_codigo


def alvos(pasta_projeto: Path, escopo: str, documentos: list[str] | None = None) -> list[str]:
    if escopo not in ESCOPOS:
        raise ValueError(f"escopo deve ser {', '.join(ESCOPOS)}")
    todos = sorted(_masters(pasta_projeto))
    if escopo == "projeto":
        return todos
    if escopo in ("documentos", "folha"):
        pedidos = {d.upper() for d in documentos or []}
        return [c for c in todos if c.upper() in pedidos]
    atuais = {Path(l.arquivo).stem: l for l in
              mod_planilha.ler_json(pasta_projeto / "catalogacao" / "leituras.json")}
    return [c for c in todos if c not in atuais or _vazia(atuais[c])]


def _imagem(codigo_doc: str, versoes: list[Path], cache: Path, pasta_estado: Path) -> Path | None:
    jpg = cache / f"{codigo_doc}.jpg"
    if jpg.exists():
        return jpg
    melhor = formatos.Documento(codigo_doc, versoes).para_ler
    bruta = formatos.imagem_de_leitura(melhor, pasta_estado / "leitura_bruta" / "releitura" / f"{codigo_doc}.jpg")
    if bruta is None:
        return None
    mod_preparo.preparar(bruta, melhor, jpg, None)
    bruta.unlink(missing_ok=True)
    return jpg


def reler(raiz_final: Path, pasta_estado: Path, config, cliente, codigo_projeto: str,
          escopo: str = "vazios", documentos: list[str] | None = None, motivo: str = "",
          pedido_por: str = "", livro=None, leitor=None, tabela=None, regravar: bool = True) -> dict:
    """Relê e grava ao lado. Devolve o resumo (vai na resposta ao painel)."""
    pasta = mod_renomear.achar_projeto(raiz_final, codigo_projeto)
    if pasta is None:
        raise FileNotFoundError(f"projeto {codigo_projeto} não encontrado no acervo")
    codigo = pasta.name.split(" - ")[0]
    escolhidos = alvos(pasta, escopo, documentos)
    masters = _masters(pasta)
    cache = pasta_estado / "leitura" / codigo
    cache.mkdir(parents=True, exist_ok=True)
    if leitor is None:
        from .visao import LeitorDeCarimbo

        leitor = LeitorDeCarimbo(config, cliente)

    atuais = {Path(l.arquivo).stem: l for l in mod_planilha.ler_json(pasta / "catalogacao" / "leituras.json")}
    novas: list[Leitura] = []
    comparacao: dict[str, dict] = {}
    t_in = t_out = 0
    falhas: list[str] = []
    for doc in escolhidos:
        jpg = _imagem(doc, masters.get(doc, []), cache, pasta_estado)
        if jpg is None:
            falhas.append(f"{doc}: não abri o master")
            continue
        nova = leitor.ler(jpg)
        nova.arquivo = jpg.name
        t_in += nova.tokens_entrada
        t_out += nova.tokens_saida
        if nova.erro:
            falhas.append(f"{doc}: {nova.erro[:120]}")
        novas.append(nova)
        antes = atuais.get(doc)
        mudou = {}
        for campo in sorted(set(nova.valores) | set(antes.valores if antes else {})):
            a = (antes.valores.get(campo, "") if antes else "") or ""
            b = nova.valores.get(campo, "") or ""
            if a != b:
                mudou[campo] = {"antes": a, "agora": b,
                                "confianca_agora": round(nova.confiancas.get(campo, 0.0), 2)}
        comparacao[doc] = {"mudou": mudou, "preencheu": sorted(k for k, v in mudou.items() if not v["antes"]),
                           "erro": nova.erro}

    quando = datetime.now().strftime("%Y%m%d-%H%M%S")
    destino = pasta / "catalogacao" / "releituras" / quando
    destino.mkdir(parents=True, exist_ok=True)
    mod_planilha.escrever_json(novas, destino / "leituras.json")
    regravados = 0
    if regravar and novas:
        regravados = _regravar(pasta, raiz_final, pasta_estado, destino, atuais, novas, tabela)
    custo = round(config.custo_estimado_usd(t_in, t_out), 4) if hasattr(config, "custo_estimado_usd") else 0.0
    resumo = {
        "projeto_codigo": codigo, "escopo": escopo, "motivo": motivo, "pedido_por": pedido_por,
        "relido_em": _agora(), "documentos": escolhidos, "relidos": len(novas),
        "com_mudanca": sum(1 for c in comparacao.values() if c["mudou"]),
        "campos_preenchidos": sum(len(c["preencheu"]) for c in comparacao.values()),
        "falhas": falhas, "custo_usd": custo, "regravados": regravados,
        "pasta": str(destino.relative_to(raiz_final)) if destino.is_relative_to(raiz_final) else str(destino),
    }
    (destino / "comparacao.json").write_text(
        json.dumps({"resumo": resumo, "documentos": comparacao}, ensure_ascii=False, indent=1), encoding="utf-8")
    if livro is not None:
        livro.anotar("relido", codigo_projeto=codigo,
                     detalhe=f"{escopo}: {len(novas)} folha(s), {resumo['com_mudanca']} com mudança "
                             f"({motivo or 'sem motivo'}; por {pedido_por or 'linha de comando'})")
    _log.info("Releitura %s (%s): %d folha(s), %d com mudança, US$ %.2f.",
              codigo, escopo, len(novas), resumo["com_mudanca"], custo)
    return resumo


def _regravar(pasta: Path, raiz_final: Path, pasta_estado: Path, destino: Path, atuais: dict,
              novas: list[Leitura], tabela=None, remontar: bool = False) -> int:
    """Troca as folhas relidas em leituras.json/CSV/pacote, guardando o antes ao lado."""
    import shutil
    from types import SimpleNamespace

    from . import entrada as mod_entrada, fundos as mod_fundos, grupos, projeto as mod_projeto

    cat = pasta / "catalogacao"
    for nome, copia in (("leituras.json", "antes_leituras.json"), ("pacote_tainacan.json", "antes_pacote.json"),
                        ("catalogacao.csv", "antes_catalogacao.csv")):
        if (cat / nome).exists():
            shutil.copy2(cat / nome, destino / copia)
    leituras = dict(atuais)
    trocadas = 0
    for nova in novas:
        codigo = Path(nova.arquivo).stem
        if nova.erro:
            continue  # leitura que falhou não substitui a que existe
        leituras[codigo] = nova
        trocadas += 1
    if not trocadas and not remontar:
        return 0
    # O checkpoint do projeto é a memória das leituras: sem atualizar, o próximo
    # lote traria de volta a leitura velha (o carregador fica com a ÚLTIMA linha).
    checkpoint = pasta / "campvision2_checkpoint.jsonl"
    try:
        with checkpoint.open("a", encoding="utf-8") as f:
            for nova in novas:
                if not nova.erro:
                    f.write(json.dumps(nova.para_dict(), ensure_ascii=False) + "\n")
    except OSError as erro:
        _log.warning("Checkpoint de %s não atualizado: %s", pasta.name, erro)
    lista = [leituras[c] for c in sorted(leituras)]
    for l in lista:  # consolidação do grupo refeita do zero, sem herdar outliers velhos
        if l.lidos_originais:
            l.valores.update(l.lidos_originais)
        l.lidos_originais, l.outliers, l.grupo = {}, [], ""
        l.ressalvas = [r for r in l.ressalvas if "grupo" not in r and "conjunto" not in r and "série indica" not in r]
    grupos.consolidar(lista)
    pacote_antigo = mod_entrada.ler_json(cat / "pacote_tainacan.json", {}) or {}
    preparos = mod_entrada.ler_json(cat / "preparo.json", {}) or {}
    mapa = mod_entrada.ler_json(cat / "mapa_origem.json", {}) or {}
    codigo_projeto = pacote_antigo.get("projeto_codigo") or pasta.name.split(" - ")[0]
    nome = pacote_antigo.get("projeto_nome") or (pasta.name.split(" - ", 1) + [""])[1]
    tabela = tabela or mod_fundos.com_parametros(mod_fundos.carregar(pasta_estado), raiz_final)
    fundo = tabela.get(codigo_projeto[:4]) or mod_fundos.Fundo(codigo_projeto[:4], "", codigo_projeto[:4])
    mod_entrada.enriquecer(leituras, preparos, fundo)
    mod_projeto.melhor_versao(leituras, preparos)
    for l in leituras.values():
        l.titulo_publicacao = mod_projeto.titulo_publicacao(l, nome)
    mod_planilha.escrever_json(lista, cat / "leituras.json")
    mod_planilha.escrever_csv(lista, cat / "catalogacao.csv")
    ano = codigo_projeto and next((c.split("-")[2] for c in leituras if c.count("-") >= 4), "0000")
    ctx = SimpleNamespace(fundo=fundo, serie="S01", ano=ano, projeto=nome,
                          teste=bool(pacote_antigo.get("teste")))
    info = mod_entrada.ler_json(pasta / "info_projeto.json", {}) or {}
    novo = mod_entrada.pacote_tainacan(codigo_projeto, nome, ctx, leituras, preparos, mapa, raiz_final,
                                       cat, info, "")
    mod_entrada.gravar_json(cat / "pacote_tainacan.json", novo)
    mod_entrada.gravar_json(cat / "erros.json", mod_entrada.erros_das_leituras(leituras.values()))
    return trocadas or len(leituras)


def remontar_pacote(raiz_final: Path, pasta_estado: Path, codigo_projeto: str, tabela=None) -> int:
    """Refaz leituras/CSV/pacote do que JÁ foi lido, sem chamar a API (ex.: projeto antigo sem pacote)."""
    pasta = mod_renomear.achar_projeto(raiz_final, codigo_projeto)
    if pasta is None:
        raise FileNotFoundError(f"projeto {codigo_projeto} não encontrado")
    atuais = {Path(l.arquivo).stem: l for l in mod_planilha.ler_json(pasta / "catalogacao" / "leituras.json")}
    if not atuais:
        return 0
    destino = pasta / "catalogacao" / "releituras" / datetime.now().strftime("%Y%m%d-%H%M%S-remontado")
    destino.mkdir(parents=True, exist_ok=True)
    return _regravar(pasta, raiz_final, pasta_estado, destino, atuais, [], tabela, remontar=True)


def projetos(raiz_final: Path, fundo: str = "") -> list[Path]:
    from . import estrutura

    saida = []
    for pasta_fundo in sorted(raiz_final.glob(f"{fundo.upper()}*" if fundo else "F[0-9][0-9][0-9]*")):
        pp = pasta_fundo / estrutura.PASTA_PROJETOS
        if pp.is_dir():
            saida += [p for p in sorted(pp.iterdir()) if p.is_dir() and estrutura.codigo_da_pasta(p)]
    return saida


def diagnostico(pasta: Path) -> dict:
    """O que existe × o que foi lido × o que o painel consegue importar, por projeto."""
    from . import estrutura

    masters = _masters(pasta)
    cat = pasta / "catalogacao"
    leituras = {Path(l.arquivo).stem: l for l in mod_planilha.ler_json(cat / "leituras.json")}
    pacote = {}
    try:
        pacote = json.loads((cat / "pacote_tainacan.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    sem_jpg = [c for c, vs in masters.items() if not any(v.suffix.lower() in (".jpg", ".jpeg") for v in vs)]
    vazias = [c for c in masters if c not in leituras or _vazia(leituras[c])]
    return {
        "codigo": estrutura.codigo_da_pasta(pasta), "documentos": len(masters),
        "lidos": sum(1 for c in masters if c in leituras), "com_erro": sum(1 for l in leituras.values() if l.erro),
        "com_carimbo": sum(1 for l in leituras.values() if l.carimbo_encontrado),
        "vazias_ou_sem_leitura": len(vazias), "sem_jpg": len(sem_jpg),
        "pacote": "v2" if pacote.get("versao") == 2 else ("antigo" if pacote else "NÃO"),
        "no_pacote": len(pacote.get("documentos") or pacote.get("itens") or []),
    }
