"""Passada 2 — leitura (método de leitura §0, §3, §6).

Uma chamada por folha. Página INTEIRA, 2000 px, já orientada pelo preparo.
Sem localizar nem recortar o carimbo: o recorte destrói dado (data a lápis na
margem, número de folha no contorno, monograma, amostras de material).

Prancha → prompt de transcrição literal (§3.1), esquema {valor, confianca,
alternativas, onde} por campo (§3.2). Fotografia/negativo/slide → prompt
próprio (§6.1); crédito de fotógrafo nunca vem do modelo (§6.2).
"""

from __future__ import annotations

import logging
import random
import re
import time
from pathlib import Path
from typing import Any, Protocol

from PIL import Image

from . import imagem as img_mod
from .config import Config
from .esquema import CAMPOS_DO_MODELO, Leitura, esquema_ferramenta, esquema_fotografia

_log = logging.getLogger("cv2.visao")

LADO_ENVIO = 2000
QUALIDADE_ENVIO = 78

INSTRUCOES = """Você vai ler UMA prancha de arquitetura digitalizada e TRANSCREVER o que está
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
7. "arquiteto" é a pessoa; "escritorio" é a empresa. Nunca misture os dois.
8. Em "onde", diga em que parte da folha leu: carimbo, margem, contorno,
   manuscrito, legenda ou corpo.

Devolva chamando a ferramenta registrar_prancha."""

INSTRUCOES_FOTO = """Você vai descrever UMA fotografia de acervo de arquitetura.

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

Devolva chamando a ferramenta registrar_fotografia."""

# Série pelo código do documento: S03 fotografias, S04 negativos, S05 slides.
SERIES_FOTO = ("S03", "S04", "S05")


def modo_do_arquivo(nome: str) -> str:
    achado = re.search(r"-(S\d{2})-D\d{5}", nome)
    return "fotografia" if achado and achado.group(1) in SERIES_FOTO else "prancha"


class ClienteAPI(Protocol):
    """Interface mínima usada aqui — permite testar sem rede."""

    def chamar(
        self, mensagens: list[dict[str, Any]], ferramenta: dict[str, Any], sistema: str
    ) -> tuple[dict[str, Any], int, int]:
        """Devolve (entrada_da_ferramenta, tokens_entrada, tokens_saida)."""
        ...


class ClienteAnthropic:
    """Cliente real. Importa o SDK só quando usado, para os testes rodarem sem ele."""

    def __init__(self, config: Config) -> None:
        import os

        try:
            import anthropic
        except ImportError as erro:  # pragma: no cover - ambiente
            raise RuntimeError(
                "Pacote 'anthropic' não instalado. Rode: pip install anthropic"
            ) from erro

        chave = config.api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not chave:
            raise RuntimeError(
                "Sem chave de API. Defina ANTHROPIC_API_KEY no ambiente ou "
                "preencha a chave na janela de configuração."
            )
        # Chave de usuário (sk-ant-usr-...) não tem workspace: a API exige o
        # cabeçalho anthropic-workspace-id. Vem de ANTHROPIC_WORKSPACE_ID.
        import os

        workspace = os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip()
        cabecalhos = {"anthropic-workspace-id": workspace} if workspace else None
        self._cliente = anthropic.Anthropic(
            api_key=chave, timeout=config.timeout_segundos, default_headers=cabecalhos,
        )
        self._config = config

    def chamar(
        self, mensagens: list[dict[str, Any]], ferramenta: dict[str, Any], sistema: str
    ) -> tuple[dict[str, Any], int, int]:
        cfg = self._config
        ultima_excecao: Exception | None = None
        for tentativa in range(1, cfg.max_tentativas_api + 1):
            try:
                resposta = self._cliente.messages.create(
                    model=cfg.modelo,
                    max_tokens=4000,
                    system=sistema,
                    messages=mensagens,
                    tools=[ferramenta],
                    tool_choice={"type": "tool", "name": ferramenta["name"]},
                )
            except Exception as erro:  # noqa: BLE001 - SDK levanta tipos variados
                ultima_excecao = erro
                if tentativa == cfg.max_tentativas_api:
                    break
                espera = min(30.0, 2.0**tentativa) + random.uniform(0, 1)
                _log.warning(
                    "Falha na API (tentativa %d/%d): %s — repetindo em %.1fs",
                    tentativa, cfg.max_tentativas_api, erro, espera,
                )
                time.sleep(espera)
                continue

            entrada = _extrair_uso_de_ferramenta(resposta, ferramenta["name"])
            uso = getattr(resposta, "usage", None)
            return (
                entrada,
                getattr(uso, "input_tokens", 0) or 0,
                getattr(uso, "output_tokens", 0) or 0,
            )
        raise RuntimeError(f"API falhou após {cfg.max_tentativas_api} tentativas: {ultima_excecao}")


