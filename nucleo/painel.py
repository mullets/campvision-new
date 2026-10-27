"""Cliente HTTP do painel de administração (contrato: docs/contrato-painel.md).

Regras:
- Os arquivos no QNAP são a fonte da verdade. HTTP só informa e acelera.
- Nada aqui pode travar o vigia: timeout curto, nenhuma exceção vaza.
- Avisos que não chegaram ficam numa fila em disco e são reenviados; basta o
  mais recente de cada projeto.
- Só `/reservar` é bloqueante para o fluxo (sem número P não há projeto novo),
  e mesmo assim o lote só ESPERA — nunca recebe código inventado.

Sem dependência externa: urllib da biblioteca padrão.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

_log = logging.getLogger("cv2.painel")

# Aviso que o painel recusa com 5xx volta para a fila, mas não para sempre (C7).
MAX_TENTATIVAS_AVISO = 20
# 4xx é recusa definitiva (repetir não adianta), menos estes: o painel só pediu para esperar.
_TRANSITORIOS = (408, 425, 429)
# Rotas do painel que exigem usuário admin (cookie de sessão), não o token da estação.
_ROTAS_ADMIN = ("/api/itens/mover", "/api/projetos/")


def _numero_do_projeto(dados: dict) -> str:
    """numero_projeto (P0001) do corpo da reserva: no topo ou dentro de `projeto`."""
    projeto = dados.get("projeto") if isinstance(dados.get("projeto"), dict) else {}
    for origem in (dados, projeto):
        for chave in ("numero_projeto", "numero"):
            valor = origem.get(chave)
            if valor not in (None, "", 0):
                return str(valor)
    return ""


class Painel:
    def __init__(self, url: str, token: str = "", pasta_estado: Path | None = None,
                 timeout: float = 8.0) -> None:
        self.url = (url or "").rstrip("/")
        self.token = token
        self.timeout = timeout
        self.fila = (pasta_estado / "painel_pendentes.json") if pasta_estado else None
        self.ultimo_erro = ""
        self.ultima_reserva: dict = {"pendente": False, "existente": False, "mensagem": ""}
        self.ultimo_ok: str = ""
        # Sessão de ADMIN do painel (cookie camp_sessao), só para as rotas de mover/mesclar
        # (CAMP-009). O token da estação NÃO vale nelas. Vazio = o CV2 não chama essas rotas.
        self.sessao = os.environ.get("CAMP_PAINEL_SESSAO", "")

    @property
    def ligado(self) -> bool:
        return bool(self.url)

    # ------------------------------------------------------------ baixo nível
    def _chamar(self, metodo: str, caminho: str, corpo: dict | None = None):
        """Devolve (status_http, json|None). Status 0 = sem conexão."""
        if not self.ligado:
            return 0, None
        dados = json.dumps(corpo, ensure_ascii=False).encode("utf-8") if corpo is not None else None
        pedido = urllib.request.Request(self.url + caminho, data=dados, method=metodo)
        pedido.add_header("Accept", "application/json")
        if dados is not None:
            pedido.add_header("Content-Type", "application/json; charset=utf-8")
        if self.token:
            pedido.add_header("X-Camp-Token", self.token)
        if self.sessao and caminho.startswith(_ROTAS_ADMIN):
            pedido.add_header("Cookie", f"camp_sessao={self.sessao}")
        try:
            with urllib.request.urlopen(pedido, timeout=self.timeout) as resposta:
                bruto = resposta.read().decode("utf-8") or "null"
                self.ultimo_ok = datetime.now().isoformat(timespec="seconds")
                self.ultimo_erro = ""
                try:
                    return resposta.status, json.loads(bruto)
                except json.JSONDecodeError:
                    return resposta.status, None
        except urllib.error.HTTPError as erro:
            self.ultimo_erro = f"{metodo} {caminho}: HTTP {erro.code}"
            try:
                return erro.code, json.loads(erro.read().decode("utf-8") or "null")
            except (json.JSONDecodeError, OSError):
                return erro.code, None
        except (urllib.error.URLError, OSError, TimeoutError) as erro:
            self.ultimo_erro = f"{metodo} {caminho}: {getattr(erro, 'reason', erro)}"
            return 0, None

    # ------------------------------------------------------------ contrato
    def contexto(self, fundo: str = "") -> dict | None:
        caminho = "/api/estacoes/contexto" + (f"?fundo={fundo}" if fundo else "")
        status, dados = self._chamar("GET", caminho)
        return dados if status == 200 else None

    def reservar(self, fundo_codigo: str, titulo: str, chave_reserva: str, **extra) -> str | None:
        """Reserva o projeto no painel e devolve o código (F002-P0002) ou None.

        A mesma chave devolve o mesmo projeto: repetir depois de timeout é seguro.
        """
        corpo = {"fundo_codigo": fundo_codigo, "titulo": titulo, "chave_reserva": chave_reserva}
        corpo.update({k: v for k, v in extra.items() if v not in (None, "")})
        status, dados = self._chamar("POST", "/api/estacoes/projetos/reservar", corpo)
        # Contrato §6.2 do painel: 202 = achou projeto parecido e abriu decisão para
        # uma pessoa (sem código; tentar de novo com a mesma chave); 200 com
        # existente=true = "é o mesmo projeto", o material entra no que já existe.
        self.ultima_reserva = {"pendente": False, "existente": False, "mensagem": ""}
        if status == 410:
            # C7: o painel descartou este projeto/lote. Não é erro nem espera: o CV2
            # marca o lote como descartado e para de chamar.
            mensagem = str((dados or {}).get("mensagem") if isinstance(dados, dict) else "") or \
                "lote descartado no painel"
            self.ultima_reserva = {"pendente": False, "existente": False, "descartado": True,
                                   "mensagem": mensagem}
            self.ultimo_erro = mensagem
            _log.warning("Reserva descartada pelo painel (410): %s", mensagem)
            return None
        if status == 202 and isinstance(dados, dict):
            mensagem = str(dados.get("mensagem") or "aguardando decisão no painel (projeto parecido)")
            self.ultima_reserva = {"pendente": True, "existente": False, "mensagem": mensagem,
                                   "decisao_id": dados.get("decisao_id")}
            self.ultimo_erro = mensagem
            _log.info("Reserva pendente: %s", mensagem)
            return None
        if status not in (200, 201) or not isinstance(dados, dict):
            if status:
                _log.warning("Reserva recusada (%s): %s", status, dados)
            return None
        codigo = dados.get("codigo") or (dados.get("projeto") or {}).get("codigo")
        if not codigo and dados.get("numero"):
            codigo = f"{fundo_codigo}-P{int(dados['numero']):04d}"
        existente = bool(dados.get("existente"))
        if existente and not _numero_do_projeto(dados):
            # C2: o painel só reaproveita projeto por número quando o corpo traz
            # `numero_projeto`. Sem ele a resposta é suspeita (painel antigo que
            # casou pelo "P0001 - " da estação): segura o lote em vez de misturar.
            mensagem = ("painel devolveu projeto existente sem numero_projeto — "
                        "lote em espera até o painel decidir")
            self.ultima_reserva = {"pendente": True, "existente": False, "mensagem": mensagem,
                                   "decisao_id": dados.get("decisao_id")}
            self.ultimo_erro = mensagem
            _log.warning("Reserva %s ignorada: %s (codigo %s)", fundo_codigo, mensagem, codigo)
            return None
        self.ultima_reserva["existente"] = existente
        return str(codigo) if codigo else None

    def pedidos_releitura(self, estacao_id: str) -> list[dict]:
        """Pedidos de releitura abertos no painel. Painel sem o recurso (404) = lista vazia."""
        status, dados = self._chamar(
            "GET", f"/api/estacoes/pedidos-releitura?estacao={estacao_id}&estacao_id={estacao_id}")
        if status != 200:
            return []
        if isinstance(dados, dict):
            dados = dados.get("pedidos") or []
        return [d for d in dados if isinstance(d, dict) and d.get("id")] if isinstance(dados, list) else []

    def releitura_iniciada(self, pedido_id, estacao_id: str) -> bool:
        """True = pode ler. 409 = pedido já encerrado (cancelado): NÃO ler."""
        status, _ = self._chamar("POST", f"/api/estacoes/pedidos-releitura/{pedido_id}/iniciado",
                                 {"estacao": estacao_id})
        if status == 409:
            return False
        return True  # painel antigo sem a rota (404) ou fora do ar: lê assim mesmo

    def releitura_concluida(self, pedido_id, resumo: dict, ok: bool = True, mensagem: str = "") -> bool:
        corpo = {"ok": ok, "mensagem": (mensagem or "")[:500], **resumo}
        status, _ = self._chamar("POST", f"/api/estacoes/pedidos-releitura/{pedido_id}/concluido", corpo)
        return 200 <= status < 300

    def destinos(self) -> list[dict] | None:
        """UX-43: GET /api/campvision/destinos — pastas que o painel decidiu que são de outro projeto.

        Lista (talvez vazia) quando o painel respondeu; None quando não deu para saber (painel antigo
        sem a rota = 404, fora do ar, resposta estranha). Nunca levanta."""
        status, dados = self._chamar("GET", "/api/campvision/destinos")
        if status != 200 or not isinstance(dados, dict) or not isinstance(dados.get("destinos"), list):
            return None
        return [d for d in dados["destinos"] if isinstance(d, dict) and d.get("id") is not None]

    def reidentificar_lote(self, lote_id, destino: str, simular: bool = True,
                           motivo: str = "") -> tuple[int, dict | None]:
        """POST /api/campvision/reidentificar (rede local/X-Camp-Token, CAMP-009) com o lote inteiro.
        `simular` é o padrão do painel; aplicar só com `campvision.reidentificar_aplicar` = 1 lá (senão 403)."""
        return self._chamar("POST", "/api/campvision/reidentificar", {
            "simular": bool(simular), "motivo": motivo,
            "movimentos": [{"lote_id": lote_id, "destino": destino}]})

    def mover_folhas(self, destino: str, itens: list[str], motivo: str = "") -> tuple[int, dict | None]:
        """POST /api/itens/mover (admin). Painel antigo/sem sessão: (status, corpo) para registrar; nunca levanta."""
        return self._chamar("POST", "/api/itens/mover", {"itens": itens, "destino": destino, "motivo": motivo})

    def mesclar_projeto(self, origem: str, destino: str, motivo: str = "") -> tuple[int, dict | None]:
        """POST /api/projetos/{origem}/mesclar-em (admin)."""
        return self._chamar("POST", f"/api/projetos/{origem}/mesclar-em", {"destino": destino, "motivo": motivo})

    def heartbeat(self, dados: dict) -> bool:
        status, resposta = self._chamar("POST", "/api/estacoes/heartbeat", dados)
        if not 200 <= status < 300:
            _log.warning("Heartbeat recusado (%s): %s", status or "sem conexão",
                         resposta if resposta else self.ultimo_erro)
        return 200 <= status < 300

    def aviso(self, codigo: str, pasta: str, status_projeto: str) -> bool:
        """Pede ao painel para reler só esta pasta. Falhou: guarda para depois."""
        corpo = {"codigo": codigo, "pasta": pasta, "status": status_projeto,
                 "em": datetime.now().isoformat(timespec="seconds")}
        status, dados = self._chamar("POST", "/api/campvision/aviso", corpo)
        veredito = self._veredito_do_aviso(corpo, status, dados)
        if veredito == "reenfileirar":
            self._enfileirar(corpo)
        return veredito == "ok"

    @staticmethod
    def _veredito_do_aviso(corpo: dict, status: int, dados) -> str:
        """'ok' | 'descartar' | 'reenfileirar' (C7)."""
        if 200 <= status < 300:
            if isinstance(dados, dict) and dados.get("ok") is False:
                _log.warning("Aviso de %s aceito mas com ok:false — %s", corpo.get("codigo"),
                             dados.get("motivo") or dados)
                return "descartar"
            return "ok"
        if status == 0 or status >= 500 or status in _TRANSITORIOS:
            return "reenfileirar"
        _log.warning("Aviso de %s recusado pelo painel (HTTP %s): %s — não será reenviado",
                     corpo.get("codigo"), status, dados)
        return "descartar"

    # ------------------------------------------------------------ fila
    def _ler_fila(self) -> dict:
        if not self.fila or not self.fila.exists():
            return {}
        try:
            dados = json.loads(self.fila.read_text(encoding="utf-8"))
            return dados if isinstance(dados, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _gravar_fila(self, fila: dict) -> None:
        if not self.fila:
            return
        try:
            self.fila.parent.mkdir(parents=True, exist_ok=True)
            # Atômico: temporário + rename. Queda no meio nunca deixa a fila pela metade.
            temporario = self.fila.with_name(f".{self.fila.name}.tmp")
            temporario.write_text(json.dumps(fila, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(temporario, self.fila)
        except OSError as erro:
            _log.warning("Fila de avisos não gravada: %s", erro)

    def _enfileirar(self, corpo: dict) -> None:
        fila = self._ler_fila()
        fila[corpo["codigo"]] = corpo  # só o mais recente de cada projeto
        self._gravar_fila(fila)

    def pendentes(self) -> int:
        return len(self._ler_fila())

    def reenviar(self) -> int:
        """Tenta de novo os avisos guardados. Devolve quantos entraram."""
        fila = self._ler_fila()
        if not fila or not self.ligado:
            return 0
        enviados = 0
        for codigo, corpo in list(fila.items()):
            enviavel = {k: v for k, v in corpo.items() if not k.startswith("_")}
            status, dados = self._chamar("POST", "/api/campvision/aviso", enviavel)
            veredito = self._veredito_do_aviso(enviavel, status, dados)
            if veredito == "ok":
                fila.pop(codigo)
                enviados += 1
            elif veredito == "descartar":
                fila.pop(codigo)
            elif status == 0:
                break  # painel fora: não insiste nos outros agora
            else:  # 5xx: conta a tentativa e desiste no limite
                corpo["_tentativas"] = int(corpo.get("_tentativas", 0)) + 1
                if corpo["_tentativas"] >= MAX_TENTATIVAS_AVISO:
                    _log.warning("Aviso de %s descartado após %d tentativas (HTTP %s)",
                                 codigo, corpo["_tentativas"], status)
                    fila.pop(codigo)
        self._gravar_fila(fila)
        return enviados


class PainelDesligado(Painel):
    """Quando não há URL configurada. Tudo vira no-op."""

    def __init__(self) -> None:
        super().__init__("")
