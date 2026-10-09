"""Livro de registro: tudo que o CV2 fez, arquivo por arquivo.

Fica NO QNAP (`ACERVOS_CAMP/_campvision/registro/AAAA-MM.jsonl` + `.csv`),
append-only, para sobreviver ao servidor. Formato definido no contrato com o
painel (docs/contrato-painel.md, item 4b) — mudou aqui, muda lá.

Nunca se reescreve uma linha. Correção é linha nova com `acao: refeito`.
"""

from __future__ import annotations

import csv
import json
import logging
from datetime import datetime
from pathlib import Path

from .config import VERSAO_BUILD

_log = logging.getLogger("cv2.livro")

PASTA = Path("_campvision") / "registro"

ACOES = (
    "recebido", "copiado", "conferido", "renomeado", "lido", "exif_gravado",
    "apagado_original", "erro", "refeito", "legado", "decisao", "relido",
)

CAMPOS = (
    "quando", "acao", "lote_id", "codigo_projeto", "codigo_documento",
    "arquivo_origem", "arquivo_destino", "nome_original", "tamanho", "sha256",
    "operador", "estacao", "versao_cv2", "detalhe",
)


class Livro:
    def __init__(self, raiz_acervo: Path, reserva: Path | None = None) -> None:
        """`reserva` (pasta local) recebe as linhas se o QNAP estiver fora."""
        self.pasta = raiz_acervo / PASTA
        self.reserva = reserva

    def _arquivos(self, quando: datetime) -> tuple[Path, Path]:
        nome = quando.strftime("%Y-%m")
        return self.pasta / f"{nome}.jsonl", self.pasta / f"{nome}.csv"

    def anotar(self, acao: str, **campos) -> dict:
        if acao not in ACOES:
            raise ValueError(f"ação desconhecida no livro: {acao}")
        agora = datetime.now().astimezone()
        linha = {c: "" for c in CAMPOS}
        linha.update({k: v for k, v in campos.items() if k in CAMPOS and v is not None})
        linha["quando"] = agora.isoformat(timespec="seconds")
        linha["acao"] = acao
        linha["versao_cv2"] = VERSAO_BUILD
        jsonl, csv_ = self._arquivos(agora)
        try:
            self.pasta.mkdir(parents=True, exist_ok=True)
            self._gravar(jsonl, csv_, linha)
        except OSError as erro:
            _log.error("Livro no QNAP indisponível (%s); gravando na reserva local.", erro)
            if self.reserva:
                self.reserva.mkdir(parents=True, exist_ok=True)
                self._gravar(self.reserva / jsonl.name, self.reserva / csv_.name, linha)
        return linha

    @staticmethod
    def _gravar(jsonl: Path, csv_: Path, linha: dict) -> None:
        with jsonl.open("a", encoding="utf-8") as f:
            f.write(json.dumps(linha, ensure_ascii=False) + "\n")
        novo = not csv_.exists()
        with csv_.open("a", encoding="utf-8-sig" if novo else "utf-8", newline="") as f:
            escritor = csv.DictWriter(f, fieldnames=CAMPOS)
            if novo:
                escritor.writeheader()
            escritor.writerow(linha)

    def descarregar_reserva(self) -> int:
        """Passa para o QNAP as linhas que ficaram na reserva local."""
        if not self.reserva or not self.reserva.is_dir():
            return 0
        movidas = 0
        for arquivo in sorted(self.reserva.glob("*.jsonl")):
            try:
                linhas = [json.loads(l) for l in arquivo.read_text(encoding="utf-8").splitlines() if l.strip()]
                self.pasta.mkdir(parents=True, exist_ok=True)
                destino = self.pasta / arquivo.name
                for linha in linhas:
                    self._gravar(destino, destino.with_suffix(".csv"), linha)
                arquivo.unlink()
                arquivo.with_suffix(".csv").unlink(missing_ok=True)
                movidas += len(linhas)
            except (OSError, json.JSONDecodeError) as erro:
                _log.warning("Reserva do livro não descarregada: %s", erro)
                break
        return movidas

    def todas(self):
        if not self.pasta.is_dir():
            return
        for arquivo in sorted(self.pasta.glob("*.jsonl")):
            for texto in arquivo.read_text(encoding="utf-8").splitlines():
                try:
                    yield json.loads(texto)
                except json.JSONDecodeError:
                    continue

    def historico(self, termo: str) -> list[dict]:
        """Linhas que mencionam o termo (nome de arquivo, código, lote ou data)."""
        termo_baixo = termo.lower()
        achadas = []
        for linha in self.todas():
            if termo_baixo in (linha.get("quando") or "")[:10]:
                achadas.append(linha)
                continue
            campos = ("lote_id", "codigo_projeto", "codigo_documento", "arquivo_origem",
                      "arquivo_destino", "nome_original")
            if any(termo_baixo in str(linha.get(c, "")).lower() for c in campos):
                achadas.append(linha)
        return achadas
