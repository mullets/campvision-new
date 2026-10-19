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
import fnmatch
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
from . import triagem as mod_triagem
from . import metadados as mod_metadados
from .atomico import gravar_json as _gravar_json_atomico
from .config import VERSAO_BUILD, Config
from .esquema import CAMPOS

_log = logging.getLogger("cv2.entrada")

PROCESSANDO, PRONTO, ERRO, AGUARDANDO = "processando", "pronto", "erro", "aguardando"
DESCARTADO = "descartado"  # o painel respondeu 410 à reserva: o lote não vale mais (C7)
# Parada do serviço no meio do lote: não é defeito do lote, volta à fila no próximo início.
CANCELADO = "cancelado antes da leitura"
MARCADORES = ("manifesto.json", "info_projeto.json", "status.json", "status_mac.json")
# O Mac grava um info_lote_AAAAMMDD-HHMMSS_mac.json por lote: é marcador (metadado
# da estação), não arquivo do lote — não vai para o acervo nem gera aviso de formato.
_INFO_LOTE = "info_lote_*_mac.json"
MARCAS_DE_FIM = {"enviado_windows", "enviado_mac"}


def e_marcador(nome: str) -> bool:
    return nome in MARCADORES or fnmatch.fnmatch(nome, _INFO_LOTE)


# Cópia em andamento / resto de cópia interrompida das estações: `X.tif.copiando` (Windows e Mac) e `status.json.<id>.tmp`
# (gravação atômica). Nunca são arquivo do lote: não contam, não são copiados e não viram "formato não suportado".
SUFIXOS_PARCIAIS = (".copiando", ".tmp")


def e_parcial(nome: str) -> bool:
    return nome.lower().endswith(SUFIXOS_PARCIAIS)


def marcadores_da(pasta: Path) -> list[Path]:
    """Os marcadores que existem na pasta (inclui info_lote_*_mac.json)."""
    achados = [pasta / m for m in MARCADORES if (pasta / m).exists()]
    achados += sorted(pasta.glob(_INFO_LOTE))
    return achados
PASTAS_DE_FORMATO = {
    "tif", "tiff", "jpg", "jpeg", "dng", "pdf", "png", "raw", "preview", "previews",
    "alta", "baixa", "alta resolucao", "baixa resolucao", "matriz", "master",
}


class Andamento:
    """Uma linha de log por etapa longa: "copiando 7 de 38". Na primeira, na
    última e no máximo a cada `intervalo` segundos — cópia, preparo e EXIF de
    TIFF pela rede levam minutos sem nada no log e parecia travado (14/10)."""

    def __init__(self, lote_id: str, etapa: str, total: int, estado_vigia=None,
                 intervalo: float = 15.0) -> None:
        self.lote_id, self.etapa, self.total = lote_id, etapa, total
        self.estado_vigia, self.intervalo = estado_vigia, intervalo
        self.feitos = 0
        self._ultimo = -1e9

    def passo(self, n: int = 1) -> None:
        self.feitos += n
        agora = time.monotonic()
        if self.feitos in (1, self.total) or agora - self._ultimo >= self.intervalo:
            self._ultimo = agora
            _log.info("Lote %s: %s %d de %d", self.lote_id, self.etapa, self.feitos, self.total)
        if self.estado_vigia is not None:
            self.estado_vigia.situacao = f"{self.etapa} {self.feitos} de {self.total}"


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
    _gravar_json_atomico(caminho, dados)


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
            and not e_marcador(p.name) and not e_parcial(p.name)
        )

    def parciais(self) -> list[Path]:
        return [p for p in self.pasta.rglob("*") if p.is_file() and e_parcial(p.name)]

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


# Subpastas que a estação Mac cria DENTRO da pasta de material: "01 - Arquivo Arquivístico (TIFF)",
# "03 - Preview (JPG)". Nunca são um lote: o lote é a pasta do projeto, duas acima.
_SUBPASTA_MAC = re.compile(r"^\d{2} (arquivo arquivistico|preview)\b")


def _e_subpasta_mac(nome: str) -> bool:
    return bool(_SUBPASTA_MAC.match(estrutura.normalizar(nome) or ""))


def _estruturado_como_mac(pasta: Path) -> bool:
    """<projeto>/<NN - Material>/<NN - Arquivo Arquivístico (TIFF) | NN - Preview (JPG)>/"""
    try:
        return any(sub.is_dir() and _e_subpasta_mac(sub.name)
                   for material in pasta.iterdir() if material.is_dir() and not material.name.startswith((".", "_"))
                   for sub in material.iterdir())
    except OSError:
        return False


def descobrir(raiz: Path, profundidade_maxima: int = 6) -> list[Unidade]:
    """Unidade = primeira pasta que tem arquivo direto ou só pastas de formato.

    Subpasta do Mac ("01 - Arquivo Arquivístico (TIFF)", "03 - Preview (JPG)") sobe para a pasta do
    projeto: enquanto o Mac copia, a pasta do projeto ainda não tem os JSON e cada subpasta virava um
    lote — o mesmo projeto era lido (e pago) três vezes (P0092, 19/10).
    """
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
            f.is_file() and not f.name.startswith(".") and not e_marcador(f.name) and not e_parcial(f.name)
            for f in filhos
        )
        subpastas = [f for f in filhos if f.is_dir() and not f.name.startswith((".", "_"))]
        if tem_arquivo or bool(marcadores_da(pasta)) or (
            subpastas and all(_e_pasta_de_formato(f.name) for f in subpastas)
        ):
            if pasta != raiz:
                achadas.append(Unidade(pasta, pasta.relative_to(raiz)))
            return
        for sub in subpastas:
            caminhar(sub, nivel + 1)

    if raiz.is_dir():
        caminhar(raiz, 0)
    unicas: dict[str, Unidade] = {}
    for unidade in achadas:
        partes = unidade.relativo.parts
        i = next((k for k, parte in enumerate(partes) if _e_subpasta_mac(parte)), None)
        if i is not None:
            corte = i - 1 if i >= 2 and re.match(r"^\s*\d{2}\s*[-–]", partes[i - 1]) else max(i, 1)
            relativo = Path(*partes[:corte])
            unidade = Unidade(raiz / relativo, relativo)
        unicas.setdefault(unidade.chave, unidade)
    return list(unicas.values())


def _marca_de_fim(pasta: Path) -> bool:
    """status.json/status_mac.json com `status` OU `etapa` = enviado_windows/enviado_mac (C1)."""
    for nome in ("status.json", "status_mac.json"):
        dados = ler_json(pasta / nome, {})
        if isinstance(dados, dict) and (
                {str(dados.get("status", "")), str(dados.get("etapa", ""))} & MARCAS_DE_FIM):
            return True
    return False


def pronta(unidade: Unidade, config: Config) -> tuple[bool, str]:
    """A estação terminou de mandar?"""
    arquivos = unidade.arquivos()
    if not arquivos:
        return False, "vazia"
    # `.copiando` escrito há menos que a espera = a estação ainda está copiando: espera. Mais antigo = resto de cópia
    # interrompida: é ignorado (não conta, não é copiado) em vez de travar o lote para sempre.
    recente = time.time() - config.espera_estabilidade_segundos
    for parcial in unidade.parciais():
        try:
            if parcial.stat().st_mtime > recente:
                return False, "cópia em andamento (.copiando recente)"
        except OSError:
            return False, "arquivo sumiu durante a leitura"
    if _estruturado_como_mac(unidade.pasta):
        # O Mac grava status_mac.json POR ÚLTIMO (F12). Sem ele, ou com arquivo mais novo que ele (outro
        # envio para o mesmo projeto começou), o Mac ainda está copiando — mesmo que a pasta pareça parada.
        status_mac = unidade.pasta / "status_mac.json"
        if not status_mac.exists() or not _marca_de_fim(unidade.pasta):
            return False, "aguardando o Mac terminar o envio (status_mac.json)"
        try:
            fim = status_mac.stat().st_mtime
            if any(max(a.stat().st_mtime, a.stat().st_ctime) > fim + 2 for a in arquivos):
                return False, "envio novo do Mac em andamento"
        except OSError:
            return False, "arquivo sumiu durante a leitura"
    marcada = (unidade.pasta / "manifesto.json").exists() or _marca_de_fim(unidade.pasta)
    espera = config.espera_estabilidade_segundos if marcada else config.entrada_quieto_minutos * 60
    limite = time.time() - espera
    for arquivo in arquivos:
        try:
            info = arquivo.stat()
            # O Mac copia com copy2 (mantém o mtime do scan): só o ctime diz que
            # o arquivo acabou de chegar. Sem isso a pasta TIFF era pega no meio
            # da cópia, antes do info_lote (SESI Marília, 14/10).
            if max(info.st_mtime, info.st_ctime) > limite:
                return False, "arquivos ainda chegando"
        except OSError:
            return False, "arquivo sumiu durante a leitura"
    esperada = _contagem_esperada(unidade)
    # O manifesto.json tem nome fixo: um 2º lote no mesmo projeto, enviado antes de o CV2 pegar o 1º, o sobrescreve e ele passa
    # a descrever só o lote mais novo. Por isso a contagem é um PISO: menos que o manifesto = ainda chegando; mais = há arquivos
    # de um lote anterior (já conferidos pela estação) que entram junto.
    if esperada is not None and len(arquivos) < esperada:
        return False, f"contagem {len(arquivos)} de {esperada}"
    return True, ""


MINUTOS_LOTE_INCOMPLETO = 30  # CAMP-051/aviso: faltando arquivo do manifesto há mais que isto → alerta


def lote_incompleto_parado(unidade: Unidade, motivo: str, minutos: int = MINUTOS_LOTE_INCOMPLETO,
                           agora: float | None = None) -> str:
    """Texto de alerta quando o lote tem MENOS arquivos que o manifesto e nada chega há `minutos`.

    `pronta()` devolve "contagem N de M" e o lote espera; sem isto esperava em silêncio para sempre."""
    if not motivo.startswith("contagem "):
        return ""
    try:
        ultimo = max(max(a.stat().st_mtime, a.stat().st_ctime) for a in unidade.arquivos())
    except (OSError, ValueError):
        return ""
    parado = ((agora if agora is not None else time.time()) - ultimo) / 60
    if parado < minutos:
        return ""
    return (f"lote {unidade.relativo} incompleto: {motivo}; nada chega há {int(parado)} min — "
            "conferir a cópia na estação")


def serie_do_arquivo(arquivo: Path, unidade: Unidade, padrao: str) -> str:
    """Série pela pasta de material (`03 - Fotografias/...`) dentro do lote; senão `padrao`."""
    try:
        partes = arquivo.relative_to(unidade.pasta).parts[:-1]
    except ValueError:
        return padrao
    for parte in partes:
        if _e_pasta_de_material(parte):
            achada = estrutura.serie_do_texto(parte)
            if achada:
                return achada
    return padrao


