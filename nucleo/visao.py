"""Leitura do carimbo por modelo de visão.

Estratégia, em ordem:

1. Se há região conhecida (cache da prancha anterior da mesma pasta), manda
   direto o recorte em alta resolução — 1 chamada, barata e precisa.
2. Senão, manda a página inteira reduzida. O modelo devolve os campos E a
   região do carimbo em coordenadas normalizadas.
3. Se a confiança média ficou abaixo do limiar, faz o 2º passe: recorta a
   região devolvida na resolução ORIGINAL e relê só ela.

Não existe detector geométrico, YOLO, limiar de contorno nem correção de
orientação por heurística. Quem localiza e quem lê é o mesmo modelo.
"""

from __future__ import annotations

import logging
import random
import time
from pathlib import Path
from typing import Any, Protocol

from PIL import Image

from . import imagem as img_mod
from .config import Config
from .esquema import CAMPOS, Leitura, esquema_consolidacao, esquema_ferramenta

_log = logging.getLogger("cv2.visao")

INSTRUCOES = """Você lê carimbos (legendas de título) de pranchas de arquitetura \
brasileiras digitalizadas, a maioria entre 1940 e 1990.

Como trabalhar:
- O carimbo é o bloco com o nome do projeto, do autor e dados técnicos. Costuma \
ficar num canto ou numa faixa da borda, mas pode estar em qualquer lugar, girado \
ou de cabeça para baixo. Às vezes é dividido em dois blocos vizinhos — nesse caso \
considere os dois juntos como um carimbo só.
- Não confunda carimbo com legenda de material, tabela de esquadrias ou nota \
construtiva. Se a imagem não tem carimbo, diga carimbo_encontrado=false e deixe \
todos os campos vazios.
- Transcreva LITERALMENTE o que está escrito, inclusive grafia antiga e \
abreviação. Não corrija, não complete, não deduza nome de arquiteto famoso a \
partir de fragmento.
- Campo que não existe no carimbo: valor vazio, confiança 0. Campo que existe \
mas está ilegível: valor vazio, confiança 0, e cite na nota.
- Confiança é sua de verdade: use 0.9+ só para texto nítido e inequívoco; use \
abaixo de 0.6 sempre que tiver chutado caractere.
- Manuscrito conta: leia se der, mas baixe a confiança.

Devolva a resposta chamando a ferramenta registrar_carimbo."""

INSTRUCOES_RECORTE = """Esta imagem já é um RECORTE aproximado da região do \
carimbo, em alta resolução. Leia os campos com atenção máxima. Se este recorte \
não contiver um carimbo de fato, diga carimbo_encontrado=false. \
Em regiao_carimbo, informe onde o carimbo está DENTRO deste recorte."""


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
        self._cliente = anthropic.Anthropic(api_key=chave, timeout=config.timeout_segundos)
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
                    max_tokens=2000,
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


def _para_leitura(entrada: dict[str, Any], leitura: Leitura) -> Leitura:
    """Converte a saída da ferramenta em Leitura, sem confiar na forma."""
    leitura.carimbo_encontrado = bool(entrada.get("carimbo_encontrado", False))
    leitura.nota_ia = str(entrada.get("nota", "") or "")
    try:
        leitura.rotacao = int(entrada.get("rotacao", 0) or 0) % 360
    except (TypeError, ValueError):
        leitura.rotacao = 0

    regiao = entrada.get("regiao_carimbo")
    if isinstance(regiao, (list, tuple)) and len(regiao) == 4:
        try:
            valores = [max(0.0, min(1.0, float(v))) for v in regiao]
            if valores[2] > valores[0] and valores[3] > valores[1]:
                leitura.regiao = (valores[0], valores[1], valores[2], valores[3])
        except (TypeError, ValueError):
            pass

    for campo in CAMPOS:
        bruto = entrada.get(campo.nome) or {}
        if not isinstance(bruto, dict):
            bruto = {"valor": str(bruto), "confianca": 0.0}
        valor = str(bruto.get("valor", "") or "").strip()
        try:
            confianca = max(0.0, min(1.0, float(bruto.get("confianca", 0.0) or 0.0)))
        except (TypeError, ValueError):
            confianca = 0.0
        leitura.valores[campo.nome] = valor
        leitura.confiancas[campo.nome] = confianca if valor else 0.0
    return leitura