def _extrair_uso_de_ferramenta(resposta: Any, nome: str) -> dict[str, Any]:
    """Pega o bloco tool_use pelo TIPO, nunca por posição na lista."""
    for bloco in getattr(resposta, "content", []) or []:
        if getattr(bloco, "type", None) == "tool_use" and getattr(bloco, "name", None) == nome:
            return dict(getattr(bloco, "input", {}) or {})
    raise RuntimeError("A resposta não trouxe o bloco tool_use esperado.")


def _bloco_imagem(env: img_mod.ImagemParaEnvio) -> dict[str, Any]:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": env.media_type,
            "data": env.base64,
        },
    }


def _texto(valor: Any) -> str:
    return "" if valor is None else str(valor).strip()


def _lista(valor: Any) -> list[str]:
    if isinstance(valor, (list, tuple)):
        return [str(v).strip() for v in valor if str(v).strip()]
    return [str(valor).strip()] if _texto(valor) else []


def _para_leitura(entrada: dict[str, Any], leitura: Leitura) -> Leitura:
    """Converte a saída da ferramenta em Leitura, sem confiar na forma.

    Aceita também o formato antigo (carimbo_encontrado, nota, rotacao).
    """
    leitura.carimbo_encontrado = bool(entrada.get("tem_carimbo", entrada.get("carimbo_encontrado", False)))
    leitura.legivel = bool(entrada.get("legivel", True))
    leitura.nota_ia = _texto(entrada.get("nota"))
    try:
        leitura.rotacao = int(entrada.get("rotacao", 0) or 0) % 360
    except (TypeError, ValueError):
        leitura.rotacao = 0
    leitura.transcricao_integral = _texto(entrada.get("transcricao_integral"))
    leitura.materiais_citados = _lista(entrada.get("materiais_citados"))
    leitura.anotacoes_manuscritas = _lista(entrada.get("anotacoes_manuscritas"))
    leitura.observacoes_leitura = _lista(entrada.get("observacoes_de_leitura"))

    for campo in CAMPOS_DO_MODELO:
        bruto = entrada.get(campo.nome)
        if not isinstance(bruto, dict):
            bruto = {"valor": bruto, "confianca": 0.0}
        valor = _texto(bruto.get("valor"))
        try:
            confianca = max(0.0, min(1.0, float(bruto.get("confianca", 0.0) or 0.0)))
        except (TypeError, ValueError):
            confianca = 0.0
        leitura.valores[campo.nome] = valor
        leitura.confiancas[campo.nome] = confianca if valor else 0.0
        alternativas = [a for a in _lista(bruto.get("alternativas")) if a != valor]
        if alternativas:
            leitura.alternativas[campo.nome] = alternativas
        if bruto.get("onde") and valor:
            leitura.onde[campo.nome] = _texto(bruto.get("onde"))
    # Derivações em código, nunca pedidas ao modelo (§5): ano da data escrita,
    # tipo de desenho do título escrito, ficha de documentação.
    from .derivacao import ano_da_data, e_documento, tipo_de_desenho

    data = leitura.valores.get("data", "")
    if data and ano_da_data(data):
        leitura.valores["ano"] = ano_da_data(data)
        leitura.confiancas["ano"] = leitura.confiancas.get("data", 0.0) if leitura.valores["ano"] else 0.0
        anos_alt = [ano_da_data(a) for a in leitura.alternativas.get("data", [])]
        anos_alt = [a for a in anos_alt if a and a != leitura.valores["ano"]]
        if anos_alt:
            leitura.alternativas["ano"] = anos_alt
    titulo = leitura.valores.get("titulo_prancha", "")
    leitura.valores["tipo"] = tipo_de_desenho(titulo)
    leitura.confiancas["tipo"] = leitura.confiancas.get("titulo_prancha", 0.0) if leitura.valores["tipo"] else 0.0
    leitura.e_documento = e_documento(titulo)
    # Formato antigo trazia "ano" do modelo; o novo deriva em código.
    if not leitura.valores.get("ano") and _texto((entrada.get("ano") or {}).get("valor") if isinstance(entrada.get("ano"), dict) else entrada.get("ano")):
        bruto = entrada["ano"] if isinstance(entrada.get("ano"), dict) else {"valor": entrada["ano"], "confianca": 0.0}
        leitura.valores["ano"] = _texto(bruto.get("valor"))
        leitura.confiancas["ano"] = float(bruto.get("confianca", 0.0) or 0.0)
    return leitura