def conferir_manifesto(unidade: Unidade) -> list[str]:
    """Confere o manifesto.json das estações (C1): contagem, tamanho e SHA-256.

    Devolve os problemas (lista vazia = íntegro ou sem o que conferir). Só LÊ:
    quem chama decide parar o lote sem copiar nem apagar nada.
    """
    manifesto = ler_json(unidade.pasta / "manifesto.json", {})
    if not isinstance(manifesto, dict) or not manifesto:
        return []
    problemas: list[str] = []
    arquivos = unidade.arquivos()
    esperada = _contagem_esperada(unidade)
    if esperada is not None and len(arquivos) < esperada:
        problemas.append(f"manifesto diz {esperada} arquivo(s); a pasta tem {len(arquivos)}")
    listados = manifesto.get("arquivos")
    if isinstance(listados, list):
        relativos = {a.relative_to(unidade.pasta).as_posix(): a for a in arquivos}
        for item in listados:
            if not isinstance(item, dict) or not item.get("caminho"):
                continue
            caminho = str(item["caminho"]).replace("\\", "/").strip("/")
            alvo = relativos.get(caminho) or next(
                (a for rel, a in relativos.items() if rel.endswith("/" + caminho) or caminho.endswith("/" + rel)), None)
            if alvo is None:
                problemas.append(f"arquivo do manifesto não chegou: {caminho}")
                continue
            try:
                tamanho = alvo.stat().st_size
                if item.get("tamanho") not in (None, "") and int(item["tamanho"]) != tamanho:
                    problemas.append(f"tamanho diferente do manifesto em {caminho}: {tamanho} != {item['tamanho']}")
                    continue
                esperado = str(item.get("sha256") or "").lower()
                if esperado and sha256(alvo) != esperado:
                    problemas.append(f"arquivo corrompido (SHA-256 diferente do manifesto): {caminho}")
            except (OSError, ValueError, TypeError) as erro:
                problemas.append(f"não consegui conferir {caminho}: {erro}")
    return problemas


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
    codigo: str = ""          # F002-P0002, código oficial (reservado no painel)
    p_estacao: str = ""       # "P0001" que a estação escreveu: NÃO é oficial, só pista
    ano: str = ""
    cidade: str = ""
    operador: str = ""
    operador_email: str = ""
    estacao: str = ""
    tipo_estacao: str = ""
    enviado_em: str = ""
    lote_id: str = ""
    teste: bool = False
    serie_deduzida: bool = False
    series: list[str] = field(default_factory=list)  # lote com mais de uma série (Mac): a de cada arquivo vem da pasta
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


def lote_mac(pasta: Path) -> dict:
    """O info_lote_*_mac.json mais recente da pasta (estação Mac/VueScan), ou {}."""
    for caminho in sorted(pasta.glob("info_lote_*.json"), reverse=True):
        dados = ler_json(caminho, {})
        if isinstance(dados, dict) and dados:
            return dados
    return {}


def dados_do_mac(mac: dict) -> dict:
    """O que o info_lote do Mac acrescenta ao contexto (C1): operador, estação, tipo, séries."""
    saida: dict = {}
    for destino, origens in (("operador", ("operador",)), ("estacao", ("estacao_id", "estacao")),
                             ("tipo_estacao", ("estacao_tipo", "tipo_estacao")),
                             ("series", ("series",)),
                             ("quantidade_documentos", ("quantidade_documentos",))):
        for origem in origens:
            if mac.get(origem) not in (None, "", []):
                saida[destino] = mac[origem]
                break
    return saida


def codigo_do_mac(dados: dict) -> str:
    """F009-P0060 do info_lote do Mac: é oficial.

    O número P sozinho não é oficial (Rafa, 09/10) quando a estação o escreve à mão. O Mac nunca
    calcula P: projeto existente vem da lista do painel e projeto novo é reservado no painel no F9
    (sistema_mac._reservar_numero_projeto). Por isso vale com ou sem projeto_ja_existia (P0092, 19/10).
    """
    if dados.get("projeto_ja_existia") is not True and "mac" not in str(dados.get("estacao") or "").lower():
        return ""
    fundo = str(dados.get("codigo_fundo") or "").strip().upper()
    numero = str(dados.get("numero_projeto") or "").strip().upper()
    if re.fullmatch(r"F\d{3}", fundo) and re.fullmatch(r"P\d{4}", numero):
        return f"{fundo}-{numero}"
    return ""


def contexto(unidade: Unidade, tabela: mod_fundos.Tabela, serie_padrao: str = "S01") -> Contexto:
    """Monta o contexto: manifesto/info da estação > caminho da pasta."""
    ctx = Contexto()
    manifesto = ler_json(unidade.pasta / "manifesto.json", {}) or {}
    mac = lote_mac(unidade.pasta)
    bruto_info = mod_info.ler_bruto(unidade.pasta)
    traduzido = mod_info.ler(unidade.pasta)
    dados = {**{k.split(".")[-1]: v for k, v in bruto_info.items()}, **dados_do_mac(mac), **manifesto}

    partes = [p for p in unidade.relativo.parts if not _e_pasta_de_formato(p)]

    # Fundo: código/fundo explícito do manifesto, senão o caminho, sempre pela tabela.
    # Fundo: pela TABELA, nunca chutado. O caminho (F022 - MSL - ...) e o campo
    # explícito do manifesto/info são duas fontes; se só uma identifica, vale ela;
    # se as duas identificam fundos DIFERENTES, é conflito e o lote para.
    explicito = _texto(dados, "fundo_codigo", "codigo_fundo", "fundo")
    pelo_explicito = tabela.identificar(explicito) if explicito else None
    pelo_caminho = None
    for parte in partes:
        pelo_caminho = tabela.identificar(parte)
        if pelo_caminho:
            break
    if pelo_explicito and pelo_caminho and pelo_explicito is not pelo_caminho:
        ctx.problemas.append(f"conflito de fundo: info/manifesto diz '{explicito}' "
                             f"({pelo_explicito.codigo}), a pasta diz {pelo_caminho.codigo}")
    ctx.fundo = pelo_explicito or pelo_caminho
    if ctx.fundo is None:
        ctx.problemas.append(
            f"fundo '{explicito}' não está na tabela de autoridade e a pasta não traz código F000"
            if explicito else "não achei o fundo (código F000, sigla ou nome) no caminho")

    # Série: manifesto, senão a pasta de material no caminho.
    ctx.serie = _texto(dados, "serie").upper()
    if not re.fullmatch(r"S(0[1-8]|99)", ctx.serie):
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
        # A estação disse a série: o Mac manda `series` no info_lote e a pasta de
        # material fica DENTRO do lote (P0060 - SESI/03 - Fotografias/...). Vale
        # ela; adivinhar pela imagem pôs 14 fotos claras em S01 (SESI, 14/10).
        declaradas = {estrutura.serie_do_texto(str(s)) for s in (dados.get("series") or [])
                      if isinstance(dados.get("series"), list)} - {""}
        if not declaradas and unidade.pasta.is_dir():
            declaradas = {estrutura.serie_do_texto(f.name) for f in unidade.pasta.iterdir()
                          if f.is_dir() and _e_pasta_de_material(f.name)} - {""}
        if len(declaradas) == 1:
            ctx.serie = next(iter(declaradas))
        elif len(declaradas) > 1:
            # Lote misto (negativos + slides + fotos): cada arquivo leva a série da sua
            # pasta de material (serie_do_arquivo); ctx.serie só cobre o que ficar solto.
            ctx.series = sorted(declaradas)
            ctx.serie = ctx.series[0]
    if not ctx.serie:
        # Sem pasta de material nem manifesto: a estação de fotografia/VueScan
        # manda foto; o resto (Contex, Universal) manda prancha. "99 - Não
        # identificado" só se o config pedir — mandava tudo para lá.
        estacao = estrutura.normalizar(f"{_texto(dados, 'tipo_estacao')} {_texto(dados, 'estacao')}")
        ctx.serie = "S03" if re.search(r"\b(foto|vuescan|camera)", estacao) else serie_padrao
        ctx.serie_deduzida = True

    # Projeto: código explícito, nome do manifesto/info, senão a pasta mais funda.
    ctx.codigo = _texto(dados, "codigo", "projeto_codigo").upper() or codigo_do_mac(mac)
    if ctx.codigo and not estrutura.CODIGO_PROJETO.fullmatch(ctx.codigo):
        if re.fullmatch(r"P\d{4}", ctx.codigo):
            ctx.p_estacao = ctx.codigo  # número da estação: pista, não código (Rafa, 09/10)
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
    # Número P sozinho ("P0001", ou a pasta "P0001 - EXPO Brasil...") NÃO é
    # oficial (Rafa, 09/10): o código vem da reserva no painel. O número da
    # estação vira pista — vai para o painel na reserva e fica no lote.
    if not ctx.codigo and not ctx.p_estacao:
        # O P que a estação escreveu: numero_projeto (info/info_lote) ou identificacao_original.
        for candidato in (_texto(dados, "numero_projeto"), _texto(dados, "identificacao_original")):
            achado = re.match(r"^\s*P(\d{4})\b", candidato or "", re.IGNORECASE)
            if achado:
                ctx.p_estacao = f"P{achado.group(1)}"
                break
    if not ctx.codigo and not ctx.p_estacao:
        for candidato in [nome, *reversed(restantes)]:
            achado = re.match(r"^\s*P(\d{4})\b", candidato or "", re.IGNORECASE)
            if achado:
                ctx.p_estacao = f"P{achado.group(1)}"
                break
    # Nome que é só o código não é nome: usa o resto da pasta.
    if re.fullmatch(r"\s*(F\d{3}-)?P\d{4}\s*", nome or "", re.IGNORECASE):
        nome = next((r for r in reversed(restantes)
                     if re.sub(r"^\s*(F\d{3}-)?P\d{4}\s*[-–]?\s*", "", r, flags=re.IGNORECASE)), "")
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
    ctx.estacao = _texto(dados, "estacao", "estacao_id") or traduzido.get("estacao", "")
    ctx.tipo_estacao = _texto(dados, "tipo_estacao", "estacao_tipo") or traduzido.get("tipo_estacao", "")
    ctx.enviado_em = (_texto(dados, "enviado_em", "horario", "data_envio", "finalizado_em")
                      or traduzido.get("enviado_em", ""))
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


# ---- EXIF retomável (CAMP-010)

PASSO_EXIF = "exif"  # estado local: o lote parou no meio da gravação de metadados
EXIF_PENDENTE = "exif_pendente.json"


def campos_do_exif(ctx: Contexto, codigo_doc: str, leitura, nome: str, nome_original: str) -> dict:
    """Os campos que vão para o EXIF/XMP de um documento (mesmos no lote e na retomada)."""
    campos = {c.rotulo: (leitura.valores.get(c.nome, "") if leitura else "") for c in CAMPOS}
    campos.update({
        "_credito": ctx.fundo.credito, "_codigo": codigo_doc, "_fundo": ctx.fundo.codigo,
        "_autor": ctx.fundo.nome,
        "_serie": codigo_doc.split("-")[3] if codigo_doc.count("-") >= 4 else ctx.serie,
        "_operador": ctx.operador, "_estacao": ctx.estacao,
        "_digitalizado_em": ctx.enviado_em, "_nome_original": nome_original,
    })
    if not campos.get("Projeto"):
        campos["Projeto"] = nome
    return campos


def marcar_exif_pendente(catalogacao: Path, lote_id: str, destinos: list[Path], raiz_final: Path) -> None:
    def rel(d: Path) -> str:
        try:
            return str(d.relative_to(raiz_final))
        except ValueError:
            return str(d)
    gravar_json(catalogacao / EXIF_PENDENTE, {"lote_id": lote_id, "desde": _agora(),
                                              "arquivos": [rel(d) for d in destinos]})


def limpar_exif_pendente(catalogacao: Path) -> None:
    (catalogacao / EXIF_PENDENTE).unlink(missing_ok=True)


def exif_incompleto(pasta_projeto: Path, raiz_final: Path) -> list[Path]:
    """Arquivos do projeto cujo EXIF ficou pela metade (marcador + conferência). [] = ok."""
    marcador = ler_json(pasta_projeto / "catalogacao" / EXIF_PENDENTE, None)
    if not isinstance(marcador, dict):
        return []
    alvos = [raiz_final / a for a in marcador.get("arquivos") or [] if (raiz_final / a).exists()]
    if not alvos:
        return []
    ok = conferir_exif([(a, a.stem) for a in alvos])
    return [a for a in alvos if a not in ok]


# ================================================================ erros