def _melhor(a: Leitura, b: Leitura) -> Leitura:
    """Entre dois passes, fica com o que leu mais campos com mais confiança."""
    def pontos(l: Leitura) -> tuple[int, int, float]:
        preenchidos = sum(1 for v in l.valores.values() if v)
        return (int(l.carimbo_encontrado), preenchidos, l.confianca_media)

    return b if pontos(b) > pontos(a) else a


class LeitorDeCarimbo:
    """Orquestra os passes de leitura de uma prancha."""

    def __init__(self, config: Config, cliente: ClienteAPI) -> None:
        self.config = config
        self.cliente = cliente
        self.ferramenta = esquema_ferramenta()

    def _ler_imagem(self, img: Image.Image, instrucao_extra: str = "") -> tuple[Leitura, int, int]:
        env = img_mod.para_envio(
            img, self.config.lado_maximo_envio, self.config.qualidade_jpeg_envio
        )
        texto = instrucao_extra or "Leia o carimbo desta prancha."
        mensagens = [{"role": "user", "content": [_bloco_imagem(env), {"type": "text", "text": texto}]}]
        entrada, t_in, t_out = self.cliente.chamar(mensagens, self.ferramenta, INSTRUCOES)
        return _para_leitura(entrada, Leitura()), t_in, t_out

    def _ganho_de_resolucao(self, original: Image.Image, regiao: img_mod.Caixa) -> float:
        """Quantas vezes mais nítido o recorte fica no 2º passe.

        Reler custa uma chamada inteira. Se a região do carimbo já foi enviada
        praticamente na resolução máxima no 1º passe, o 2º passe não traz pixel
        novo nenhum — só conta o mesmo texto de novo e cobra por isso.
        """
        lado_maximo = self.config.lado_maximo_envio
        maior_original = max(original.size)
        fator = min(1.0, lado_maximo / maior_original) if maior_original else 1.0
        x0, y0, x1, y1 = regiao
        recorte_px = max(abs(x1 - x0) * original.width, abs(y1 - y0) * original.height)
        if recorte_px <= 0:
            return 0.0
        enviado_no_1o = recorte_px * fator
        enviado_no_2o = min(lado_maximo, recorte_px)
        return enviado_no_2o / max(1.0, enviado_no_1o)

    def ler(self, caminho: Path, regiao_sugerida: img_mod.Caixa | None = None) -> Leitura:
        """Lê uma prancha. `regiao_sugerida` vem do cache da pasta."""
        resultado = Leitura(arquivo=caminho.name)
        try:
            original = img_mod.abrir(caminho)
        except Exception as erro:  # noqa: BLE001
            resultado.erro = f"não foi possível abrir a imagem: {erro}"
            _log.error("%s: %s", caminho.name, resultado.erro)
            return resultado

        try:
            # Passe 0 — atalho pelo cache de região da pasta.
            if regiao_sugerida and self.config.usar_cache_de_regiao:
                recorte = img_mod.recortar(original, regiao_sugerida, self.config.margem_recorte)
                leitura, t_in, t_out = self._ler_imagem(recorte, INSTRUCOES_RECORTE)
                resultado.tokens_entrada += t_in
                resultado.tokens_saida += t_out
                resultado.passes += 1
                if leitura.carimbo_encontrado and leitura.confianca_media >= self.config.confianca_minima_para_aceitar:
                    return _fundir(resultado, leitura, regiao_sugerida)
                resultado.cache_falhou = True
                _log.info("%s: cache de região não bastou, indo pela página inteira.", caminho.name)

            # Passe 1 — página inteira reduzida: localiza e já lê.
            leitura, t_in, t_out = self._ler_imagem(original)
            resultado.tokens_entrada += t_in
            resultado.tokens_saida += t_out
            resultado.passes += 1

            precisa_segundo_passe = (
                leitura.carimbo_encontrado
                and leitura.regiao is not None
                and leitura.confianca_media < self.config.confianca_minima_para_aceitar
                and self._ganho_de_resolucao(original, leitura.regiao) >= self.config.ganho_minimo_2o_passe
            )
            if not precisa_segundo_passe:
                return _fundir(resultado, leitura, leitura.regiao)

            # Passe 2 — recorte na resolução ORIGINAL, onde o texto está inteiro.
            ganho = self._ganho_de_resolucao(original, leitura.regiao)
            _log.info(
                "%s: confiança %.2f abaixo do limiar, relendo o recorte em alta (%.1fx).",
                caminho.name, leitura.confianca_media, ganho,
            )
            resultado.fez_segundo_passe = True
            resultado.confianca_antes_do_2o = leitura.confianca_media
            resultado.ganho_de_resolucao = ganho
            recorte = img_mod.recortar(original, leitura.regiao, self.config.margem_recorte)
            if leitura.rotacao:
                recorte = img_mod.girar(recorte, leitura.rotacao)
            fino, t_in, t_out = self._ler_imagem(recorte, INSTRUCOES_RECORTE)
            resultado.tokens_entrada += t_in
            resultado.tokens_saida += t_out
            resultado.passes += 1
            return _fundir(resultado, _melhor(leitura, fino), leitura.regiao)

        except Exception as erro:  # noqa: BLE001
            resultado.erro = str(erro)
            _log.error("%s: %s", caminho.name, erro)
            return resultado
        finally:
            original.close()


