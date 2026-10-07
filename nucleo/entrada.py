"""Entrada bruta — os lotes que as estações mandam para o QNAP.

As estações (Foto/Mac, Contex/Windows, Universal) só capturam e enviam: cada
lote chega numa subpasta de `pasta_entrada` com um `manifesto.json`. Quem
organiza o acervo final é o CAMP Vision 2:

    entrada/<lote>/manifesto.json + imagens
        │  1. lote estável e completo (manifesto + contagem bate)
        │  2. COPIA para  <acervo>/<Fundo>/<Projeto>/<Série>/   (original intacto)
        │  3. lê os carimbos (vigia.processar)
        │  4. grava EXIF com o crédito nas CÓPIAS
        │  5. escreve <Projeto>/catalogacao/lotes/<lote>.json
        └─ 6. só então marca o lote como "pronto" (último passo, gravação atômica)

O painel considera material pronto SÓ quando o JSON do lote diz
`"status": "pronto"`. Qualquer falha no meio deixa `"status": "erro"` com o
motivo, ou `"processando"` — nunca um "pronto" parcial.

Nada na pasta de entrada é alterado ou apagado. O que já foi recebido fica
registrado em `~/.campvision2/entrada.jsonl` e no próprio JSON do lote.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import imagem, metadados as mod_metadados
from .aplicar import limpar_nome
from .config import VERSAO_BUILD, Config
from .esquema import CAMPOS

_log = logging.getLogger("cv2.entrada")

NOME_MANIFESTO = "manifesto.json"
VERSAO_JSON_FINAL = 1

# Estados do JSON do lote. O painel só trata como pronto o último.
PROCESSANDO, ERRO, PRONTO = "processando", "erro", "pronto"

CODIGO_FUNDO = re.compile(r"^F\d{3}$")
CODIGO_PROJETO = re.compile(r"^P\d{4}$")

# tipo_material do manifesto -> série. A série também pode vir explícita.
SERIE_POR_MATERIAL: dict[str, str] = {
    "prancha": "S01", "pranchas": "S01", "desenho": "S01", "desenhos": "S01",
    "documento": "S02", "documentos": "S02", "texto": "S02", "textual": "S02",
    "foto": "S03", "fotos": "S03", "fotografia": "S03", "fotografias": "S03",
    "negativo": "S04", "negativos": "S04",
    "slide": "S05", "slides": "S05",
    "material": "S06", "materiais": "S06",
}

CAMPOS_OBRIGATORIOS = ("operador", "estacao", "fundo", "projeto", "tipo_material")


class ManifestoInvalido(ValueError):
    pass


@dataclass
class Manifesto:
    lote_id: str
    operador: str
    operador_email: str
    estacao: str
    tipo_estacao: str
    fundo: str
    fundo_nome: str
    projeto: str
    projeto_codigo: str
    ano: str
    tipo_material: str
    serie: str
    enviado_em: str
    contagem_esperada: int | None
    bruto: dict = field(default_factory=dict)

    def contexto(self) -> dict:
        """O que vai para o status.json e para o JSON final."""
        return {
            "lote_id": self.lote_id,
            "operador": self.operador,
            "operador_email": self.operador_email,
            "estacao": self.estacao,
            "tipo_estacao": self.tipo_estacao,
            "fundo": self.fundo,
            "projeto": self.projeto,
            "projeto_codigo": self.projeto_codigo,
            "ano": self.ano,
            "tipo_material": self.tipo_material,
            "serie": self.serie,
            "enviado_em": self.enviado_em,
        }


def _texto(dados: dict, *chaves: str) -> str:
    for chave in chaves:
        valor = dados.get(chave)
        if valor not in (None, ""):
            return str(valor).strip()
    return ""


def ler_manifesto(pasta_lote: Path) -> Manifesto:
    """Lê e valida o manifesto. Levanta ManifestoInvalido com o motivo."""
    caminho = pasta_lote / NOME_MANIFESTO
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ManifestoInvalido("sem manifesto.json") from None
    except (OSError, json.JSONDecodeError) as erro:
        raise ManifestoInvalido(f"manifesto.json ilegível: {erro}") from None
    if not isinstance(dados, dict):
        raise ManifestoInvalido("manifesto.json não é um objeto JSON")

    faltando = [c for c in CAMPOS_OBRIGATORIOS if not _texto(dados, c)]
    if faltando:
        raise ManifestoInvalido("faltam campos no manifesto: " + ", ".join(faltando))

    fundo = _texto(dados, "fundo").upper()
    if not CODIGO_FUNDO.match(fundo):
        # O código vem SEMPRE da tabela de autoridade, nunca de nome de pasta.
        raise ManifestoInvalido(f"fundo '{fundo}' não é um código F000 da tabela de autoridade")

    projeto_codigo = _texto(dados, "projeto_codigo").upper()
    if projeto_codigo and not CODIGO_PROJETO.match(projeto_codigo):
        raise ManifestoInvalido(f"projeto_codigo '{projeto_codigo}' não está no formato P0000")

    tipo_material = _texto(dados, "tipo_material").lower()
    serie = _texto(dados, "serie").upper() or SERIE_POR_MATERIAL.get(tipo_material, "")
    if not re.match(r"^S0[1-6]$", serie):
        raise ManifestoInvalido(
            f"não sei a série do material '{tipo_material}' — informe 'serie' (S01–S06)"
        )

    contagens = dados.get("contagens") if isinstance(dados.get("contagens"), dict) else {}
    esperada = contagens.get("arquivos", dados.get("arquivos"))
    try:
        esperada = int(esperada) if esperada not in (None, "") else None
    except (TypeError, ValueError):
        raise ManifestoInvalido(f"contagens.arquivos inválido: {esperada!r}") from None

    return Manifesto(
        lote_id=limpar_nome(_texto(dados, "lote_id", "lote") or pasta_lote.name, 80),
        operador=_texto(dados, "operador"),
        operador_email=_texto(dados, "operador_email", "email"),
        estacao=_texto(dados, "estacao"),
        tipo_estacao=_texto(dados, "tipo_estacao"),
        fundo=fundo,
        fundo_nome=_texto(dados, "fundo_nome"),
        projeto=_texto(dados, "projeto"),
        projeto_codigo=projeto_codigo,
        ano=_texto(dados, "ano"),
        tipo_material=tipo_material,
        serie=serie,
        enviado_em=_texto(dados, "enviado_em", "horario", "data_envio"),
        contagem_esperada=esperada,
        bruto=dados,
    )


# ------------------------------------------------------------- arquivos

def arquivos_do_lote(pasta_lote: Path, config: Config) -> list[Path]:
    """Todas as imagens do lote, em qualquer subpasta, com caminho estável."""
    extensoes = {e.lower() for e in config.extensoes}
    achados = [
        p for p in pasta_lote.rglob("*")
        if p.is_file() and p.suffix.lower() in extensoes
        and not any(parte.startswith(".") for parte in p.relative_to(pasta_lote).parts)
    ]
    return sorted(achados)


def lote_estavel(pasta_lote: Path, arquivos: list[Path], config: Config) -> bool:
    """Nada mexido há menos de `espera_estabilidade_segundos` (cópia em curso)."""
    limite = time.time() - config.espera_estabilidade_segundos
    for caminho in [pasta_lote / NOME_MANIFESTO, *arquivos]:
        try:
            if caminho.stat().st_mtime > limite:
                return False
        except OSError:
            return False
    return True


def _hash(caminho: Path) -> str:
    h = hashlib.sha256()
    with caminho.open("rb") as f:
        for bloco in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloco)
    return h.hexdigest()


def _achar_filho(pai: Path, codigo: str, nome_exato: str) -> Path | None:
    """Pasta existente que começa com o código (F026..., P0001...) ou tem o nome exato."""
    if not pai.is_dir():
        return None
    alvo = nome_exato.lower()
    for filho in sorted(pai.iterdir()):
        if not filho.is_dir():
            continue
        nome = filho.name
        if codigo and re.match(rf"^{re.escape(codigo)}(\b|[\s_-])", nome, re.IGNORECASE):
            return filho
        if nome.lower() == alvo:
            return filho
    return None


def destino_do_lote(raiz_final: Path, m: Manifesto, config: Config) -> tuple[Path, Path]:
    """(pasta do projeto, pasta da série) na estrutura Fundo → Projeto → Série."""
    nome_fundo = limpar_nome(f"{m.fundo} - {m.fundo_nome}" if m.fundo_nome else m.fundo)
    pasta_fundo = _achar_filho(raiz_final, m.fundo, nome_fundo) or raiz_final / nome_fundo

    rotulo = m.projeto + (f" ({m.ano})" if m.ano and m.ano not in m.projeto else "")
    nome_projeto = limpar_nome(
        f"{m.projeto_codigo} - {rotulo}" if m.projeto_codigo else rotulo, 100
    )
    pasta_projeto = (
        _achar_filho(pasta_fundo, m.projeto_codigo, nome_projeto)
        or pasta_fundo / nome_projeto
    )

    nome_serie = config.pastas_series.get(m.serie, m.serie)
    return pasta_projeto, pasta_projeto / nome_serie


def copiar_lote(
    pasta_lote: Path, arquivos: list[Path], pasta_serie: Path
) -> tuple[list[dict], list[str]]:
    """Copia preservando subpastas (TIF/, JPG/). Nunca sobrescreve.

    Arquivo igual já no destino (mesmo conteúdo) é aceito — é o que deixa
    repetir o recebimento depois de uma queda. Arquivo DIFERENTE com o mesmo
    nome é erro: o lote para em vez de perder uma prancha.
    """
    copiados: list[dict] = []
    conflitos: list[str] = []
    for origem in arquivos:
        relativo = origem.relative_to(pasta_lote)
        destino = pasta_serie / relativo
        if destino.exists():
            if destino.stat().st_size == origem.stat().st_size and _hash(destino) == _hash(origem):
                copiados.append({"origem": str(relativo), "destino": str(destino), "ja_existia": True})
            else:
                conflitos.append(str(relativo))
            continue
        destino.parent.mkdir(parents=True, exist_ok=True)
        temporario = destino.with_name(f".{destino.name}.cv2tmp")
        shutil.copy2(origem, temporario)
        if temporario.stat().st_size != origem.stat().st_size:
            temporario.unlink(missing_ok=True)
            conflitos.append(f"{relativo} (cópia incompleta)")
            continue
        os.replace(temporario, destino)
        copiados.append({"origem": str(relativo), "destino": str(destino), "ja_existia": False})
    return copiados, conflitos


# ------------------------------------------------------------- JSON final

def caminho_json_final(pasta_projeto: Path, lote_id: str) -> Path:
    return pasta_projeto / "catalogacao" / "lotes" / f"{lote_id}.json"


def gravar_json_atomico(caminho: Path, dados: dict) -> None:
    """Escreve em temporário e renomeia: o painel nunca lê um JSON pela metade."""
    caminho.parent.mkdir(parents=True, exist_ok=True)
    temporario = caminho.with_name(f".{caminho.name}.tmp")
    temporario.write_text(json.dumps(dados, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporario, caminho)


def ler_json_final(caminho: Path) -> dict:
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
        return dados if isinstance(dados, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


# ------------------------------------------------------------- registro

def _registro(pasta_estado: Path) -> Path:
    return pasta_estado / "entrada.jsonl"


def lotes_registrados(pasta_estado: Path) -> dict[str, dict]:
    """Chave (pasta + hash do manifesto) -> última linha registrada."""
    caminho = _registro(pasta_estado)
    vistos: dict[str, dict] = {}
    if not caminho.exists():
        return vistos
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(linha)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and item.get("chave"):
            vistos[item["chave"]] = item
    return vistos


def registrar(pasta_estado: Path, item: dict) -> None:
    pasta_estado.mkdir(parents=True, exist_ok=True)
    with _registro(pasta_estado).open("a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")


def chave_do_lote(pasta_lote: Path) -> str:
    try:
        assinatura = _hash(pasta_lote / NOME_MANIFESTO)[:16]
    except OSError:
        assinatura = "sem-manifesto"
    return f"{pasta_lote.name}:{assinatura}"


# ------------------------------------------------------------- descoberta

@dataclass
class LoteBruto:
    pasta: Path
    chave: str


def varrer(pasta_entrada: Path, pasta_estado: Path) -> list[LoteBruto]:
    """Lotes com manifesto ainda não tratados.

    Pronto, erro e recusado não voltam sozinhos: o lote só entra de novo se a
    estação reenviar o manifesto (muda o hash) ou com `vigia.py --refazer-lote`.
    """
    if not pasta_entrada.is_dir():
        return []
    feitos = lotes_registrados(pasta_estado)
    achados: list[tuple[float, LoteBruto]] = []
    for pasta in sorted(pasta_entrada.iterdir()):
        if not pasta.is_dir() or pasta.name.startswith((".", "_")):
            continue
        if not (pasta / NOME_MANIFESTO).exists():
            continue  # estação ainda enviando: o manifesto é gravado por último
        chave = chave_do_lote(pasta)
        if feitos.get(chave, {}).get("status") in (PRONTO, ERRO, "recusado"):
            continue
        achados.append(((pasta / NOME_MANIFESTO).stat().st_mtime, LoteBruto(pasta, chave)))
    return [l for _, l in sorted(achados, key=lambda x: x[0])]


# ------------------------------------------------------------- EXIF

def _metadados_por_arquivo(leituras, pasta_projeto: Path, copiados: list[dict]) -> list[tuple[Path, dict]]:
    """Casa cada cópia com a leitura do carimbo pelo nome-base (TIF e JPG irmãos)."""
    por_base = {Path(l.arquivo).stem.lower(): l for l in leituras}
    itens: list[tuple[Path, dict]] = []
    for c in copiados:
        destino = Path(c["destino"])
        leitura = por_base.get(destino.stem.lower())
        valores = {
            campo.rotulo: (leitura.valores.get(campo.nome, "") if leitura else "")
            for campo in CAMPOS
        }
        itens.append((destino, valores))
    return itens


# ------------------------------------------------------------- processamento

def processar_lote(
    lote_bruto: LoteBruto,
    config: Config,
    cliente,
    pasta_estado: Path,
    estado=None,
    cancelar=None,
) -> dict:
    """Recebe um lote de ponta a ponta. Devolve o JSON final (dict)."""
    from . import vigia as mod_vigia  # evita import circular

    pasta_lote = lote_bruto.pasta
    raiz_final = Path(config.pasta_acervo_final or config.pasta_vigiada)
    agora = lambda: datetime.now().isoformat(timespec="seconds")  # noqa: E731

    try:
        m = ler_manifesto(pasta_lote)
    except ManifestoInvalido as erro:
        _log.error("Lote %s recusado: %s", pasta_lote.name, erro)
        item = {"chave": lote_bruto.chave, "lote": pasta_lote.name, "status": "recusado",
                "motivo": str(erro), "em": agora()}
        registrar(pasta_estado, item)
        return item

    arquivos = arquivos_do_lote(pasta_lote, config)
    if not lote_estavel(pasta_lote, arquivos, config):
        return {"status": "aguardando", "motivo": "arquivos ainda mudando"}
    if m.contagem_esperada is not None and len(arquivos) != m.contagem_esperada:
        # Pode ser cópia ainda chegando por SMB; não recusa, espera e avisa.
        _log.warning("Lote %s: manifesto diz %d arquivo(s), achei %d — aguardando.",
                     pasta_lote.name, m.contagem_esperada, len(arquivos))
        return {"status": "aguardando",
                "motivo": f"contagem {len(arquivos)} de {m.contagem_esperada}"}
    if not arquivos:
        return {"status": "aguardando", "motivo": "lote sem imagens"}

    pasta_projeto, pasta_serie = destino_do_lote(raiz_final, m, config)
    caminho_final = caminho_json_final(pasta_projeto, m.lote_id)
    final: dict = {
        "versao": VERSAO_JSON_FINAL,
        "gerado_por": f"CAMP Vision 2 {VERSAO_BUILD}",
        "status": PROCESSANDO,
        "status_em": agora(),
        "recebido_em": agora(),
        "concluido_em": None,
        "lote": m.contexto(),
        "origem": {"pasta_entrada": str(pasta_lote), "manifesto": m.bruto},
        "destino": {
            "raiz": str(raiz_final),
            "projeto": str(pasta_projeto),
            "serie": str(pasta_serie),
        },
        "contagens": {"esperados": m.contagem_esperada, "encontrados": len(arquivos)},
        "arquivos": [],
        "exif": {},
        "outliers": [],
        "erros": [],
        "historico": [{"em": agora(), "evento": "recebido", "por": m.operador, "estacao": m.estacao}],
    }
    gravar_json_atomico(caminho_final, final)

    def falhar(motivo: str) -> dict:
        final["status"] = ERRO
        final["status_em"] = agora()
        final["erros"].append(motivo)
        final["historico"].append({"em": agora(), "evento": "erro", "motivo": motivo})
        gravar_json_atomico(caminho_final, final)
        registrar(pasta_estado, {"chave": lote_bruto.chave, "lote": m.lote_id, "status": ERRO,
                                 "motivo": motivo, "json": str(caminho_final), "em": agora()})
        _log.error("Lote %s: %s", m.lote_id, motivo)
        return final

    # 2. organização: cópia para Fundo → Projeto → Série
    try:
        copiados, conflitos = copiar_lote(pasta_lote, arquivos, pasta_serie)
    except OSError as erro:
        return falhar(f"falha ao copiar para {pasta_serie}: {erro}")
    if conflitos:
        return falhar("arquivo com mesmo nome e conteúdo diferente no destino: "
                      + ", ".join(conflitos[:10]))
    final["historico"].append({"em": agora(), "evento": "organizado", "arquivos": len(copiados)})

    # Status do projeto com o contexto do lote — o painel e o QNAP leem daqui.
    status_projeto = pasta_projeto / mod_vigia.NOME_STATUS
    mod_vigia.escrever_status(status_projeto, config.status_pronto, {
        "lote_atual": m.lote_id, "contexto_ultimo_lote": m.contexto(),
    })
    gravar_json_atomico(caminho_final, final)

    # 3. leitura dos carimbos
    pastas = mod_vigia.achar_pastas_de_imagens(pasta_projeto, config)
    projeto = mod_vigia.Projeto(pasta_projeto, pastas or [pasta_serie], pasta_projeto.name, raiz_final)
    evento = mod_vigia.processar(projeto, config, cliente, estado, cancelar)
    if evento.falha:
        return falhar(f"leitura falhou: {evento.falha}")

    from . import planilha as mod_planilha
    leituras = mod_planilha.ler_json(pasta_projeto / "catalogacao" / "leituras.json")
    do_lote = {Path(c["destino"]).stem.lower() for c in copiados}
    leituras_lote = [l for l in leituras if Path(l.arquivo).stem.lower() in do_lote]

    # 4. EXIF com o crédito, nas cópias (o original da entrada não é tocado)
    itens = _metadados_por_arquivo(leituras_lote, pasta_projeto, copiados)
    gravados, avisos = mod_metadados.gravar_em_lote(itens, config.identidade())
    final["exif"] = {"gravados": gravados, "total": len(itens), "avisos": avisos}
    if config.entrada_exigir_exif and gravados < len(itens):
        return falhar(f"EXIF gravado em {gravados} de {len(itens)} arquivo(s)"
                      + (f": {avisos[-1]}" if avisos else ""))

    # 5. JSON final completo
    por_base = {Path(l.arquivo).stem.lower(): l for l in leituras_lote}
    for c in copiados:
        destino = Path(c["destino"])
        leitura = por_base.get(destino.stem.lower())
        final["arquivos"].append({
            "origem": c["origem"],
            "destino": str(destino.relative_to(raiz_final)),
            "tipo": destino.suffix.lower().lstrip("."),
            "metadados": dict(leitura.valores) if leitura else {},
            "carimbo_encontrado": bool(leitura and leitura.carimbo_encontrado),
            "erro": leitura.erro if leitura else "",
        })
        if leitura and (leitura.divergencias or leitura.suspeita_grupo):
            final["outliers"].append({
                "arquivo": destino.name,
                "divergencias": list(leitura.divergencias),
                "suspeita_grupo": leitura.suspeita_grupo,
            })
        if leitura and leitura.erro:
            final["erros"].append(f"{destino.name}: {leitura.erro}")
    final["contagens"].update({
        "copiados": len(copiados),
        "lidos": len(leituras_lote),
        "com_carimbo": sum(1 for l in leituras_lote if l.carimbo_encontrado),
        "com_erro": sum(1 for l in leituras_lote if l.erro),
        "outliers": len(final["outliers"]),
        "custo_usd": round(evento.custo_usd, 4),
    })

    # 6. pronto: SÓ agora, e como última gravação
    final["status"] = PRONTO
    final["status_em"] = final["concluido_em"] = agora()
    final["historico"].append({"em": agora(), "evento": "pronto"})
    mod_vigia.escrever_status(status_projeto, config.status_concluido, {
        "lote_atual": m.lote_id, "ultimo_lote_pronto": m.lote_id,
        "ultimo_lote_json": str(caminho_final.relative_to(pasta_projeto)),
    })
    gravar_json_atomico(caminho_final, final)
    registrar(pasta_estado, {"chave": lote_bruto.chave, "lote": m.lote_id, "status": PRONTO,
                             "json": str(caminho_final), "em": agora()})
    _log.info("Lote %s pronto: %d arquivo(s) em %s.", m.lote_id, len(copiados), pasta_serie)
    return final