def enriquecer(leituras: dict, preparos: dict, fundo) -> None:
    """Preparo, duplicatas e autoria em cada leitura do projeto (método §2.3, §4.4, §8.1)."""
    from .derivacao import autoria_divergente, identificar_pessoas

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
        pts = sorted((float(v) for v in (p.get("pontos") or {}).values()), reverse=True)
        l.confianca_rotacao = round((pts[0] - (pts[1] if len(pts) > 1 else 0.0)) / pts[0], 2) \
            if pts and pts[0] > 0 else 0.0
        l.origem_formato = p.get("origem_formato", "") or l.origem_formato
        tri = p.get("triagem") or {}
        l.triagem = tri
        l.serie_incerta = bool(tri.get("incerta"))
        ano = l.ano_do_projeto or l.valores.get("ano", "")
        l.fora_do_periodo = fundo.fora_do_periodo(ano)
        if l.modo == "fotografia":
            l.pessoas_identificadas = identificar_pessoas((l.foto or {}).get("pessoas") or [],
                                                          fundo.chaves_de_identidade)
            if l.pessoas_identificadas:
                nomes = " e ".join(l.pessoas_identificadas) if len(l.pessoas_identificadas) <= 2 \
                    else ", ".join(l.pessoas_identificadas[:-1]) + " e " + l.pessoas_identificadas[-1]
                legenda = l.valores.get("titulo_prancha", "")
                if not legenda.startswith(nomes):
                    l.valores["titulo_prancha"] = f"{nomes} — {legenda}" if legenda else nomes
                nota = "pessoas nomeadas pelas chaves de identidade do fundo (não pelo modelo)"
                if nota not in l.ressalvas:
                    l.ressalvas.append(nota)
        l.duplicata_de, l.tipo_duplicata = "", ""
        if l.md5 and l.md5 in por_md5:
            l.duplicata_de, l.tipo_duplicata = por_md5[l.md5], "exata"
        elif l.hash_perceptual:
            for outro, h in hashes:
                if mod_preparo.distancia(h, l.hash_perceptual) <= mod_preparo.DISTANCIA_PERCEPTUAL:
                    l.duplicata_de, l.tipo_duplicata = outro, "quase"
                    break
        if l.md5:
            por_md5.setdefault(l.md5, codigo)
        if l.hash_perceptual and not l.duplicata_de:
            hashes.append((codigo, l.hash_perceptual))
        arquiteto = (l.lidos_originais or l.valores).get("arquiteto", "") or l.valores.get("arquiteto", "")
        l.autoria_divergente = l.modo == "prancha" and autoria_divergente(arquiteto, fundo.autorizados)
        ident = p.get("identidade") or {}
        l.suspeita_grupo = bool(ident.get("divergente"))
        l.capa = str(ident.get("capa") or "")
        l.sem_identificacao = bool(ident.get("sem_identificacao"))
        if ident.get("carimbo"):
            l.carimbo = dict(ident["carimbo"])
        elif not l.carimbo:
            from .identidade import carimbo as _carimbo
            l.carimbo = _carimbo(l)


def indice_do_fundo(pasta_fundo: Path, raiz_final: Path) -> dict[str, Path]:
    """SHA-256 → arquivo já arquivado em algum projeto do fundo (pelos mapa_origem.json)."""
    indice: dict[str, Path] = {}
    for mapa in (pasta_fundo / estrutura.PASTA_PROJETOS).glob("*/catalogacao/mapa_origem.json"):
        for sha, item in (ler_json(mapa, {}) or {}).items():
            destino = raiz_final / item.get("destino", "")
            if item.get("destino") and destino.exists():
                indice[sha] = destino
    return indice


def _diz(l) -> str:
    c = l.carimbo or {}
    return " · ".join(v for v in (c.get("obra"), c.get("local"), c.get("cliente")) if v) \
        or (l.lidos_originais or l.valores).get("projeto", "")


def _proporcao(caminho: Path) -> float:
    """Lado maior / lado menor da imagem de leitura (tira estreita = não é prancha)."""
    try:
        from PIL import Image

        with Image.open(caminho) as im:
            a, b = im.size
        return max(a, b) / max(1, min(a, b))
    except (OSError, ValueError, ImportError):
        return 0.0


def marcar_identidade(preparos: dict, analise, plano: list[dict], leituras: dict) -> None:
    """Grava no preparo de cada folha o que a identificação decidiu (sobrevive a releituras)."""
    from .identidade import carimbo

    varios = len(plano) > 1 or len(analise.grupos) > 1
    for codigo, l in leituras.items():
        prep = preparos.setdefault(codigo, {})
        grupo = analise.grupo_de(codigo)
        destino = next((d for d in plano if codigo in d["documentos"]), None)
        no_grupo = bool(destino and codigo in destino.get("folhas_grupo", []))
        prep["identidade"] = {
            "grupo": grupo.titulo if grupo else "",
            "carimbo": carimbo(l),
            "capa": analise.capas.get(codigo, ""),
            # Folha de OUTRA obra (grupo pequeno sem projeto na base) que foi junto com o principal.
            "divergente": bool(grupo and destino and not no_grupo),
            "sem_identificacao": bool(varios and codigo in analise.sem_identificacao),
        }


def relatorio_identificacao(analise, plano: list[dict], pendente: str, leituras: dict, origem: str) -> str:
    """O que o carimbo diz, grupo a grupo, e para onde cada folha vai (ou o que falta decidir)."""
    from .identidade import carimbo

    linhas = [f"Identificação pelo carimbo — {origem}", ""]
    for i, g in enumerate(analise.grupos, 1):
        destino = next((d for d in plano if set(g.folhas) & set(d["documentos"])), None)
        linhas.append(f"Grupo {i}: {g.titulo} ({len(g.folhas)} folha(s))"
                      + (f" → {destino['codigo']} ({destino['motivo']})" if destino else ""))
        if g.cliente:
            linhas.append(f"   cliente: {g.cliente}")
        if g.ids:
            linhas.append(f"   identificadores: {', '.join(sorted(g.ids))}")
        for c in g.folhas:
            dz = carimbo(leituras[c]) if c in leituras else {}
            linhas.append(f"   {c}: " + " · ".join(v for v in (dz.get("obra"), dz.get("local"), dz.get("data")) if v))
    if analise.sem_identificacao:
        linhas += ["", "Sem identificação (o carimbo não diz a obra):",
                   *(f"   {c}" for c in analise.sem_identificacao)]
    if analise.capas:
        linhas += ["", "Capas, envelopes e tiras (não são pranchas):",
                   *(f"   {c}: {m}" for c, m in sorted(analise.capas.items()))]
    if analise.mesmo_endereco:
        linhas += ["", "Obras diferentes no MESMO endereço — perguntar se são projetos separados ou fases:"]
        for juntos in analise.mesmo_endereco:
            linhas.append("   " + " | ".join(analise.grupos[i].titulo for i in juntos))
    for f in analise.fases:
        linhas += ["", f"Escritórios diferentes em décadas diferentes em '{f['titulo']}' — fases ou obras?"]
        linhas += [f"   {e}: {', '.join(fs)}" for e, fs in f["subgrupos"].items()]
    linhas.append("")
    if pendente:
        linhas.append(f"AGUARDANDO DECISÃO: {pendente}")
    else:
        for d in plano:
            linhas.append(f"→ {d['codigo']} {d['nome']}: {len(d['documentos'])} documento(s) — {d['motivo']}")
    return "\n".join(linhas) + "\n"


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
            add(nome, "autoria divergente", "corrigir",
                f"carimbo diz '{(l.lidos_originais or l.valores).get('arquiteto', '')}' — "
                "não é titular nem coautor do fundo; publica com ressalva 'autoria não confirmada'")
        if l.duplicata_de:
            add(nome, "duplicata", "corrigir", f"{l.tipo_duplicata} de {l.duplicata_de} — só a única publica")
        if l.suspeita_grupo:
            add(nome, "projeto errado", "bloqueia",
                f"projeto divergente: o carimbo diz '{_diz(l)}' — fora da publicação até decisão")
        if l.capa:
            add(nome, "metadado", "aviso", f"{l.capa}: não é prancha — fora da publicação")
        if l.sem_identificacao:
            add(nome, "projeto errado", "bloqueia", "sem identificação: pasta com várias obras e a folha não diz "
                "de qual é — fora da publicação até decisão")
        if l.serie_incerta:
            add(nome, "série", "aviso", "série incerta na triagem — "
                f"{(l.triagem or {}).get('motivo', '')}; conferir na folha de contatos")
        if l.fora_do_periodo:
            add(nome, "metadado", "aviso", f"ano {l.ano_do_projeto or l.valores.get('ano', '')} "
                "fora do período de atuação do fundo — conferir autoria/fundo")
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


def execucao(config, leituras_do_lote: list, custo_usd: float, tempo_s: float) -> dict:
    """Versão, modelo, prompts, chamadas, tokens, custo e tempo do lote (ticket 89)."""
    from .config import CONTRATO, versao_completa
    from .visao import VERSAO_PROMPT

    lidas = [l for l in leituras_do_lote if l.tokens_entrada or l.tokens_saida]
    return {"versao_cv2": versao_completa(), "contrato": CONTRATO, "modelo": getattr(config, "modelo", ""),
            "versao_prompt": dict(VERSAO_PROMPT), "chamadas": sum(max(1, l.passes) for l in lidas),
            "tokens_entrada": sum(l.tokens_entrada for l in lidas), "tokens_saida": sum(l.tokens_saida for l in lidas),
            "custo_usd": round(float(custo_usd or 0.0), 4), "tempo_s": round(tempo_s, 1)}


def pacote_tainacan(codigo: str, nome: str, ctx, leituras: dict, preparos: dict, mapa: dict,
                    raiz: Path, catalogacao: Path | None = None, info: dict | None = None,
                    nome_pasta: str = "", execucao_lote: dict | None = None) -> dict:
    """Pacote para o painel/importador (Anexo D.3). Montado em nucleo/projeto.py."""
    from . import projeto as mod_projeto

    return mod_projeto.pacote(codigo, nome, ctx, leituras, preparos, mapa,
                              catalogacao or Path("/nao-existe"), info, nome_pasta, execucao_lote)


def _d(codigo: str) -> str:
    """F002-P0002-1972-S01-D00004 → D4 (como no registro da Paróquia)."""
    achado = re.search(r"-D(\d+)$", codigo)
    return f"D{int(achado.group(1))}" if achado else codigo


def log_orientacao(leituras: dict) -> str:
    """Trilha de auditoria da orientação, folha por folha (procedimento, etapa 5).

    Formato: `90° horário: D2, D8, D10 · sem giro: D5, D6, D7`.
    """
    grupos: dict[str, list[str]] = {}
    for codigo in sorted(leituras):
        l = leituras[codigo]
        giro = {0: "sem giro", 90: "90° horário", 180: "180°", 270: "90° anti-horário"}.get(
            l.rotacao_aplicada % 360, f"{l.rotacao_aplicada}°")
        if l.espelhada:
            giro += " + espelhada"
        grupos.setdefault(giro, []).append(_d(codigo))
    linha = " · ".join(f"{g}: {', '.join(ds)}" for g, ds in grupos.items())
    incertas = [_d(c) for c in sorted(leituras) if leituras[c].orientacao_incerta]
    texto = f"Orientação aplicada (gerado em {_agora()})\n{linha}\n"
    if incertas:
        texto += f"incerta — conferir na folha de contatos: {', '.join(incertas)}\n"
    return texto


FOLHA_DE_N = re.compile(r"(?:^|[^A-Za-z0-9])([A-Z]{1,3})?\s*[-.]?\s*(\d{1,3})\s*/\s*(\d{1,3})\b")