def _para_foto(entrada: dict[str, Any], leitura: Leitura) -> Leitura:
    leitura.modo = "fotografia"
    leitura.carimbo_encontrado = False
    foto = {
        "tipo_de_imagem": _texto(entrada.get("tipo_de_imagem")) or "fotografia",
        "assunto": _texto(entrada.get("assunto")),
        "enquadramento": _texto(entrada.get("enquadramento")),
        "elementos_visiveis": _lista(entrada.get("elementos_visiveis")),
        "texto_na_imagem": _lista(entrada.get("texto_na_imagem")),
        "legenda_proposta": _texto(entrada.get("legenda_proposta")),
    }
    try:
        confianca = max(0.0, min(1.0, float(entrada.get("confianca", 0.0) or 0.0)))
    except (TypeError, ValueError):
        confianca = 0.0
    leitura.foto = foto
    leitura.valores["titulo_prancha"] = foto["legenda_proposta"]
    leitura.confiancas["titulo_prancha"] = confianca if foto["legenda_proposta"] else 0.0
    leitura.transcricao_integral = " | ".join(foto["texto_na_imagem"])
    leitura.valores["tipo"] = "Fotografia"
    leitura.confiancas["tipo"] = 1.0
    return leitura


class LeitorDeCarimbo:
    """Uma chamada por folha, página inteira. Nome mantido por compatibilidade."""

    def __init__(self, config: Config, cliente: ClienteAPI) -> None:
        self.config = config
        self.cliente = cliente
        self.ferramenta = esquema_ferramenta()
        self.ferramenta_foto = esquema_fotografia()

    def ler(self, caminho: Path, regiao_sugerida=None, modo: str | None = None) -> Leitura:
        resultado = Leitura(arquivo=caminho.name)
        modo = modo or modo_do_arquivo(caminho.name)
        try:
            original = img_mod.abrir(caminho)
        except Exception as erro:  # noqa: BLE001
            resultado.erro = f"não foi possível abrir a imagem: {erro}"
            _log.error("%s: %s", caminho.name, resultado.erro)
            return resultado
        try:
            env = img_mod.para_envio(original, LADO_ENVIO, QUALIDADE_ENVIO)
            foto = modo == "fotografia"
            texto = "Descreva esta fotografia." if foto else "Transcreva esta prancha."
            mensagens = [{"role": "user", "content": [_bloco_imagem(env), {"type": "text", "text": texto}]}]
            entrada, t_in, t_out = self.cliente.chamar(
                mensagens, self.ferramenta_foto if foto else self.ferramenta,
                INSTRUCOES_FOTO if foto else INSTRUCOES,
            )
            resultado.tokens_entrada, resultado.tokens_saida, resultado.passes = t_in, t_out, 1
            return _para_foto(entrada, resultado) if foto else _para_leitura(entrada, resultado)
        except Exception as erro:  # noqa: BLE001
            resultado.erro = str(erro)
            _log.error("%s: %s", caminho.name, erro)
            return resultado
        finally:
            original.close()
