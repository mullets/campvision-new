"""Recebimento: de `100 - Scanners` para `ACERVOS_CAMP`.

    100 - Scanners/<fundo>/<material>/<projeto>/{TIF,JPG,DNG,PDF}   (qualquer ordem)
        │ 1. unidade completa (manifesto/status da estação, ou quieta há N min)
        │ 2. contexto: fundo pela TABELA (nunca pelo nome solto), série,
        │    projeto, ano, operador/estação (manifesto ou info_projeto.json)
        │ 3. projeto: existente em ACERVOS_CAMP ou reservado no painel (/reservar)
        │ 4. copia com o nome CAMP F0xx-P000x-AAAA-S0x-DNNNNN, confere por hash
        │ 5. lê o carimbo (uma vez por documento, da versão mais leve)
        │ 6. grava EXIF/XMP completo em TODAS as versões e confere
        │ 7. catalogacao/: catalogacao.csv, contatos.jpg, erros.json, lotes/
        │ 8. status.json = "pronto" (atômico, por último) + aviso ao painel
        └ 9. só então apaga o original da entrada

Cada passo vai para o livro de registro. Falha antes do 8: nada é apagado e o
projeto fica `erro` com o motivo. Contrato com o painel: docs/contrato-painel.md.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import estrutura, formatos, fundos as mod_fundos, info_projeto as mod_info
from . import preparo as mod_preparo
from . import metadados as mod_metadados
from .config import VERSAO_BUILD, Config
from .esquema import CAMPOS

_log = logging.getLogger("cv2.entrada")

PROCESSANDO, PRONTO, ERRO, AGUARDANDO = "processando", "pronto", "erro", "aguardando"
MARCADORES = ("manifesto.json", "info_projeto.json", "status.json")
PASTAS_DE_FORMATO = {
    "tif", "tiff", "jpg", "jpeg", "dng", "pdf", "png", "raw", "preview", "previews",
    "alta", "baixa", "alta resolucao", "baixa resolucao", "matriz", "master",
}


def _agora() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sha256(caminho: Path) -> str:
    h = hashlib.sha256()
    with caminho.open("rb") as f:
        for bloco in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloco)
    return h.hexdigest()


def gravar_json(caminho: Path, dados) -> None:
    """Temporário + rename: quem lê nunca pega o arquivo pela metade."""
    caminho.parent.mkdir(parents=True, exist_ok=True)
    temporario = caminho.with_name(f".{caminho.name}.tmp")
    temporario.write_text(json.dumps(dados, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporario, caminho)


def ler_json(caminho: Path, padrao=None):
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return padrao


# ================================================================ descoberta

@dataclass
class Unidade:
    """Uma pasta da entrada que vira (parte de) um projeto."""

    pasta: Path
    relativo: Path

    def arquivos(self) -> list[Path]:
        return sorted(
            p for p in self.pasta.rglob("*")
            if p.is_file() and not any(x.startswith(".") for x in p.relative_to(self.pasta).parts)
            and p.name not in MARCADORES
        )

    def assinatura(self) -> str:
        h = hashlib.sha1()
        for arquivo in self.arquivos():
            try:
                h.update(f"{arquivo.relative_to(self.pasta)}|{arquivo.stat().st_size}\n".encode())
            except OSError:
                continue
        return h.hexdigest()[:16]

    @property
    def chave(self) -> str:
        return str(self.relativo)


def _e_pasta_de_formato(nome: str) -> bool:
    return estrutura.normalizar(nome) in PASTAS_DE_FORMATO


def _e_pasta_de_material(nome: str) -> bool:
    """`03 - Fotografias`, `Negativos`, `S01 Pranchas` — e não `Casa Carta Branca`."""
    if not estrutura.serie_do_texto(nome):
        return False
    if re.match(r"^\s*(\d{2}\s*[-–]|S\d{2}\b)", nome):
        return True
    return len(estrutura.normalizar(nome).split()) <= 3 and not re.search(r"\d{4}", nome)


def descobrir(raiz: Path, profundidade_maxima: int = 6) -> list[Unidade]:
    """Unidade = primeira pasta que tem arquivo direto ou só pastas de formato."""
    achadas: list[Unidade] = []

    def caminhar(pasta: Path, nivel: int) -> None:
        if nivel > profundidade_maxima:
            return
        try:
            filhos = sorted(pasta.iterdir(), key=lambda p: p.name.lower())
        except OSError as erro:
            _log.warning("Não consegui listar %s: %s", pasta, erro)
            return
        tem_arquivo = any(
            f.is_file() and not f.name.startswith(".") and f.name not in MARCADORES
            for f in filhos
        )
        subpastas = [f for f in filhos if f.is_dir() and not f.name.startswith((".", "_"))]
        if tem_arquivo or any((pasta / m).exists() for m in MARCADORES) or (
            subpastas and all(_e_pasta_de_formato(f.name) for f in subpastas)
        ):
            if pasta != raiz:
                achadas.append(Unidade(pasta, pasta.relative_to(raiz)))
            return
        for sub in subpastas:
            caminhar(sub, nivel + 1)

    if raiz.is_dir():
        caminhar(raiz, 0)
    return achadas


def pronta(unidade: Unidade, config: Config) -> tuple[bool, str]:
    """A estação terminou de mandar?"""
    arquivos = unidade.arquivos()
    if not arquivos:
        return False, "vazia"
    status = ler_json(unidade.pasta / "status.json", {})
    marcada = (unidade.pasta / "manifesto.json").exists() or (
        isinstance(status, dict) and status.get("status") == "enviado_windows"
    )
    espera = config.espera_estabilidade_segundos if marcada else config.entrada_quieto_minutos * 60
    limite = time.time() - espera
    for arquivo in arquivos:
        try:
            if arquivo.stat().st_mtime > limite:
                return False, "arquivos ainda chegando"
        except OSError:
            return False, "arquivo sumiu durante a leitura"
    esperada = _contagem_esperada(unidade)
    if esperada is not None and len(arquivos) != esperada:
        return False, f"contagem {len(arquivos)} de {esperada}"
    return True, ""


def _contagem_esperada(unidade: Unidade) -> int | None:
    manifesto = ler_json(unidade.pasta / "manifesto.json", {}) or {}
    contagens = manifesto.get("contagens") if isinstance(manifesto.get("contagens"), dict) else {}
    valor = contagens.get("arquivos", manifesto.get("arquivos"))
    try:
        return int(valor) if valor not in (None, "") else None
    except (TypeError, ValueError):
        return None


# ================================================================ contexto

@dataclass
class Contexto:
    fundo: object | None = None
    serie: str = ""
    projeto: str = ""
    codigo: str = ""          # F002-P0002, se já veio da estação
    ano: str = ""
    cidade: str = ""
    operador: str = ""
    operador_email: str = ""
    estacao: str = ""
    tipo_estacao: str = ""
    enviado_em: str = ""
    lote_id: str = ""
    teste: bool = False
    problemas: list[str] = field(default_factory=list)

    def como_dict(self) -> dict:
        dados = dataclasses.asdict(self)
        dados["fundo"] = self.fundo.codigo if self.fundo else ""
        return dados


def _texto(dados: dict, *chaves: str) -> str:
    for chave in chaves:
        valor = dados.get(chave)
        if valor not in (None, ""):
            return str(valor).strip()
    return ""


def contexto(unidade: Unidade, tabela: mod_fundos.Tabela) -> Contexto:
    """Monta o contexto: manifesto/info da estação > caminho da pasta."""
    ctx = Contexto()
    manifesto = ler_json(unidade.pasta / "manifesto.json", {}) or {}
    bruto_info = mod_info.ler_bruto(unidade.pasta)
    traduzido = mod_info.ler(unidade.pasta)
    dados = {**{k.split(".")[-1]: v for k, v in bruto_info.items()}, **manifesto}

    partes = [p for p in unidade.relativo.parts if not _e_pasta_de_formato(p)]

    # Fundo: código/fundo explícito do manifesto, senão o caminho, sempre pela tabela.
    explicito = _texto(dados, "fundo_codigo", "fundo")
    if explicito:
        ctx.fundo = tabela.identificar(explicito)
        if ctx.fundo is None:
            ctx.problemas.append(f"fundo '{explicito}' não está na tabela de autoridade")
    if ctx.fundo is None and not ctx.problemas:
        for parte in partes:
            ctx.fundo = tabela.identificar(parte)
            if ctx.fundo:
                break
        if ctx.fundo is None:
            ctx.problemas.append("não achei o fundo (código F000, sigla ou nome) no caminho")

    # Série: manifesto, senão a pasta de material no caminho.
    ctx.serie = _texto(dados, "serie").upper()
    if not re.fullmatch(r"S(0[1-7]|99)", ctx.serie):
        ctx.serie = estrutura.serie_do_texto(_texto(dados, "tipo_material"))
    restantes: list[str] = []
    pulou_fundo = False
    for parte in partes:
        if ctx.fundo and not pulou_fundo and tabela.identificar(parte) is ctx.fundo:
            pulou_fundo = True  # a pasta do arquiteto/fundo
            continue
        if _e_pasta_de_material(parte):
            ctx.serie = ctx.serie or estrutura.serie_do_texto(parte)
            continue
        restantes.append(parte)
    if not ctx.serie:
        ctx.serie = "S99"

    # Projeto: código explícito, nome do manifesto/info, senão a pasta mais funda.
    ctx.codigo = _texto(dados, "codigo", "projeto_codigo").upper()
    if ctx.codigo and not estrutura.CODIGO_PROJETO.fullmatch(ctx.codigo):
        if re.fullmatch(r"P\d{4}", ctx.codigo) and ctx.fundo:
            ctx.codigo = f"{ctx.fundo.codigo}-{ctx.codigo}"
        else:
            ctx.codigo = ""
    nome = _texto(dados, "nome", "titulo") or traduzido.get("projeto", "")
    if not nome and isinstance(manifesto.get("projeto"), str):
        nome = manifesto["projeto"]
    if not nome and restantes:
        nome = restantes[-1]
    if not ctx.codigo:
        achado = estrutura.CODIGO_PROJETO.search(" ".join(restantes))
        if achado:
            ctx.codigo = f"{achado.group(1)}-{achado.group(2)}".upper()
    # "SBU-Taruma-1972" (padrão da estação Windows): tira prefixo e ano.
    nome = re.sub(r"^\s*(F\d{3}-)?P\d{4}\s*[-–]\s*", "", nome)
    padrao_windows = re.match(r"^[A-Z]{3}-(.+?)(?:-(\d{4}))?$", nome)
    if padrao_windows:
        nome = padrao_windows.group(1).replace("-", " ")
        ctx.ano = padrao_windows.group(2) or ""
    ano_no_nome = re.search(r"\b(1[89]\d{2}|20\d{2})\b", nome)
    ctx.ano = _texto(dados, "ano") or traduzido.get("ano", "") or ctx.ano or (
        ano_no_nome.group(1) if ano_no_nome else ""
    )
    if ano_no_nome:
        nome = re.sub(r"[\s\-–(]*\b" + ano_no_nome.group(1) + r"\b[)\s]*$", "", nome).strip()
    ctx.projeto = estrutura.limpar(nome, 90) if nome else ""
    if not ctx.projeto and not ctx.codigo:
        ctx.problemas.append("não achei o nome do projeto no caminho nem no manifesto")

    ctx.cidade = _texto(dados, "cidade") or traduzido.get("cidade", "")
    ctx.operador = _texto(dados, "operador")
    ctx.operador_email = _texto(dados, "operador_email", "email")
    ctx.estacao = _texto(dados, "estacao")
    ctx.tipo_estacao = _texto(dados, "tipo_estacao")
    ctx.enviado_em = _texto(dados, "enviado_em", "horario", "data_envio")
    ctx.lote_id = estrutura.limpar(
        _texto(dados, "lote_id", "lote")
        or f"{datetime.now():%Y%m%d-%H%M%S}-" + "_".join(unidade.relativo.parts[-3:]), 100
    )
    marca = dados.get("teste")
    ctx.teste = marca is True or str(marca).lower() == "true" or bool(
        re.search(r"\b(teste|test)\b", str(unidade.relativo), re.IGNORECASE)
    )
    return ctx


# ================================================================ estado local

class Estado:
    """Registro local do que já foi tratado e das reservas de projeto."""

    def __init__(self, pasta: Path) -> None:
        self.pasta = pasta
        self.arquivo = pasta / "entrada.jsonl"
        self.reservas = pasta / "reservas.json"

    def ultimos(self) -> dict[str, dict]:
        vistos: dict[str, dict] = {}
        if not self.arquivo.exists():
            return vistos
        for linha in self.arquivo.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(linha)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict) and item.get("unidade"):
                vistos[item["unidade"]] = item
        return vistos

    def anotar(self, unidade: str, situacao: str, **extra) -> dict:
        self.pasta.mkdir(parents=True, exist_ok=True)
        item = {"unidade": unidade, "status": situacao, "em": _agora(), **extra}
        with self.arquivo.open("a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
        return item

    def chave_reserva(self, fundo: str, nome: str) -> str:
        """Mesma chave para o mesmo projeto: repetir /reservar não duplica."""
        reservas = ler_json(self.reservas, {}) or {}
        chave = f"{fundo}|{estrutura.normalizar(nome)}"
        item = reservas.get(chave) or {}
        if not item.get("chave_reserva"):
            item = {"chave_reserva": uuid.uuid4().hex, "codigo": ""}
            reservas[chave] = item
            gravar_json(self.reservas, reservas)
        return item["chave_reserva"]

    def guardar_codigo(self, fundo: str, nome: str, codigo: str) -> None:
        reservas = ler_json(self.reservas, {}) or {}
        chave = f"{fundo}|{estrutura.normalizar(nome)}"
        reservas.setdefault(chave, {})["codigo"] = codigo
        gravar_json(self.reservas, reservas)


# ================================================================ EXIF

def conferir_exif(itens: list[tuple[Path, str]]) -> set[Path]:
    """Arquivos cujo XMP-dc:Identifier bate com o código esperado."""
    if not itens or not shutil.which("exiftool"):
        return set()
    try:
        proc = subprocess.run(
            ["exiftool", "-json", "-charset", "utf8", "-XMP-dc:Identifier", *[str(p) for p, _ in itens]],
            capture_output=True, text=True, timeout=600,
        )
        dados = json.loads(proc.stdout or "[]")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return set()
    esperado = {str(p): codigo for p, codigo in itens}
    return {
        Path(d["SourceFile"]) for d in dados
        if str(d.get("Identifier", "")) == esperado.get(d.get("SourceFile", ""))
    }


# ================================================================ erros

def enriquecer(leituras: dict, preparos: dict, fundo) -> None:
    """Preparo, duplicatas e autoria em cada leitura do projeto (método §2.3, §4.4, §8.1)."""
    from .derivacao import autoria_divergente

    ordem = sorted(leituras)
    por_md5: dict[str, str] = {}
    hashes: list[tuple[str, str]] = []
    for codigo in ordem:
        l = leituras[codigo]
        p = preparos.get(codigo, {})
        l.md5 = p.get("md5", "")
        l.hash_perceptual = p.get("hash_perceptual", "")
        l.rotacao_aplicada = int(p.get("rotacao", 0) or 0)
        l.espelhada = bool(p.get("espelhada", False))
        l.orientacao_incerta = bool(p.get("incerta", False))
        l.duplicata_de, l.tipo_duplicata = "", ""
        if l.md5 and l.md5 in por_md5:
            l.duplicata_de, l.tipo_duplicata = por_md5[l.md5], "exata"
        elif l.hash_perceptual:
            for outro, h in hashes:
                if mod_preparo.distancia(h, l.hash_perceptual) <= mod_preparo.DISTANCIA_PERCEPTUAL:
                    l.duplicata_de, l.tipo_duplicata = outro, "perceptual"
                    break
        if l.md5:
            por_md5.setdefault(l.md5, codigo)
        if l.hash_perceptual and not l.duplicata_de:
            hashes.append((codigo, l.hash_perceptual))
        arquiteto = (l.lidos_originais or l.valores).get("arquiteto", "") or l.valores.get("arquiteto", "")
        l.autoria_divergente = l.modo == "prancha" and autoria_divergente(arquiteto, fundo.autorizados)


def erros_das_leituras(leituras) -> list[dict]:
    """Erros por documento no vocabulário do painel (contrato item 4)."""
    erros: list[dict] = []

    def add(arquivo, categoria, gravidade, detalhe):
        erros.append({"arquivo": arquivo, "categoria": categoria, "gravidade": gravidade,
                      "origem": "CAMP Vision", "detalhe": detalhe})

    for l in leituras:
        nome = Path(l.arquivo).stem
        if l.erro:
            add(nome, "metadado", "corrigir", f"leitura falhou: {l.erro}")
            continue
        if l.autoria_divergente:
            add(nome, "autoria divergente", "bloqueia",
                f"carimbo diz '{(l.lidos_originais or l.valores).get('arquiteto', '')}' — "
                "não é titular nem coautor do fundo; não publicar")
        if l.duplicata_de:
            add(nome, "duplicata", "corrigir", f"{l.tipo_duplicata} de {l.duplicata_de} — só a única publica")
        if l.suspeita_grupo:
            add(nome, "projeto errado", "corrigir", f"carimbo diz '{l.valores.get('projeto', '')}'")
        if l.espelhada:
            add(nome, "espelhado", "aviso", "folha digitalizada pelo verso — preview já corrigido")
        if l.orientacao_incerta:
            add(nome, "orientação", "aviso", "orientação incerta — conferir na folha de contatos")
        if l.modo == "prancha" and not l.carimbo_encontrado:
            add(nome, "metadado", "aviso", "carimbo não encontrado")
        for campo in l.outliers:
            if campo != "arquiteto":
                add(nome, "metadado", "aviso", f"{campo} diverge do grupo (lido "
                    f"'{(l.lidos_originais or {}).get(campo, l.valores.get(campo, ''))}')")
        for ressalva in l.ressalvas:
            add(nome, "metadado", "aviso", ressalva)
    return erros


def pacote_tainacan(codigo: str, nome: str, ctx, leituras: dict, preparos: dict, mapa: dict,
                    raiz: Path) -> dict:
    """Pacote para o painel/importador (§8.2). IDs de termo NÃO são inventados aqui:
    o painel converte `serie`/`tipo` para o ID de taxonomia — sempre NÚMERO ({"values": 26})."""
    arquivos_por_codigo: dict[str, list[str]] = {}
    for item in mapa.values():
        arquivos_por_codigo.setdefault(Path(item["destino"]).stem, []).append(item["destino"])
    itens = []
    for doc in sorted(leituras):
        l = leituras[doc]
        serie = doc.split("-")[3] if doc.count("-") >= 4 else ctx.serie
        bloqueios = []
        if l.autoria_divergente:
            bloqueios.append("autoria divergente")
        if l.duplicata_de:
            bloqueios.append(f"duplicata de {l.duplicata_de}")
        if l.e_documento:
            bloqueios.append("ficha de documentação (não é obra)")
        itens.append({
            "codigo": doc, "projeto_codigo": codigo, "fundo_codigo": ctx.fundo.codigo,
            "serie": serie, "modo": l.modo, "titulo": l.valores.get("titulo_prancha", ""),
            "tipo_de_desenho": l.valores.get("tipo", ""),
            "ano": l.valores.get("ano", ""), "ano_do_projeto": l.ano_do_projeto,
            "metadados": {k: v for k, v in l.valores.items() if v},
            "alternativas": l.alternativas, "onde": l.onde,
            "transcricao_integral": l.transcricao_integral,
            "materiais_citados": l.materiais_citados, "foto": l.foto,
            "credito": ctx.fundo.credito,
            "preview": preparos.get(doc, {}).get("preview", ""),
            "arquivos": sorted(arquivos_por_codigo.get(doc, [])),
            "publicavel": not bloqueios, "bloqueios": bloqueios, "ressalvas": l.ressalvas,
        })
    return {"versao": 1, "projeto_codigo": codigo, "projeto_nome": nome,
            "fundo_codigo": ctx.fundo.codigo, "gerado_em": _agora(), "itens": itens}


def checklist_aceite(por_codigo: dict, leituras: dict, erros: list[dict], lote: dict) -> dict:
    """Checklist de aceite do lote (§10). Vai no JSON do lote; o painel decide publicar."""
    do_lote = [leituras[c] for c in por_codigo if c in leituras]
    exif = lote.get("exif", {})
    itens = {
        "contagem_origem_igual_saida": len(do_lote) == len(por_codigo),
        "sem_autoria_divergente": not any(l.autoria_divergente for l in do_lote),
        "duplicatas_marcadas": True,
        "orientacao_incerta_zerada": not any(l.orientacao_incerta for l in do_lote),
        "exif_conferido": exif.get("conferidos", 0) == exif.get("total", 0),
        "relatorio_gerado": True,
    }
    itens["publicavel"] = all(itens.values())
    itens["pendencias"] = [k for k, v in itens.items() if v is False]
    return itens


# ================================================================ o lote

@dataclass
class Resultado:
    status: str
    motivo: str = ""
    codigo: str = ""
    pasta_projeto: Path | None = None
    copiados: int = 0
    apagados: int = 0
    lote: dict = field(default_factory=dict)


class Recebedor:
    def __init__(self, config: Config, cliente, painel, livro, pasta_estado: Path,
                 tabela: mod_fundos.Tabela | None = None) -> None:
        self.config = config
        self.cliente = cliente
        self.painel = painel
        self.livro = livro
        self.pasta_estado = pasta_estado
        self.estado = Estado(pasta_estado)
        self.tabela = tabela or mod_fundos.carregar(pasta_estado, painel if painel.ligado else None)
        self.raiz_entrada = Path(config.pasta_entrada)
        self.raiz_final = Path(config.raiz_final)

    # ------------------------------------------------------------ fila
    def pendentes(self) -> list[Unidade]:
        feitos = self.estado.ultimos()
        fila = []
        for unidade in descobrir(self.raiz_entrada):
            ultimo = feitos.get(unidade.chave, {})
            if ultimo.get("status") in (PRONTO, ERRO) and ultimo.get("assinatura") == unidade.assinatura():
                continue  # nada mudou desde a última vez
            fila.append(unidade)
        return fila

    # ------------------------------------------------------------ status/info
    def _relativo(self, caminho: Path) -> str:
        try:
            return str(caminho.relative_to(self.raiz_final))
        except ValueError:
            return str(caminho)

    def _status(self, pasta: Path, codigo: str, situacao: str, ctx: Contexto, **extra) -> None:
        atual = ler_json(pasta / "status.json", {}) or {}
        atual.update({
            "codigo": codigo, "status": situacao, "status_em": _agora(),
            "lote_atual": ctx.lote_id, "versao_cv2": VERSAO_BUILD, **extra,
        })
        gravar_json(pasta / "status.json", atual)
        self.painel.aviso(codigo, self._relativo(pasta), situacao)

    def _info(self, pasta: Path, codigo: str, nome: str, ctx: Contexto, folhas: int) -> None:
        gravar_json(pasta / "info_projeto.json", {
            "codigo": codigo, "nome": nome, "fundo_codigo": ctx.fundo.codigo,
            "ano": ctx.ano, "folhas_esperadas": folhas,
            "estacao": ctx.estacao, "tipo_estacao": ctx.tipo_estacao,
            "operador": ctx.operador, "operador_email": ctx.operador_email,
            "teste": ctx.teste, "atualizado_em": _agora(),
            "gerado_por": f"CAMP Vision 2 {VERSAO_BUILD}",
        })

    # ------------------------------------------------------------ projeto
    def _resolver_projeto(self, ctx: Contexto) -> tuple[Path | None, str, str]:
        """(pasta, código, nome) — ou (None, '', motivo) para esperar."""
        pasta_fundo = estrutura.garantir_fundo(self.raiz_final, ctx.fundo)
        existente = estrutura.achar_projeto(pasta_fundo, ctx.codigo, ctx.projeto)
        if existente:
            codigo = estrutura.codigo_da_pasta(existente) or ctx.codigo
            nome = estrutura.nome_sem_codigo(existente.name)
            return existente, codigo, nome
        codigo = ctx.codigo
        if not codigo:
            if not self.painel.ligado:
                return None, "", "painel não configurado: não há como numerar projeto novo"
            chave = self.estado.chave_reserva(ctx.fundo.codigo, ctx.projeto)
            codigo = self.painel.reservar(
                ctx.fundo.codigo, ctx.projeto, chave, ano=ctx.ano, cidade=ctx.cidade,
                identificacao_original=ctx.projeto, operador=ctx.operador,
            ) or ""
            if not codigo:
                return None, "", f"painel não reservou o projeto ({self.painel.ultimo_erro or 'sem resposta'})"
            self.estado.guardar_codigo(ctx.fundo.codigo, ctx.projeto, codigo)
        nome = ctx.projeto or codigo
        return estrutura.garantir_projeto(pasta_fundo, codigo, nome, ctx.fundo), codigo, nome

    # ------------------------------------------------------------ principal
    def processar(self, unidade: Unidade, estado_vigia=None, cancelar=None) -> Resultado:
        ok, motivo = pronta(unidade, self.config)
        if not ok:
            return Resultado(AGUARDANDO, motivo)
        assinatura = unidade.assinatura()
        origem_rel = lambda p: str(Path("100 - Scanners") / unidade.relativo / p.relative_to(unidade.pasta))  # noqa: E731

        ctx = contexto(unidade, self.tabela)
        if ctx.problemas:
            motivo = "; ".join(ctx.problemas)
            self.livro.anotar("erro", arquivo_origem=str(unidade.relativo), detalhe=motivo)
            self.estado.anotar(unidade.chave, ERRO, motivo=motivo, assinatura=assinatura)
            _log.error("Unidade %s: %s", unidade.relativo, motivo)
            return Resultado(ERRO, motivo)

        pasta, codigo, nome = self._resolver_projeto(ctx)
        if pasta is None:
            self.estado.anotar(unidade.chave, AGUARDANDO, motivo=nome)
            _log.warning("Unidade %s aguardando: %s", unidade.relativo, nome)
            return Resultado(AGUARDANDO, nome)

        resultado = Resultado(PROCESSANDO, codigo=codigo, pasta_projeto=pasta)
        catalogacao = pasta / "catalogacao"
        erros_lote: list[dict] = []
        lote = {
            "versao": 2, "lote_id": ctx.lote_id, "codigo": codigo,
            "recebido_em": _agora(), "contexto": ctx.como_dict(),
            "origem": str(Path("100 - Scanners") / unidade.relativo),
            "arquivos": [], "nao_suportados": [], "erros": erros_lote,
        }
        self._status(pasta, codigo, PROCESSANDO, ctx)

        def falhar(motivo: str) -> Resultado:
            erros_lote.append({"arquivo": "", "categoria": "metadado", "gravidade": "bloqueia",
                               "origem": "CAMP Vision", "detalhe": motivo})
            lote["status"] = ERRO
            gravar_json(catalogacao / "lotes" / f"{ctx.lote_id}.json", lote)
            self._erros(catalogacao, erros_lote)
            self._status(pasta, codigo, ERRO, ctx, motivo=motivo)
            self.livro.anotar("erro", lote_id=ctx.lote_id, codigo_projeto=codigo,
                              arquivo_origem=str(unidade.relativo), detalhe=motivo)
            self.estado.anotar(unidade.chave, ERRO, motivo=motivo, assinatura=assinatura,
                               codigo=codigo)
            _log.error("Lote %s (%s): %s", ctx.lote_id, codigo, motivo)
            resultado.status, resultado.motivo, resultado.lote = ERRO, motivo, lote
            return resultado

        # ---- 4. cópia com nome CAMP, conferida por hash
        mapa_caminho = catalogacao / "mapa_origem.json"
        mapa: dict = ler_json(mapa_caminho, {}) or {}
        todos = unidade.arquivos()
        suportados = [a for a in todos if formatos.suportado(a)]
        for a in todos:
            if a not in suportados:
                lote["nao_suportados"].append(origem_rel(a))
                erros_lote.append({"arquivo": a.name, "categoria": "metadado", "gravidade": "aviso",
                                   "origem": "CAMP Vision", "detalhe": "formato não suportado — ficou na entrada"})
        documentos = formatos.agrupar(suportados)
        proximo = estrutura.proximo_documento(pasta)
        pasta_serie = pasta / estrutura.SERIES[ctx.serie]
        arquivados: list[tuple[Path, Path, str, str]] = []  # (origem, destino, código, sha)
        vistos_sha: dict[str, str] = {}
        try:
            for doc in documentos:
                shas = {v: sha256(v) for v in doc.versoes}
                ja = [mapa[s] for s in shas.values() if s in mapa]
                if ja:
                    codigo_doc = Path(ja[0]["destino"]).stem
                else:
                    codigo_doc = estrutura.codigo_documento(codigo, ctx.ano, ctx.serie, proximo)
                    proximo += 1
                for versao, soma in shas.items():
                    if soma in vistos_sha and vistos_sha[soma] != codigo_doc:
                        erros_lote.append({"arquivo": codigo_doc, "categoria": "duplicata", "gravidade": "corrigir",
                                           "origem": "CAMP Vision", "detalhe": f"conteúdo idêntico a {vistos_sha[soma]}"})
                    vistos_sha.setdefault(soma, codigo_doc)
                    if soma in mapa:
                        destino = self.raiz_final / mapa[soma]["destino"]
                        if destino.exists():
                            arquivados.append((versao, destino, codigo_doc, soma))
                            continue
                    destino = pasta_serie / f"{codigo_doc}{versao.suffix.lower()}"
                    if destino.exists() and sha256(destino) != soma:
                        return falhar(f"{destino.name} já existe com outro conteúdo")
                    if not destino.exists():
                        temporario = destino.with_name(f".{destino.name}.cv2tmp")
                        shutil.copy2(versao, temporario)
                        if sha256(temporario) != soma:
                            temporario.unlink(missing_ok=True)
                            return falhar(f"cópia de {versao.name} não confere (hash)")
                        os.replace(temporario, destino)
                        self.livro.anotar(
                            "copiado", lote_id=ctx.lote_id, codigo_projeto=codigo,
                            codigo_documento=codigo_doc, arquivo_origem=origem_rel(versao),
                            arquivo_destino=self._relativo(destino), nome_original=versao.name,
                            tamanho=destino.stat().st_size, sha256=soma,
                            operador=ctx.operador, estacao=ctx.estacao)
                        resultado.copiados += 1
                    mapa[soma] = {"destino": self._relativo(destino), "origem": origem_rel(versao),
                                  "nome_original": versao.name, "lote_id": ctx.lote_id}
                    arquivados.append((versao, destino, codigo_doc, soma))
                    lote["arquivos"].append({"origem": origem_rel(versao), "nome_original": versao.name,
                                             "destino": self._relativo(destino), "codigo": codigo_doc,
                                             "tipo": versao.suffix.lower().lstrip("."), "sha256": soma})
            gravar_json(mapa_caminho, mapa)
        except OSError as erro:
            gravar_json(mapa_caminho, mapa)
            return falhar(f"falha de disco/rede ao copiar: {erro}")

        if cancelar is not None and cancelar.is_set():
            return falhar("cancelado antes da leitura")

        # ---- 5. leitura: imagem leve por documento, cache fora do acervo
        cache = self.pasta_estado / "leitura" / codigo
        cache.mkdir(parents=True, exist_ok=True)
        por_codigo: dict[str, list[Path]] = {}
        for _o, destino, codigo_doc, _s in arquivados:
            por_codigo.setdefault(codigo_doc, []).append(destino)
        # Passada 1 — preparo (§2): imagem bruta → orientação pelo texto →
        # leitura 2000 px + preview 3000 px já girados; md5 e hash perceptual.
        bruta_dir = self.pasta_estado / "leitura_bruta" / codigo
        preview_dir = self.raiz_final / "_campvision" / "preview" / codigo
        preparo_caminho = catalogacao / "preparo.json"
        preparos: dict = ler_json(preparo_caminho, {}) or {}
        for codigo_doc, versoes in por_codigo.items():
            leitura_jpg = cache / f"{codigo_doc}.jpg"
            if codigo_doc in preparos and leitura_jpg.exists():
                continue
            melhor = formatos.Documento(codigo_doc, versoes).para_ler
            bruta = formatos.imagem_de_leitura(melhor, bruta_dir / f"{codigo_doc}.jpg")
            if bruta is None:
                erros_lote.append({"arquivo": codigo_doc, "categoria": "arquivo corrompido",
                                   "gravidade": "corrigir", "origem": "CAMP Vision",
                                   "detalhe": f"não abri {melhor.name} para leitura"})
                continue
            prep = mod_preparo.preparar(bruta, melhor, leitura_jpg, preview_dir / f"{codigo_doc}.jpg")
            preparos[codigo_doc] = {
                "md5": prep.md5, "hash_perceptual": prep.hash_perceptual,
                "rotacao": prep.orientacao.rotacao, "espelhada": prep.orientacao.espelhada,
                "incerta": prep.orientacao.incerta, "pontos": prep.orientacao.pontos,
                "lido_de": melhor.name,
                "preview": self._relativo(prep.preview) if prep.preview else "",
            }
            bruta.unlink(missing_ok=True)
        gravar_json(preparo_caminho, preparos)

        from . import planilha as mod_planilha, vigia as mod_vigia

        cfg_leitura = dataclasses.replace(
            self.config, status_concluido=PROCESSANDO, status_pronto=PROCESSANDO,
            espera_estabilidade_segundos=0, escrever_por_projeto=True, formatos_saida=("csv",),
        )
        projeto = mod_vigia.Projeto(pasta, [cache], pasta.name, self.raiz_final)
        evento = mod_vigia.processar(projeto, cfg_leitura, self.cliente, estado_vigia, cancelar)
        lote["custo_usd"] = round(evento.custo_usd, 4)
        if evento.falha:
            return falhar(f"leitura falhou: {evento.falha}")
        leituras = {Path(l.arquivo).stem: l for l in mod_planilha.ler_json(catalogacao / "leituras.json")}
        # Leitura que não aconteceu (chave inválida, sem crédito, rede) NÃO pode
        # virar "pronto": o painel receberia pranchas sem catalogação e o
        # original seria apagado. Fica em erro; --refazer-lote relê só essas.
        falhas = [c for c in por_codigo if c not in leituras or leituras[c].erro]
        if falhas:
            exemplo = leituras[falhas[0]].erro if falhas[0] in leituras else "sem leitura"
            return falhar(f"leitura falhou em {len(falhas)} de {len(por_codigo)} documento(s): {exemplo[:160]}")
        for codigo_doc in por_codigo:
            if codigo_doc in leituras:
                self.livro.anotar("lido", lote_id=ctx.lote_id, codigo_projeto=codigo,
                                  codigo_documento=codigo_doc,
                                  detalhe="carimbo lido" if leituras[codigo_doc].carimbo_encontrado
                                  else "sem carimbo")

        # Passada 3 complementos (§2.3, §4.4) — em código, sobre o projeto inteiro.
        enriquecer(leituras, preparos, ctx.fundo)
        mod_planilha.escrever_json(list(leituras.values()), catalogacao / "leituras.json")
        mod_planilha.escrever_csv(list(leituras.values()), catalogacao / "catalogacao.csv")

        # ---- 6. EXIF/XMP completo em todas as versões
        itens = []
        ja_vistos: set[Path] = set()
        for origem, destino, codigo_doc, _s in arquivados:
            if destino in ja_vistos:
                continue
            ja_vistos.add(destino)
            leitura = leituras.get(codigo_doc)
            campos = {c.rotulo: (leitura.valores.get(c.nome, "") if leitura else "") for c in CAMPOS}
            campos.update({
                "_credito": ctx.fundo.credito, "_codigo": codigo_doc, "_fundo": ctx.fundo.codigo,
                "_serie": ctx.serie, "_operador": ctx.operador, "_estacao": ctx.estacao,
                "_digitalizado_em": ctx.enviado_em, "_nome_original": origem.name,
            })
            if not campos.get("Projeto"):
                campos["Projeto"] = nome
            itens.append((destino, campos))
        gravados, avisos = mod_metadados.gravar_em_lote(itens, self.config.identidade())
        conferidos = conferir_exif([(d, Path(d).stem) for d, _ in itens])
        lote["exif"] = {"total": len(itens), "gravados": gravados, "conferidos": len(conferidos),
                        "avisos": avisos}
        for destino, campos in itens:
            if destino in conferidos:
                self.livro.anotar("exif_gravado", lote_id=ctx.lote_id, codigo_projeto=codigo,
                                  codigo_documento=campos["_codigo"],
                                  arquivo_destino=self._relativo(destino))
        if self.config.entrada_exigir_exif and len(conferidos) < len(itens):
            return falhar(f"EXIF conferido em {len(conferidos)} de {len(itens)} arquivo(s)"
                          + (f": {avisos[-1]}" if avisos else ""))

        # ---- 7. catalogacao/: contatos, erros, lote
        formatos.folha_de_contatos(
            [(c, cache / f"{c}.jpg") for c in sorted(leituras)][:1000], catalogacao / "contatos.jpg")
        todos_erros = erros_das_leituras(leituras.values()) + erros_lote
        self._erros(catalogacao, todos_erros)
        bloqueantes = sum(1 for e in todos_erros if e["gravidade"] == "bloqueia")
        gravar_json(catalogacao / "pacote_tainacan.json",
                    pacote_tainacan(codigo, nome, ctx, leituras, preparos, mapa, self.raiz_final))
        lote["aceite"] = checklist_aceite(por_codigo, leituras, todos_erros, lote)

        # ---- 8. pronto (último), info, aviso
        documentos_projeto = len({Path(v["destino"]).stem for v in mapa.values()})
        imagens = len(mapa)
        self._info(pasta, codigo, nome, ctx, documentos_projeto)
        lote.update({"status": PRONTO, "concluido_em": _agora(),
                     "contagens": {"documentos": len(por_codigo), "arquivos": len(arquivados),
                                   "copiados": resultado.copiados,
                                   "com_carimbo": sum(1 for c in por_codigo if c in leituras
                                                      and leituras[c].carimbo_encontrado),
                                   "erros": len(todos_erros), "bloqueantes": bloqueantes}})
        gravar_json(catalogacao / "lotes" / f"{ctx.lote_id}.json", lote)
        prontos = (ler_json(pasta / "status.json", {}) or {}).get("lotes_prontos", [])
        self._status(
            pasta, codigo, PRONTO, ctx,
            lotes_prontos=sorted(set(prontos) | {ctx.lote_id}),
            imagens=imagens, folhas=documentos_projeto,
            com_carimbo=sum(1 for l in leituras.values() if l.carimbo_encontrado),
            a_revisar=sum(1 for l in leituras.values() if mod_planilha.precisa_revisar(l)),
            erros_bloqueantes=bloqueantes, motivo="",
        )

        # ---- 9. apaga o original (só o que foi arquivado e conferido)
        if self.config.apagar_original_apos_pronto:
            resultado.apagados = self._apagar_originais(unidade, arquivados, ctx, codigo,
                                                        sobrou=bool(lote["nao_suportados"]))
        self.estado.anotar(unidade.chave, PRONTO, assinatura=assinatura, codigo=codigo,
                           lote_id=ctx.lote_id)
        _log.info("Lote %s → %s pronto: %d documento(s), %d copiado(s), %d apagado(s).",
                  ctx.lote_id, codigo, len(por_codigo), resultado.copiados, resultado.apagados)
        resultado.status, resultado.lote = PRONTO, lote
        return resultado

    def _erros(self, catalogacao: Path, erros: list[dict]) -> None:
        gravar_json(catalogacao / "erros.json", erros)

    def _apagar_originais(self, unidade: Unidade, arquivados, ctx: Contexto, codigo: str,
                          sobrou: bool) -> int:
        apagados = 0
        for origem, destino, codigo_doc, soma in arquivados:
            try:
                if not origem.exists() or not destino.exists():
                    continue
                # A cópia foi conferida por hash ao ser feita; o destino agora
                # tem EXIF (outro hash). Aqui confere que o ORIGINAL não mudou
                # desde então — se mudou, não apaga.
                if sha256(origem) != soma:
                    _log.warning("%s mudou depois da cópia — não apago.", origem)
                    continue
                tamanho = origem.stat().st_size
                origem.unlink()
                apagados += 1
                self.livro.anotar(
                    "apagado_original", lote_id=ctx.lote_id, codigo_projeto=codigo,
                    codigo_documento=codigo_doc,
                    arquivo_origem=str(Path("100 - Scanners") / unidade.relativo / origem.relative_to(unidade.pasta)),
                    arquivo_destino=self._relativo(destino), nome_original=origem.name,
                    tamanho=tamanho, sha256=soma)
            except OSError as erro:
                _log.warning("Não apaguei %s: %s", origem, erro)
        if not sobrou:
            for marcador in MARCADORES:
                (unidade.pasta / marcador).unlink(missing_ok=True)
        # Remove pastas vazias de dentro para fora, sem subir acima da unidade.
        for pasta in sorted((p for p in unidade.pasta.rglob("*") if p.is_dir()),
                            key=lambda p: len(p.parts), reverse=True):
            try:
                pasta.rmdir()
            except OSError:
                pass
        try:
            unidade.pasta.rmdir()
        except OSError:
            pass
        return apagados