def folhas_faltantes(leituras) -> list[str]:
    """Pelo total escrito na folha (C-3/12, A 2/13, FL 1/6, folha 3 + total 12) — CV-18."""
    vistas: dict[tuple[str, int], set[int]] = {}
    for l in leituras:
        achou = False
        for campo in ("codigo_serie", "folha"):
            m = FOLHA_DE_N.search(" " + (l.valores.get(campo) or ""))
            if m:
                prefixo = (m.group(1) or "").upper()
                prefixo = "" if prefixo in ("FL", "F", "FLS") else prefixo
                vistas.setdefault((prefixo, int(m.group(3))), set()).add(int(m.group(2)))
                achou = True
                break
        if not achou:
            total = (l.valores.get("total_folhas") or "").strip()
            numero = re.search(r"\d+", l.valores.get("folha") or "")
            if re.fullmatch(r"\d{1,3}", total) and numero:
                vistas.setdefault(("", int(total)), set()).add(int(numero.group()))
    saida = []
    for (prefixo, total), numeros in sorted(vistas.items()):
        ausentes = sorted(set(range(1, total + 1)) - numeros)
        if ausentes and total <= 300:
            nomes = [f"{prefixo}-{n}" if prefixo else str(n) for n in ausentes]
            rotulo = f"série {prefixo} de {total}" if prefixo else f"conjunto de {total} folhas"
            saida.append(f"{rotulo}: faltam {', '.join(nomes)}")
    return saida


def relatorio_lote(codigo: str, nome: str, lote: dict, leituras: dict, por_codigo: dict,
                   erros: list[dict], esperado: int | None = None,
                   catalogacao: Path | None = None) -> str:
    """Relatório final do lote, sempre nas 8 partes e nesta ordem (procedimento, etapa 8)."""
    from collections import Counter

    do_lote = {c: leituras[c] for c in por_codigo if c in leituras}
    linhas = [f"Relatório do lote {lote.get('lote_id', '')} — {codigo} {nome}",
              f"origem: {lote.get('origem', '')}", ""]

    def secao(n: int, titulo: str, itens: list[str]) -> None:
        linhas.append(f"{n}. {titulo}")
        linhas.extend(f"   {i}" for i in itens) if itens else linhas.append("   nenhum")
        linhas.append("")

    arquivos = len(lote.get("arquivos", []))
    secao(1, "Arquivos na origem × registros na saída", [
        f"{arquivos + len(lote.get('nao_suportados', []))} arquivo(s) na origem, "
        f"{arquivos} arquivado(s), {len(por_codigo)} documento(s), {len(do_lote)} com leitura"])
    series = Counter(c.split("-")[3] for c in por_codigo if c.count("-") >= 4)
    from .estrutura import SERIES
    secao(2, "Distribuição por série", [f"{s} {SERIES.get(s, '')}: {n}" for s, n in sorted(series.items())])
    secao(3, "Duplicatas (publica só a única)", [
        f"{c} é {l.tipo_duplicata} de {l.duplicata_de} — publicar {l.duplicata_de}"
        for c, l in sorted(do_lote.items()) if l.duplicata_de])
    secao(4, "Autoria divergente (publica com ressalva), projeto divergente, sem identificação e capas (estes não publicam)", [
        *(f"{c}: autoria divergente — carimbo '{(l.lidos_originais or l.valores).get('arquiteto', '')}'"
          for c, l in sorted(do_lote.items()) if l.autoria_divergente),
        *(f"{c}: projeto divergente — o carimbo diz '{_diz(l)}'"
          for c, l in sorted(do_lote.items()) if l.suspeita_grupo),
        *(f"{c}: sem identificação — pasta com várias obras; a folha não diz de qual é"
          for c, l in sorted(do_lote.items()) if l.sem_identificacao),
        *(f"{c}: {l.capa} — não é prancha" for c, l in sorted(do_lote.items()) if l.capa)])
    secao(5, "Precisa de olho humano (orientação e série incertas)", [
        *(f"{c}: orientação incerta" for c, l in sorted(do_lote.items()) if l.orientacao_incerta),
        *(f"{c}: série incerta — {(l.triagem or {}).get('motivo', '')}"
          for c, l in sorted(do_lote.items()) if l.serie_incerta)])
    faltam = []
    if esperado and esperado > len(por_codigo):
        faltam.append(f"info_projeto diz {esperado} documento(s); chegaram {len(por_codigo)}")
    faltam += folhas_faltantes(do_lote.values())
    secao(6, "Folhas esperadas que faltam (pelo total do carimbo)", faltam)
    secao(7, "Campos vazios por decisão e ressalvas", [
        f"{c}: {r}" for c, l in sorted(do_lote.items()) for r in l.ressalvas])
    from .projeto import decisoes as _decisoes

    cat = catalogacao
    retirados = [f"{c} — retirado por {d['por']} em {d['em'][:10]}: {d['motivo']}"
                 for c, d in (_decisoes(cat).items() if cat else []) if d.get("acao") == "retirar"]
    secao(8, "Retirado / não arquivado", retirados + [
        f"{a} — formato não suportado, ficou na entrada" for a in lote.get("nao_suportados", [])])
    bloqueia = [e for e in erros if e.get("gravidade") == "bloqueia"]
    linhas.append(f"Erros: {len(erros)} ({len(bloqueia)} bloqueiam). Detalhe em erros.json.")
    ex = lote.get("execucao") or {}
    if ex:
        linhas.append(f"Execução: CV2 {ex.get('versao_cv2')} · contrato {ex.get('contrato')} · modelo {ex.get('modelo')} · "
                      f"{ex.get('chamadas')} chamada(s) · {ex.get('tokens_entrada')} tokens entrada / "
                      f"{ex.get('tokens_saida')} saída · US$ {ex.get('custo_usd', 0):.4f} · {ex.get('tempo_s')} s · "
                      f"prompts {', '.join(f'{k}:{v}' for k, v in (ex.get('versao_prompt') or {}).items())}")
    return "\n".join(linhas) + "\n"