def _fundir(base: Leitura, leitura: Leitura, regiao: img_mod.Caixa | None) -> Leitura:
    """Copia o resultado da leitura para o acumulador, preservando os contadores.

    Os campos de medição (passes, tokens, 2º passe) já vivem em `base` e não
    podem ser sobrescritos pelo objeto que veio de uma chamada isolada.
    """
    base.valores = leitura.valores
    base.confiancas = leitura.confiancas
    base.carimbo_encontrado = leitura.carimbo_encontrado
    base.rotacao = leitura.rotacao
    base.nota_ia = leitura.nota_ia
    # A região que interessa guardar é a da PÁGINA, não a de dentro do recorte.
    base.regiao = regiao
    return base


def consolidar_grupo(
    cliente: ClienteAPI, nome_grupo: str, leituras: list[Leitura]
) -> tuple[dict[str, str], int, int]:
    """Normaliza os campos de projeto de um grupo. Chamada só de texto, barata.

    É isto que substitui a quarentena, o `unificar_grafias` e a moda de ano do
    app antigo: o modelo vê as N leituras lado a lado e decide a grafia boa.
    """
    linhas = []
    for leitura in leituras:
        campos = ", ".join(
            f"{nome}={leitura.valores.get(nome, '')!r}({leitura.confiancas.get(nome, 0):.2f})"
            for nome in ("projeto", "cliente", "arquiteto", "escritorio", "endereco", "cidade", "uf", "ano")
            if leitura.valores.get(nome)
        )
        linhas.append(f"- {leitura.arquivo}: {campos or '(nada legível)'}")

    texto = (
        f"Estas pranchas foram agrupadas como o projeto {nome_grupo!r}. "
        "Cada linha traz o que foi lido do carimbo daquela prancha, com a "
        "confiança de cada campo entre parênteses.\n\n"
        + "\n".join(linhas)
        + "\n\nDefina a grafia canônica de cada campo do projeto. Regras: "
        "prefira a grafia que aparece em mais pranchas e com maior confiança; "
        "trate variações de acento, abreviação e erro de leitura como a mesma "
        "coisa; NÃO invente informação que não apareça em nenhuma prancha; "
        "e aponte pranchas que pareçam ser de outro projeto."
    )
    ferramenta = esquema_consolidacao()
    entrada, t_in, t_out = cliente.chamar(
        [{"role": "user", "content": [{"type": "text", "text": texto}]}],
        ferramenta,
        "Você organiza acervos de arquitetura e normaliza metadados lidos de carimbos.",
    )
    return {k: str(v) for k, v in entrada.items() if isinstance(v, (str, int, float))}, t_in, t_out