def checklist_aceite(por_codigo: dict, leituras: dict, erros: list[dict], lote: dict) -> dict:
    """Checklist de aceite do lote (§10). Vai no JSON do lote; o painel decide publicar."""
    do_lote = [leituras[c] for c in por_codigo if c in leituras]
    exif = lote.get("exif", {})
    itens = {
        "contagem_origem_igual_saida": len(do_lote) == len(por_codigo),
        "duplicatas_marcadas": True,
        "orientacao_incerta_zerada": not any(l.orientacao_incerta for l in do_lote),
        "serie_incerta_zerada": not any(l.serie_incerta for l in do_lote),
        "exif_conferido": exif.get("conferidos", 0) == exif.get("total", 0),
        "relatorio_gerado": True,
    }
    itens["publicavel"] = all(itens.values())
    itens["pendencias"] = [k for k, v in itens.items() if v is False]
    # CAMP-046: autoria divergente não bloqueia; é aviso (N folhas com autoria não confirmada)
    itens["folhas_autoria_nao_confirmada"] = sum(1 for l in do_lote if l.autoria_divergente)
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
        self._descartado = ""
        self.painel = painel
        self.livro = livro
        self.pasta_estado = pasta_estado
        self.estado = Estado(pasta_estado)
        self.raiz_entrada = Path(config.pasta_entrada)
        self.raiz_final = Path(config.raiz_final)
        self.tabela = mod_fundos.com_parametros(
            tabela or mod_fundos.carregar(pasta_estado, painel if painel.ligado else None), self.raiz_final)

    # ------------------------------------------------------------ fila
    def pendentes(self) -> list[Unidade]:
        feitos = self.estado.ultimos()
        fila = []
        for unidade in descobrir(self.raiz_entrada):
            ultimo = feitos.get(unidade.chave, {})
            if (ultimo.get("status") in (PRONTO, ERRO, DESCARTADO) and ultimo.get("motivo") != CANCELADO
                    and ultimo.get("assinatura") == unidade.assinatura()):
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
        provisorio = estrutura.e_provisorio(codigo)
        atual.update({
            "codigo": codigo, "status": situacao, "status_em": _agora(),
            "lote_atual": ctx.lote_id, "versao_cv2": VERSAO_BUILD, **extra,
        })
        gravar_json(pasta / "status.json", atual)
        if not provisorio:  # código provisório (P9xxx) nunca vai ao painel
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
    # ------------------------------------------------------------ principal
    def processar(self, unidade: Unidade, estado_vigia=None, cancelar=None) -> Resultado:
        comeco = time.monotonic()
        ok, motivo = pronta(unidade, self.config)
        if not ok:
            return Resultado(AGUARDANDO, motivo)
        assinatura = unidade.assinatura()
        origem_rel = lambda p: str(Path("100 - Scanners") / unidade.relativo / p.relative_to(unidade.pasta))  # noqa: E731

        ctx = contexto(unidade, self.tabela, self.config.serie_padrao)
        if ctx.problemas:
            motivo = "; ".join(ctx.problemas)
            self.livro.anotar("erro", arquivo_origem=str(unidade.relativo), detalhe=motivo)
            self.estado.anotar(unidade.chave, ERRO, motivo=motivo, assinatura=assinatura)
            _log.error("Unidade %s: %s", unidade.relativo, motivo)
            return Resultado(ERRO, motivo)

        # Manifesto da estação (C1): arquivo corrompido/faltando vira erro ANTES de copiar
        # qualquer coisa — nada é apagado da entrada.
        quebrados = conferir_manifesto(unidade)
        if quebrados:
            motivo = "manifesto da estação não confere: " + "; ".join(quebrados[:5]) + (
                f" (+{len(quebrados) - 5})" if len(quebrados) > 5 else "")
            self.livro.anotar("erro", arquivo_origem=str(unidade.relativo), detalhe=motivo)
            self.estado.anotar(unidade.chave, ERRO, motivo=motivo, assinatura=assinatura)
            _log.error("Unidade %s: %s", unidade.relativo, motivo)
            return Resultado(ERRO, motivo)

        # Sem painel não há como numerar projeto novo: espera ANTES de ler (não gasta API).
        pasta_fundo = estrutura.garantir_fundo(self.raiz_final, ctx.fundo)
        if not self.painel.ligado and not estrutura.achar_projeto(pasta_fundo, ctx.codigo, ctx.projeto):
            motivo = "painel não configurado: não há como numerar projeto novo"
            self.estado.anotar(unidade.chave, AGUARDANDO, motivo=motivo)
            _log.warning("Unidade %s aguardando: %s", unidade.relativo, motivo)
            return Resultado(AGUARDANDO, motivo)

        # Auditoria F026 (13/10): o lote é lido ANTES de ter código, numa pasta
        # provisória P9xxx fora de "01 - Projetos". O carimbo decide de que
        # projeto(s) ele é; só então os arquivos vão para o código certo.
        pasta, codigo = estrutura.pasta_provisoria(pasta_fundo, ctx.fundo.codigo, unidade.chave,
                                                   ctx.projeto or unidade.relativo.name)
        nome = ctx.projeto or codigo
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
            # Um lote nunca termina sem catalogação (etapa 8): ou sai o relatório, ou o motivo.
            try:
                (catalogacao / "catalogacao_ERRO.txt").write_text(
                    f"{_agora()}  lote {ctx.lote_id}  {codigo}\n{motivo}\n"
                    f"origem: {lote['origem']}\n", encoding="utf-8")
            except OSError:
                pass
            self._erros(catalogacao, erros_lote)
            self._status(pasta, codigo, ERRO, ctx, motivo=motivo)
            self.livro.anotar("erro", lote_id=ctx.lote_id, codigo_projeto=codigo,
                              arquivo_origem=str(unidade.relativo), detalhe=motivo)
            self.estado.anotar(unidade.chave, ERRO, motivo=motivo, assinatura=assinatura,
                               codigo=codigo)
            _log.error("Lote %s (%s): %s", ctx.lote_id, codigo, motivo)
            resultado.status, resultado.motivo, resultado.lote = ERRO, motivo, lote
            return resultado

        # Livro de registro: o que acontece na pasta provisória é anotado com o código
        # FINAL, depois que o carimbo decide o projeto (a história do documento fica inteira).
        adiado = catalogacao / "livro_adiado.jsonl"

        def anotar_adiado(acao: str, **campos) -> None:
            adiado.parent.mkdir(parents=True, exist_ok=True)
            with adiado.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"acao": acao, **campos}, ensure_ascii=False, default=str) + "\n")

        # Arquivo que JÁ está no acervo (mesmo SHA-256, em qualquer projeto do fundo): não entra de novo.
        no_acervo = indice_do_fundo(pasta_fundo, self.raiz_final)

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
        if ctx.serie_deduzida:
            erros_lote.append({"arquivo": "", "categoria": "metadado", "gravidade": "aviso",
                               "origem": "CAMP Vision",
                               "detalhe": f"sem pasta de material: série {ctx.serie} deduzida "
                                          f"({estrutura.SERIES[ctx.serie]}); corrigir com --reclassificar"})
        # Espaço antes de começar: lote que enche o disco no meio termina sem nada (etapa 8).
        try:
            precisa = sum(a.stat().st_size for a in suportados)
            livre = shutil.disk_usage(pasta).free
        except OSError:
            precisa, livre = 0, 1
        if precisa and livre < precisa * 1.1 + 200 * 1024 * 1024:
            return falhar(f"espaço insuficiente no destino: o lote precisa de {precisa / 2**30:.1f} GB "
                          f"e há {livre / 2**30:.1f} GB livres")
        multiserie = len(ctx.series) > 1
        serie_de = (lambda a: serie_do_arquivo(a, unidade, ctx.serie)) if multiserie else (lambda a: ctx.serie)  # noqa: E731
        documentos = formatos.agrupar(suportados, grupo=serie_de if multiserie else None)
        proximo = estrutura.proximo_documento(pasta)
        arquivados: list[tuple[Path, Path, str, str]] = []  # (origem, destino, código, sha)
        vistos_sha: dict[str, str] = {}
        ja_arquivados: list[tuple[Path, Path, str, str]] = []
        copia = Andamento(ctx.lote_id, "copiando", sum(len(d.versoes) for d in documentos), estado_vigia)
        # Hash do original guardado por (tamanho, mtime): nova tentativa do mesmo lote
        # não relê 1050 TIFF pela rede só para recalcular o que já sabia (Golombeck2, 15/10).
        hashes_caminho = catalogacao / "hashes_origem.json"
        hashes: dict = ler_json(hashes_caminho, {}) or {}

        def sha_origem(arquivo: Path) -> str:
            info = arquivo.stat()
            chave, assinatura_arq = origem_rel(arquivo), [info.st_size, info.st_mtime_ns]
            guardado = hashes.get(chave)
            if isinstance(guardado, list) and len(guardado) == 3 and guardado[:2] == assinatura_arq:
                return guardado[2]
            soma = sha256(arquivo)
            hashes[chave] = [*assinatura_arq, soma]
            return soma

        try:
            for doc in documentos:
                copia.passo(len(doc.versoes))
                shas = {v: sha_origem(v) for v in doc.versoes}
                if all(s in no_acervo for s in shas.values()):
                    ja_arquivados += [(v, no_acervo[sh], no_acervo[sh].stem, sh) for v, sh in shas.items()]
                    continue
                ja = [mapa[s] for s in shas.values() if s in mapa]
                if ja:
                    codigo_doc = Path(ja[0]["destino"]).stem
                else:
                    codigo_doc = estrutura.codigo_documento(codigo, ctx.ano, serie_de(doc.versoes[0]), proximo)
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
                    destino = estrutura.destino_documento(pasta, serie_de(versao), codigo_doc, versao.suffix)
                    destino.parent.mkdir(parents=True, exist_ok=True)
                    if destino.exists() and sha256(destino) != soma:
                        return falhar(f"{destino.name} já existe com outro conteúdo")
                    if not destino.exists():
                        temporario = destino.with_name(f".{destino.name}.cv2tmp")
                        shutil.copy2(versao, temporario)
                        if sha256(temporario) != soma:
                            temporario.unlink(missing_ok=True)
                            return falhar(f"cópia de {versao.name} não confere (hash)")
                        os.replace(temporario, destino)
                        anotar_adiado(
                            "copiado", lote_id=ctx.lote_id, codigo_projeto=codigo,
                            codigo_documento=codigo_doc, arquivo_origem=origem_rel(versao),
                            arquivo_destino=self._relativo(destino), nome_original=versao.name,
                            tamanho=destino.stat().st_size, sha256=soma,
                            operador=ctx.operador, estacao=ctx.estacao)
                        resultado.copiados += 1
                    mapa[soma] = {"destino": self._relativo(destino), "origem": origem_rel(versao),
                                  "nome_original": versao.name, "lote_id": ctx.lote_id,
                                  "origem_formato": formatos.origem_formato(versao)}
                    arquivados.append((versao, destino, codigo_doc, soma))
                    lote["arquivos"].append({"origem": origem_rel(versao), "nome_original": versao.name,
                                             "destino": self._relativo(destino), "codigo": codigo_doc,
                                             "tipo": versao.suffix.lower().lstrip("."), "sha256": soma})
            gravar_json(mapa_caminho, mapa)
            gravar_json(hashes_caminho, hashes)
        except OSError as erro:
            gravar_json(mapa_caminho, mapa)
            gravar_json(hashes_caminho, hashes)
            return falhar(f"falha de disco/rede ao copiar: {erro}")

        if cancelar is not None and cancelar.is_set():
            return falhar(CANCELADO)
        if ja_arquivados:
            # Já estavam no acervo (lote cortado antes do fim, reenvio): confere o EXIF e
            # regrava SÓ o que faltar — nunca entrega arquivo sem metadado (CAMP-010).
            faltou = self._garantir_exif(ja_arquivados, ctx, estado_vigia)
            if faltou:
                return falhar(f"EXIF conferido em {faltou[0]} de {faltou[1]} arquivo(s) já no acervo"
                              + (f": {faltou[2]}" if faltou[2] else ""))
        if not arquivados:  # tudo o que veio já estava no acervo: nada a ler nem a decidir
            from . import transferir as mod_transferir

            mod_transferir.limpar_provisoria(pasta)
            proj = "-".join(ja_arquivados[0][2].split("-")[:2]) if ja_arquivados else ""
            if self.config.apagar_original_apos_pronto and ja_arquivados:
                resultado.apagados = self._apagar_originais(unidade, ja_arquivados, ctx, proj,
                                                            sobrou=bool(lote["nao_suportados"]))
            self.estado.anotar(unidade.chave, PRONTO, assinatura=assinatura, codigo=proj, lote_id=ctx.lote_id)
            _log.info("Lote %s: os %d arquivo(s) já estavam no acervo (%s).", ctx.lote_id, len(ja_arquivados), proj)
            resultado.status, resultado.codigo, resultado.copiados = PRONTO, proj, 0
            resultado.pasta_projeto = ja_arquivados[0][1].parents[2] if ja_arquivados else pasta
            return resultado

        from . import renomear as mod_renomear

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
        preparo = Andamento(ctx.lote_id, "preparando", len(por_codigo), estado_vigia)
        nome_na_entrada = {destino: origem_rel(o) for o, destino, _c, _s in arquivados}
        corrompidos: list[str] = []
        for codigo_doc, versoes in por_codigo.items():
            preparo.passo()
            leitura_jpg = cache / f"{codigo_doc}.jpg"
            if codigo_doc in preparos and leitura_jpg.exists():
                continue
            melhor = formatos.Documento(codigo_doc, versoes).para_ler
            try:
                bruta = formatos.imagem_de_leitura(melhor, bruta_dir / f"{codigo_doc}.jpg")
                prep = mod_preparo.preparar(bruta, melhor, leitura_jpg, preview_dir / f"{codigo_doc}.jpg") \
                    if bruta is not None else None
            except (OSError, ValueError, SyntaxError) as erro:
                if isinstance(erro, OSError) and erro.errno is not None:
                    raise  # disco/rede de verdade: o vigia interrompe a rodada e tenta depois
                # Imagem quebrada ("broken data stream…"): antes subia como OSError, o vigia
                # achava que era o QNAP e recomeçava o lote para sempre (Golombeck2, 15/10).
                _log.error("Lote %s: %s não abre (%s)", ctx.lote_id, nome_na_entrada.get(melhor, melhor.name), erro)
                bruta, prep = None, None
                detalhe_erro = f": {erro}"
            else:
                detalhe_erro = ""
            if bruta is None or prep is None:
                origem = nome_na_entrada.get(melhor, melhor.name)
                corrompidos.append(origem)
                erros_lote.append({"arquivo": codigo_doc, "categoria": "arquivo corrompido",
                                   "gravidade": "bloqueia", "origem": "CAMP Vision",
                                   "detalhe": f"não abri {origem} para leitura{detalhe_erro}"})
                continue
            preparos[codigo_doc] = {
                "md5": prep.md5, "hash_perceptual": prep.hash_perceptual,
                "rotacao": prep.orientacao.rotacao, "espelhada": prep.orientacao.espelhada,
                "incerta": prep.orientacao.incerta, "pontos": prep.orientacao.pontos,
                "lido_de": melhor.name, "origem_formato": formatos.origem_formato(melhor),
                "tamanho": max((v.stat().st_size for v in versoes if v.exists()), default=0),
                "preview": self._relativo(prep.preview) if prep.preview else "",
            }
            bruta.unlink(missing_ok=True)
        gravar_json(preparo_caminho, preparos)
        if corrompidos:
            # Para antes de gastar API. Fica em erro (não volta sozinho) até o arquivo
            # ser trocado ou tirado da entrada; o que já foi preparado não é refeito.
            lista = ", ".join(corrompidos[:5]) + (f" e mais {len(corrompidos) - 5}" if len(corrompidos) > 5 else "")
            return falhar(f"{len(corrompidos)} arquivo(s) corrompido(s), não abrem: {lista} — "
                          "troque ou tire da entrada e use --refazer-lote")

        # JPG do acervo na orientação certa (giro/espelho decidido com certeza pelo
        # texto); o master TIF/DNG/PDF nunca é tocado. Antes do EXIF, que vem depois.
        from . import derivados as mod_derivados

        for codigo_doc, versoes in por_codigo.items():
            prep = preparos.get(codigo_doc)
            for versao in versoes:
                if versao.suffix.lower() in (".jpg", ".jpeg") and prep and mod_derivados.orientar_jpg(versao, prep):
                    prep["jpg_orientado"] = True
                    anotar_adiado("refeito", lote_id=ctx.lote_id, codigo_projeto=codigo,
                                      codigo_documento=codigo_doc, arquivo_destino=self._relativo(versao),
                                      detalhe=f"JPG girado {prep.get('rotacao', 0)}°"
                                      + (" e desespelhado" if prep.get("espelhada") else ""))
        gravar_json(preparo_caminho, preparos)

        def trocar_serie(trocas: dict[str, str], motivo: str):
            """Renomeia (arquivos, catalogação, cache, preview) e recarrega o estado do lote."""
            gravar_json(catalogacao / "lotes" / f"{ctx.lote_id}.json", lote)
            movidos = mod_renomear.renomear(pasta, self.raiz_final, self.pasta_estado, trocas,
                                            self.livro, motivo)
            novo_lote = ler_json(catalogacao / "lotes" / f"{ctx.lote_id}.json", lote)
            return ([(o, movidos.get(d, d), trocas.get(c, c), sh) for o, d, c, sh in arquivados],
                    {trocas.get(c, c): [movidos.get(v, v) for v in vs] for c, vs in por_codigo.items()},
                    ler_json(mapa_caminho, {}) or {}, ler_json(preparo_caminho, {}) or {},
                    novo_lote, novo_lote.setdefault("erros", erros_lote))

        # Triagem por série (procedimento, etapa 2): só quando a estação não disse a
        # série (sem pasta de material). Carta não é lida com prompt de prancha.
        if ctx.serie_deduzida:
            trocas_serie: dict[str, str] = {}
            for codigo_doc in list(por_codigo):
                prep = preparos.get(codigo_doc)
                if not prep or prep.get("triagem"):
                    continue
                tri = mod_triagem.triar(cache / f"{codigo_doc}.jpg", prep.get("pontos"), ctx.serie)
                prep["triagem"] = tri.como_dict()
                atual = codigo_doc.split("-")[3] if codigo_doc.count("-") >= 4 else ctx.serie
                if tri.serie != atual:
                    trocas_serie[codigo_doc] = mod_renomear.novo_codigo(codigo_doc, serie=tri.serie)
            gravar_json(preparo_caminho, preparos)
            if trocas_serie:
                arquivados, por_codigo, mapa, preparos, lote, erros_lote = trocar_serie(
                    trocas_serie, "série decidida na triagem")

        from . import planilha as mod_planilha, vigia as mod_vigia

        cfg_leitura = dataclasses.replace(
            self.config, status_concluido=PROCESSANDO, status_pronto=PROCESSANDO,
            espera_estabilidade_segundos=0, escrever_por_projeto=True, formatos_saida=("csv",),
            usar_pasta_como_pista=False,  # a pasta é pista do PROJETO, nunca do que a folha diz
        )
        projeto = mod_vigia.Projeto(pasta, [cache], pasta.name, self.raiz_final)
        evento = mod_vigia.processar(projeto, cfg_leitura, self.cliente, estado_vigia, cancelar)
        lote["custo_usd"] = round(evento.custo_usd, 4)
        if evento.falha:
            return falhar(evento.falha if evento.falha.startswith("limite de custo")
                          else f"leitura falhou: {evento.falha}")
        leituras = {Path(l.arquivo).stem: l for l in mod_planilha.ler_json(catalogacao / "leituras.json")}
        # A leitura de prancha viu que era FOTO (a triagem achou que era papel) e já
        # releu como foto: a série acompanha. Só quando a estação não disse a série.
        if ctx.serie_deduzida:
            from .visao import SERIES_FOTO

            trocas_foto = {c: mod_renomear.novo_codigo(c, serie="S03") for c, l in leituras.items()
                           if c in por_codigo and l.modo == "fotografia" and c.count("-") >= 4
                           and c.split("-")[3] not in SERIES_FOTO}
            if trocas_foto:
                arquivados, por_codigo, mapa, preparos, lote, erros_lote = trocar_serie(
                    trocas_foto, "a leitura viu que é fotografia")
                leituras = {Path(l.arquivo).stem: l for l in mod_planilha.ler_json(catalogacao / "leituras.json")}
        # Foto sem texto: o OCR não decide a orientação; a leitura da foto decide.
        girou_foto = False
        for codigo_doc, versoes in por_codigo.items():
            l = leituras.get(codigo_doc)
            if l is not None and (l.foto or l.orientacao_modelo) and mod_derivados.orientar_pela_foto(
                    preparos.get(codigo_doc), l.foto or l.orientacao_modelo, versoes,
                    [cache / f"{codigo_doc}.jpg", preview_dir / f"{codigo_doc}.jpg"]):
                girou_foto = True
                prep = preparos[codigo_doc]
                if prep.get("rotacao"):
                    anotar_adiado("refeito", lote_id=ctx.lote_id, codigo_projeto=codigo,
                                  codigo_documento=codigo_doc,
                                  detalhe=f"JPG girado {prep['rotacao']}° (leitura da fotografia)")
        if girou_foto:
            gravar_json(preparo_caminho, preparos)
        # Leitura que não aconteceu (chave inválida, sem crédito, rede) NÃO pode
        # virar "pronto": o painel receberia pranchas sem catalogação e o
        # original seria apagado. Fica em erro; --refazer-lote relê só essas.
        falhas = [c for c in por_codigo if c not in leituras or leituras[c].erro]
        if falhas:
            exemplo = leituras[falhas[0]].erro if falhas[0] in leituras else "sem leitura"
            return falhar(f"leitura falhou em {len(falhas)} de {len(por_codigo)} documento(s): {exemplo[:160]}")
        for codigo_doc in por_codigo:
            if codigo_doc in leituras:
                anotar_adiado("lido", lote_id=ctx.lote_id, codigo_projeto=codigo,
                                  codigo_documento=codigo_doc,
                                  detalhe="carimbo lido" if leituras[codigo_doc].carimbo_encontrado
                                  else "sem carimbo")


        # ---- 5b. identificação pelo CARIMBO (T2/T3/T6): grupos, capas, base do fundo
        from . import identidade as mod_ident, transferir as mod_transferir

        enriquecer(leituras, preparos, ctx.fundo)
        do_lote = {c: leituras[c] for c in por_codigo if c in leituras}
        base = ((self.painel.contexto(ctx.fundo.codigo) or {}).get("projetos") or []) if self.painel.ligado else []
        analise = mod_ident.analisar(do_lote, {c: _proporcao(cache / f"{c}.jpg") for c in do_lote}, base)
        self._descartado = ""
        plano, pendente = self._planejar(analise, ctx, unidade, do_lote, base)
        if self._descartado:
            return self._descartar(unidade, ctx, pasta, codigo, assinatura, self._descartado, resultado, lote)
        marcar_identidade(preparos, analise, plano, do_lote)
        gravar_json(preparo_caminho, preparos)
        gravar_json(catalogacao / "identificacao.json", {
            "lote_id": ctx.lote_id, "unidade": str(unidade.relativo), "em": _agora(),
            "analise": analise.como_dict(), "plano": plano, "pendente": pendente})
        (catalogacao / "identificacao.txt").write_text(
            relatorio_identificacao(analise, plano, pendente, do_lote, str(unidade.relativo)), encoding="utf-8")
        lote["identificacao"] = {"grupos": len(analise.grupos), "capas": len(analise.capas),
                                 "sem_identificacao": len(analise.sem_identificacao),
                                 "destinos": [d["codigo"] for d in plano], "pendente": pendente}
        gravar_json(catalogacao / "lotes" / f"{ctx.lote_id}.json", lote)
        if pendente:
            self._status(pasta, codigo, AGUARDANDO, ctx, motivo=pendente)
            self.estado.anotar(unidade.chave, AGUARDANDO, motivo=pendente, codigo=codigo)
            _log.warning("Lote %s aguardando decisão: %s", ctx.lote_id, pendente)
            resultado.status, resultado.motivo, resultado.lote = AGUARDANDO, pendente, lote
            return resultado

        # ---- 5c. cada grupo para o projeto decidido (move, renumera o D; não relê)
        total_docs = max(1, len(por_codigo))
        prontos: list[Resultado] = []
        finais: list[tuple] = []
        for destino in plano:
            pasta_d = estrutura.garantir_projeto(pasta_fundo, destino["codigo"], destino["nome"], ctx.fundo)
            ja_tinha = set(ler_json(pasta_d / "catalogacao" / "mapa_origem.json", {}) or {})
            try:
                trocas, movidos = mod_transferir.transferir(
                    self.raiz_final, self.pasta_estado, pasta, pasta_d, destino["documentos"], destino["codigo"],
                    self.livro, destino["motivo"])
            except OSError as erro:
                return falhar(f"não consegui levar o lote para {destino['codigo']}: {erro}")
            arq_d = [(o, movidos.get(d, d), trocas[c], sh) for o, d, c, sh in arquivados if c in trocas]
            self._livro_adiado(adiado, destino["codigo"], trocas, movidos)
            finais += arq_d
            lote_d = {**lote, "codigo": destino["codigo"], "erros": [dict(e) for e in erros_lote],
                      "custo_usd": round(lote.get("custo_usd", 0.0) * len(destino["documentos"]) / total_docs, 4),
                      "arquivos": [{**a, "codigo": trocas[a["codigo"]],
                                    "destino": self._relativo(movidos.get(self.raiz_final / a["destino"],
                                                                          self.raiz_final / a["destino"]))}
                                   for a in lote.get("arquivos", []) if a.get("codigo") in trocas],
                      "identificacao": {**lote["identificacao"], "grupo": destino.get("grupo", ""),
                                        "motivo": destino["motivo"]}}
            ctx_d = dataclasses.replace(ctx, projeto=destino["nome"] if len(plano) > 1 else ctx.projeto)
            r = self._concluir(unidade, ctx_d, pasta_d, destino["codigo"], destino["nome"], lote_d, arq_d,
                               comeco, estado_vigia, cancelar,
                               copiados=len({sh for *_x, sh in arq_d if sh not in ja_tinha}))
            if r.status != PRONTO:
                return r
            prontos.append(r)
        mod_transferir.limpar_provisoria(
            pasta, guardar_em=prontos[0].pasta_projeto / "catalogacao" / "identificacao" / ctx.lote_id if prontos else None)
        for vazia in (self.raiz_final / "_campvision" / "preview" / codigo, cache,
                      self.pasta_estado / "leitura_bruta" / codigo):
            try:
                vazia.rmdir()
            except OSError:
                pass

        # ---- 9. apaga o original (só o que foi arquivado e conferido)
        codigos = [r.codigo for r in prontos]
        resultado.codigo = codigos[0] if codigos else codigo
        resultado.pasta_projeto = prontos[0].pasta_projeto if prontos else pasta
        finais += ja_arquivados
        if self.config.apagar_original_apos_pronto:
            resultado.apagados = self._apagar_originais(unidade, finais, ctx, resultado.codigo,
                                                        sobrou=bool(lote["nao_suportados"]))
        self.estado.anotar(unidade.chave, PRONTO, assinatura=assinatura, codigo=resultado.codigo,
                           codigos=codigos, lote_id=ctx.lote_id)
        _log.info("Lote %s → %s pronto: %d documento(s), %d copiado(s), %d apagado(s).",
                  ctx.lote_id, ", ".join(codigos), len(por_codigo), resultado.copiados, resultado.apagados)
        resultado.status, resultado.lote = PRONTO, (prontos[0].lote if len(prontos) == 1 else
                                                    {**lote, "status": PRONTO, "destinos": codigos})
        return resultado

    def _livro_adiado(self, adiado: Path, codigo_destino: str, trocas: dict, movidos: dict) -> None:
        """Escreve no livro, com o código e o caminho FINAIS, o que foi feito na pasta provisória."""
        if not adiado.exists():
            return
        caminhos = {self._relativo(a): self._relativo(b) for a, b in movidos.items()}
        ficam = []
        for linha in adiado.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(linha)
            except ValueError:
                continue
            doc = item.get("codigo_documento", "")
            if doc not in trocas:
                ficam.append(linha)
                continue
            item.update(codigo_documento=trocas[doc], codigo_projeto=codigo_destino)
            if item.get("arquivo_destino") in caminhos:
                item["arquivo_destino"] = caminhos[item["arquivo_destino"]]
            acao = item.pop("acao")
            self.livro.anotar(acao, **item)
        adiado.write_text("".join(f"{l}\n" for l in ficam), encoding="utf-8")

    # ------------------------------------------------------------ plano
    def _planejar(self, analise, ctx: Contexto, unidade: Unidade, leituras: dict,
                  base: list | None = None) -> tuple[list[dict], str]:
        """Para cada grupo, o projeto: base do fundo (um candidato forte), decisão no painel
        (dois ou mais), ou reserva nova (nenhum). Devolve (plano, '') ou ([], motivo de espera)."""
        from . import identidade as mod_ident

        if base is None:
            base = ((self.painel.contexto(ctx.fundo.codigo) or {}).get("projetos") or []) if self.painel.ligado else []
        pista = mod_ident.pista_da_pasta(ctx.projeto or unidade.relativo.name)
        grupos = list(analise.grupos)
        varios = len(grupos) > 1
        # Grupo de 1 folha, numa pasta com outras obras, sem projeto forte na base: não vira
        # projeto sozinho; fica em "sem identificação" (e não é encaixado à força no maior).
        sobras: list[str] = list(analise.sem_identificacao)
        casamentos = {}
        for g in list(grupos):
            casamentos[g.chave] = mod_ident.casar_com_base(g, base, pista if not varios else "")
            if varios and len(g.folhas) < mod_ident.MINIMO_GRUPO and "forte" not in casamentos[g.chave]:
                grupos.remove(g)
                sobras += g.folhas
        if not grupos:  # nada legível no lote: a pasta é a única pista (como antes)
            g = mod_ident.Grupo(obra=pista, folhas=[])
            grupos = [g]
            casamentos[g.chave] = mod_ident.casar_com_base(g, base, "")
        perguntar = {i for juntos in analise.mesmo_endereco for i in juntos}
        perguntar |= {i for i, g in enumerate(analise.grupos) if any(f["grupo"] == g.chave for f in analise.fases)}
        perguntar_chaves = {analise.grupos[i].chave for i in perguntar if i < len(analise.grupos)}

        plano: list[dict] = []
        esperando: list[str] = []
        for g in grupos:
            casa = casamentos[g.chave]
            titulo = g.titulo if g.folhas else (ctx.projeto or pista)
            forte = casa.get("forte")
            candidatos = casa.get("candidatos") or ([forte] if forte else [])
            # Código oficial explícito (manifesto/pasta F0xx-P0xxx, Mac): vale para todos os grupos, a não
            # ser que o carimbo aponte com força OUTRO projeto da base — aí pergunta. Antes valia só com um
            # grupo, e um erro de leitura ("WIZ SALES" × "LUIZ SALES") virava decisão no painel (P0092, 19/10).
            if ctx.codigo:
                if forte and forte["codigo"].upper() != ctx.codigo.upper():
                    candidatos = [{"codigo": ctx.codigo, "titulo": "código informado pela estação",
                                   "motivos": ["código no manifesto/pasta"]}, forte]
                    forte = None
                else:
                    plano.append(self._destino(ctx.codigo, g, ctx, "código oficial informado pela estação"))
                    continue
            if forte and g.chave not in perguntar_chaves:
                nome = forte["titulo"]
                plano.append(self._destino(forte["codigo"], g, ctx, "carimbo casa com o projeto da base: "
                                           + "; ".join(forte.get("motivos") or []), nome=nome))
                continue
            sem_painel = not self.painel.ligado
            if sem_painel:  # só acontece com projeto já no acervo (ver processar)
                existente = estrutura.achar_projeto(estrutura.garantir_fundo(self.raiz_final, ctx.fundo),
                                                    ctx.codigo, ctx.projeto)
                plano.append(self._destino(estrutura.codigo_da_pasta(existente), g, ctx, "projeto já no acervo",
                                           nome=estrutura.nome_sem_codigo(existente.name)))
                continue
            chave_nome = ctx.projeto if not varios else f"{ctx.projeto}|{g.chave}"
            chave = self.estado.chave_reserva(ctx.fundo.codigo, chave_nome)
            maior, usados = estrutura.numeros_p(self.raiz_final, ctx.fundo.codigo)
            extra = {}
            if candidatos and (len(candidatos) > 1 or g.chave in perguntar_chaves):
                extra["candidatos"] = [{"codigo": c["codigo"], "titulo": c.get("titulo", ""),
                                        "motivos": c.get("motivos") or []} for c in candidatos]
            codigo = self.painel.reservar(
                ctx.fundo.codigo, titulo, chave, ano=ctx.ano, cidade=ctx.cidade,
                identificacao_original=(f"{ctx.p_estacao} - {ctx.projeto}" if ctx.p_estacao else ctx.projeto),
                p_estacao=ctx.p_estacao, operador=ctx.operador, proximo_p_local=f"{ctx.fundo.codigo}-P{maior + 1:04d}",
                carimbo={"obra": g.obra, "local": g.local, "cliente": g.cliente,
                         "identificadores": sorted(g.ids), "folhas": len(g.folhas)},
                **extra) or ""
            if not codigo:
                pend = getattr(self.painel, "ultima_reserva", None) or {}
                if pend.get("descartado"):
                    self._descartado = str(pend.get("mensagem") or "lote descartado")
                    return [], self._descartado
                if extra.get("candidatos"):
                    lista = " × ".join(c["codigo"] for c in extra["candidatos"])
                    esperando.append(f"'{titulo}': qual projeto? ({lista}) — decisão no painel")
                else:
                    esperando.append(f"'{titulo}': {pend.get('mensagem') or self.painel.ultimo_erro or 'painel não reservou'}")
                continue
            existente = bool((getattr(self.painel, "ultima_reserva", None) or {}).get("existente"))
            ja_era_nosso = (ler_json(self.estado.reservas, {}) or {}).get(
                f"{ctx.fundo.codigo}|{estrutura.normalizar(chave_nome)}", {}).get("codigo", "").upper() == codigo.upper()
            if codigo.upper() in usados and not existente and not ja_era_nosso and not any(
                    c["codigo"].upper() == codigo.upper() for c in candidatos):
                esperando.append(f"painel devolveu {codigo}, que já é outro projeto no acervo "
                                 f"(maior P local: P{maior:04d}) — corrigir a numeração no painel")
                continue
            self.estado.guardar_codigo(ctx.fundo.codigo, chave_nome, codigo)
            base_titulo = next((str(p.get("projeto") or "") for p in base if str(p.get("codigo")) == codigo), "")
            plano.append(self._destino(codigo, g, ctx, "decidido no painel" if (existente or extra) else
                                       "projeto novo reservado no painel", nome=base_titulo or titulo))
        if esperando:
            return [], "; ".join(esperando)
        # Juntar destinos iguais (dois grupos que o painel mandou para o mesmo projeto).
        juntos: dict[str, dict] = {}
        for d in plano:
            if d["codigo"] in juntos:
                juntos[d["codigo"]]["documentos"] += d["documentos"]
                juntos[d["codigo"]]["motivo"] += f"; {d['motivo']}"
            else:
                juntos[d["codigo"]] = d
        plano = list(juntos.values())
        # Capas e folhas sem identificação: com o projeto principal (o maior), fora da publicação.
        principal = max(plano, key=lambda d: len(d["documentos"]))
        principal["documentos"] += [c for c in [*analise.capas, *sobras] if c not in principal["documentos"]]
        principal["documentos"] = sorted(set(principal["documentos"]))
        # Lote sem nada legível: tudo vai para o único destino.
        resto = [c for c in leituras if not any(c in d["documentos"] for d in plano)]
        principal["documentos"] = sorted(set(principal["documentos"]) | set(resto))
        return plano, ""

    @staticmethod
    def _destino(codigo: str, grupo, ctx: Contexto, motivo: str, nome: str = "") -> dict:
        return {"codigo": codigo.upper(), "nome": nome or grupo.titulo if grupo.folhas else (nome or ctx.projeto or codigo),
                "documentos": list(grupo.folhas), "folhas_grupo": list(grupo.folhas), "grupo": grupo.titulo,
                "motivo": motivo}

    def _descartar(self, unidade: Unidade, ctx: Contexto, pasta: Path, codigo: str, assinatura: str,
                   mensagem: str, resultado: Resultado, lote: dict) -> Resultado:
        """410 do painel (C7): marca o lote como descartado e para de chamar. Não apaga a origem."""
        motivo = f"descartado no painel: {mensagem}"
        lote["status"] = DESCARTADO
        gravar_json(pasta / "catalogacao" / "lotes" / f"{ctx.lote_id}.json", lote)
        self._status(pasta, codigo, ERRO, ctx, motivo=motivo)
        self.livro.anotar("descartado", lote_id=ctx.lote_id, codigo_projeto=codigo,
                          arquivo_origem=str(Path("100 - Scanners") / unidade.relativo), detalhe=motivo)
        self.estado.anotar(unidade.chave, DESCARTADO, motivo=motivo, assinatura=assinatura, codigo=codigo)
        _log.warning("Lote %s descartado pelo painel (410): %s — a origem em 100 - Scanners ficou como está.",
                     ctx.lote_id, mensagem)
        resultado.status, resultado.motivo, resultado.lote = DESCARTADO, motivo, lote
        return resultado

    # ------------------------------------------------------------ conclusão (por projeto)
    def _concluir(self, unidade: Unidade, ctx: Contexto, pasta: Path, codigo: str, nome: str, lote: dict,
                  arquivados: list, comeco: float, estado_vigia=None, cancelar=None,
                  copiados: int = 0) -> Resultado:
        """O projeto decidido: consolidação (sem API: as leituras vieram no checkpoint), EXIF,
        catalogação, pronto e aviso ao painel."""
        from . import planilha as mod_planilha, renomear as mod_renomear, vigia as mod_vigia

        assinatura = unidade.assinatura()
        resultado = Resultado(PROCESSANDO, codigo=codigo, pasta_projeto=pasta)
        resultado.copiados = copiados
        catalogacao = pasta / "catalogacao"
        erros_lote = lote["erros"]
        mapa_caminho = catalogacao / "mapa_origem.json"
        preparo_caminho = catalogacao / "preparo.json"
        mapa = ler_json(mapa_caminho, {}) or {}
        preparos = ler_json(preparo_caminho, {}) or {}
        cache = self.pasta_estado / "leitura" / codigo
        por_codigo: dict[str, list[Path]] = {}
        for _o, destino, codigo_doc, _s in arquivados:
            por_codigo.setdefault(codigo_doc, []).append(destino)
        gravar_json(catalogacao / "lotes" / f"{ctx.lote_id}.json", lote)
        self._status(pasta, codigo, PROCESSANDO, ctx)

        def falhar(motivo: str) -> Resultado:
            erros_lote.append({"arquivo": "", "categoria": "metadado", "gravidade": "bloqueia",
                               "origem": "CAMP Vision", "detalhe": motivo})
            lote["status"] = ERRO
            gravar_json(catalogacao / "lotes" / f"{ctx.lote_id}.json", lote)
            try:
                (catalogacao / "catalogacao_ERRO.txt").write_text(
                    f"{_agora()}  lote {ctx.lote_id}  {codigo}\n{motivo}\n"
                    f"origem: {lote['origem']}\n", encoding="utf-8")
            except OSError:
                pass
            self._erros(catalogacao, erros_lote)
            self._status(pasta, codigo, ERRO, ctx, motivo=motivo)
            self.livro.anotar("erro", lote_id=ctx.lote_id, codigo_projeto=codigo,
                              arquivo_origem=str(unidade.relativo), detalhe=motivo)
            self.estado.anotar(unidade.chave, ERRO, motivo=motivo, assinatura=assinatura, codigo=codigo)
            _log.error("Lote %s (%s): %s", ctx.lote_id, codigo, motivo)
            resultado.status, resultado.motivo, resultado.lote = ERRO, motivo, lote
            return resultado

        # Consolidação do projeto inteiro (o que já estava + este lote), sem chamar a API.
        cfg_leitura = dataclasses.replace(
            self.config, status_concluido=PROCESSANDO, status_pronto=PROCESSANDO,
            espera_estabilidade_segundos=0, escrever_por_projeto=True, formatos_saida=("csv",),
        )
        projeto = mod_vigia.Projeto(pasta, [cache], pasta.name, self.raiz_final)
        evento = mod_vigia.processar(projeto, cfg_leitura, self.cliente, estado_vigia, cancelar)
        if evento.falha:
            return falhar(f"leitura falhou: {evento.falha}")
        leituras = {Path(l.arquivo).stem: l for l in mod_planilha.ler_json(catalogacao / "leituras.json")}
        falhas = [c for c in por_codigo if c not in leituras or leituras[c].erro]
        if falhas:
            exemplo = leituras[falhas[0]].erro if falhas[0] in leituras else "sem leitura"
            return falhar(f"leitura falhou em {len(falhas)} de {len(por_codigo)} documento(s): {exemplo[:160]}")

        # Passada 3 complementos (§2.3, §4.4) — em código, sobre o projeto inteiro.
        enriquecer(leituras, preparos, ctx.fundo)
        from . import projeto as mod_projeto

        mod_projeto.melhor_versao(leituras, preparos)
        for aviso in mod_projeto.congelar_canonicas(leituras, ctx.fundo.codigo, self.pasta_estado):
            _log.warning(aviso)
        origem_doc: dict[str, list[str]] = {}
        for item in mapa.values():
            origem_doc.setdefault(Path(item["destino"]).stem, []).append(item.get("origem", ""))
        for c, l in leituras.items():
            l.arquivo_origem = " | ".join(sorted(origem_doc.get(c, [])))
            l.titulo_publicacao = mod_projeto.titulo_publicacao(l, nome, str(unidade.relativo.name))
        mod_planilha.escrever_json(list(leituras.values()), catalogacao / "leituras.json")
        mod_planilha.escrever_csv(list(leituras.values()), catalogacao / "catalogacao.csv")

        # Ano 0000 no código porque a pasta não dizia; a leitura achou o ano do
        # projeto (moda das datas escritas, §4.2) → renomeia o projeto inteiro.
        from collections import Counter

        anos = Counter(l.ano_do_projeto for l in leituras.values() if l.ano_do_projeto)
        if not anos:  # sem consolidação: a mesma regra (moda confirmada) sobre o ano lido
            anos = Counter(l.valores.get("ano") for l in leituras.values()
                           if re.fullmatch(r"\d{4}", l.valores.get("ano") or ""))
        ano_grupo = ""
        if anos:
            valor, vezes = anos.most_common(1)[0]
            if vezes >= 2 or len(anos) == 1:
                ano_grupo = valor
        codigos = {Path(v["destino"]).stem for v in mapa.values()}
        # CV-14: título que descreve documento, numa pasta sem série declarada, vai para S02.
        documentos_s02 = {c for c, l in leituras.items()
                          if ctx.serie_deduzida and l.e_documento and "-S01-" in c}
        trocas = {c: mod_renomear.novo_codigo(c, ano=ano_grupo if "-0000-" in c and ano_grupo else None,
                                              serie="S02" if c in documentos_s02 else None)
                  for c in codigos if ("-0000-" in c and ano_grupo) or c in documentos_s02}
        trocas = {a: b for a, b in trocas.items() if a != b}
        if trocas:
            gravar_json(catalogacao / "lotes" / f"{ctx.lote_id}.json", lote)  # o renomear reescreve
            movidos = mod_renomear.renomear(
                pasta, self.raiz_final, self.pasta_estado, trocas, self.livro,
                "; ".join(m for m in (f"ano do projeto {ano_grupo} lido nas pranchas" if ano_grupo else "",
                                      "documento textual pelo título (CV-14)" if documentos_s02 else "") if m))
            arquivados = [(o, movidos.get(d, d), trocas.get(c, c), sh) for o, d, c, sh in arquivados]
            por_codigo = {trocas.get(c, c): [movidos.get(v, v) for v in vs] for c, vs in por_codigo.items()}
            mapa = ler_json(mapa_caminho, {}) or {}
            preparos = ler_json(preparo_caminho, {}) or {}
            leituras = {Path(l.arquivo).stem: l for l in mod_planilha.ler_json(catalogacao / "leituras.json")}
            lote = ler_json(catalogacao / "lotes" / f"{ctx.lote_id}.json", lote)

        # ---- 6. EXIF/XMP completo em todas as versões
        itens = []
        ja_vistos: set[Path] = set()
        for origem, destino, codigo_doc, _s in arquivados:
            if destino in ja_vistos or destino.suffix.lower() in formatos.SEM_EXIF:
                continue
            ja_vistos.add(destino)
            itens.append((destino, campos_do_exif(ctx, codigo_doc, leituras.get(codigo_doc), nome, origem.name)))
        # Retomada de um lote cortado no EXIF: o que já está certo não é regravado.
        retomada = (catalogacao / EXIF_PENDENTE).exists()
        ja_certos = conferir_exif([(d, Path(d).stem) for d, _ in itens]) if retomada else set()
        if ja_certos:
            _log.info("Lote %s: retomando o EXIF — %d de %d arquivo(s) já estão certos.",
                      ctx.lote_id, len(ja_certos), len(itens))
        # Passo marcado ANTES de gravar: lote cortado aqui volta pelo EXIF, sem API (CAMP-010).
        marcar_exif_pendente(catalogacao, ctx.lote_id, [d for d, _ in itens], self.raiz_final)
        self.estado.anotar(unidade.chave, PASSO_EXIF, assinatura=assinatura, codigo=codigo, lote_id=ctx.lote_id)
        _log.info("Lote %s: gravando metadados em %d arquivo(s)…", ctx.lote_id, len(itens))
        if estado_vigia is not None:
            estado_vigia.situacao = f"gravando metadados ({len(itens)})"
        gravados, avisos = mod_metadados.gravar_em_lote(
            [(d, c) for d, c in itens if d not in ja_certos], self.config.identidade())
        gravados += len(ja_certos)
        _log.info("Lote %s: conferindo metadados…", ctx.lote_id)
        conferidos = conferir_exif([(d, Path(d).stem) for d, _ in itens])
        if len(conferidos) >= len(itens):
            limpar_exif_pendente(catalogacao)
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
        (catalogacao / "orientacao.txt").write_text(log_orientacao(leituras), encoding="utf-8")
        bloqueantes = sum(1 for e in todos_erros if e["gravidade"] == "bloqueia")
        lote["execucao"] = execucao(self.config, [leituras[c] for c in por_codigo if c in leituras],
                                    lote.get("custo_usd", 0.0), time.monotonic() - comeco)
        gravar_json(catalogacao / "pacote_tainacan.json",
                    pacote_tainacan(codigo, nome, ctx, leituras, preparos, mapa, self.raiz_final,
                                    catalogacao, ler_json(unidade.pasta / "info_projeto.json", {})
                                    or ler_json(pasta / "info_projeto.json", {}) or {},
                                    unidade.relativo.name, lote["execucao"]))
        propostas = ler_json(catalogacao / "pacote_tainacan.json", {}).get("propostas") or {}
        if propostas.get("obras_na_pasta") or propostas.get("fotos_sem_obra"):
            gravar_json(catalogacao / "propostas.json", propostas)
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
        (catalogacao / "relatorio.txt").write_text(
            relatorio_lote(codigo, nome, lote, leituras, por_codigo, todos_erros,
                           _contagem_esperada(unidade), catalogacao), encoding="utf-8")
        (catalogacao / "catalogacao_ERRO.txt").unlink(missing_ok=True)
        prontos = (ler_json(pasta / "status.json", {}) or {}).get("lotes_prontos", [])
        self._status(
            pasta, codigo, PRONTO, ctx,
            lotes_prontos=sorted(set(prontos) | {ctx.lote_id}),
            imagens=imagens, folhas=documentos_projeto,
            com_carimbo=sum(1 for l in leituras.values() if l.carimbo_encontrado),
            a_revisar=sum(1 for l in leituras.values() if mod_planilha.precisa_revisar(l)),
            erros_bloqueantes=bloqueantes, motivo="",
        )

        resultado.status, resultado.lote = PRONTO, lote
        return resultado

    def _garantir_exif(self, ja_arquivados: list, ctx: Contexto, estado_vigia=None):
        """Arquivos que já estavam no acervo: confere o XMP e regrava só o que faltar.

        Devolve None quando está tudo certo (ou o config não exige EXIF), senão
        (conferidos, total, aviso). Não usa API: as leituras vêm do leituras.json do projeto.
        """
        from . import planilha as mod_planilha

        destinos: dict[Path, tuple[str, str]] = {}
        for origem, destino, codigo_doc, _s in ja_arquivados:
            if destino.suffix.lower() not in formatos.SEM_EXIF and destino.exists():
                destinos.setdefault(destino, (codigo_doc, origem.name))
        if not destinos:
            return None
        conferidos = conferir_exif([(d, d.stem) for d in destinos])
        faltam = [d for d in destinos if d not in conferidos]
        if not faltam:
            for d in {d.parents[2] for d in destinos if len(d.parents) > 2}:
                limpar_exif_pendente(d / "catalogacao")
            return None
        _log.info("Lote %s: %d de %d arquivo(s) já no acervo sem EXIF completo — regravando só esses.",
                  ctx.lote_id, len(faltam), len(destinos))
        if estado_vigia is not None:
            estado_vigia.situacao = f"gravando metadados ({len(faltam)})"
        leituras_por_projeto: dict[Path, dict] = {}
        itens = []
        for d in faltam:
            projeto = d.parents[2]
            if projeto not in leituras_por_projeto:
                leituras_por_projeto[projeto] = {
                    Path(l.arquivo).stem: l
                    for l in mod_planilha.ler_json(projeto / "catalogacao" / "leituras.json")}
            codigo_doc, nome_original = destinos[d]
            itens.append((d, campos_do_exif(ctx, d.stem, leituras_por_projeto[projeto].get(d.stem),
                                            estrutura.nome_sem_codigo(projeto.name), nome_original)))
        gravados, avisos = mod_metadados.gravar_em_lote(itens, self.config.identidade())
        ok_agora = conferir_exif([(d, d.stem) for d, _ in itens])
        for d in ok_agora:
            self.livro.anotar("exif_gravado", lote_id=ctx.lote_id, codigo_documento=d.stem,
                              arquivo_destino=self._relativo(d), detalhe="regravado na retomada do lote")
        if len(ok_agora) < len(faltam):
            if self.config.entrada_exigir_exif:
                return (len(destinos) - len(faltam) + len(ok_agora), len(destinos), avisos[-1] if avisos else "")
            return None
        for projeto in leituras_por_projeto:
            limpar_exif_pendente(projeto / "catalogacao")
        return None

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
                rel_origem = Path(unidade.relativo) / origem.relative_to(unidade.pasta)
                if self.config.quarentena_entrada:
                    from . import quarentena as mod_quarentena

                    novo = mod_quarentena.mover(origem, self.raiz_entrada, rel_origem, {
                        "codigo_projeto": codigo, "codigo_documento": codigo_doc, "sha256": soma,
                        "lote_id": ctx.lote_id})
                    apagados += 1
                    self.livro.anotar(
                        "quarentena", lote_id=ctx.lote_id, codigo_projeto=codigo, codigo_documento=codigo_doc,
                        arquivo_origem=str(Path("100 - Scanners") / rel_origem),
                        arquivo_destino=str(Path("100 - Scanners") / novo.relative_to(self.raiz_entrada)),
                        nome_original=origem.name, tamanho=tamanho, sha256=soma,
                        detalhe="original em quarentena até a réplica confirmar")
                    continue
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
            for marcador in marcadores_da(unidade.pasta):
                marcador.unlink(missing_ok=True)
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
