"""Relatório diário do vigia.

Gerado uma vez por dia na hora configurada, a partir do diário de eventos.
Sempre escreve o arquivo; o email é opcional e nunca derruba o vigia se falhar.
"""

from __future__ import annotations

import logging
import os
import smtplib
from datetime import date
from email.message import EmailMessage
from pathlib import Path

from .config import Config
from .eventos import Evento

_log = logging.getLogger("cv2.relatorio")


def montar_texto(eventos: list[Evento], dia: date) -> str:
    if not eventos:
        return f"CAMP Vision 2 — {dia:%d/%m/%Y}\n\nNenhum projeto processado hoje."

    pranchas = sum(e.pranchas for e in eventos)
    carimbos = sum(e.com_carimbo for e in eventos)
    erros = sum(e.erros for e in eventos)
    revisar = sum(e.campos_a_revisar for e in eventos)
    custo = sum(e.custo_usd for e in eventos)
    minutos = sum(e.duracao_segundos for e in eventos) / 60
    falhas = [e for e in eventos if e.falha]

    linhas = [
        f"CAMP Vision 2 — {dia:%d/%m/%Y}",
        "=" * 46,
        f"Projetos processados: {len(eventos)}",
        f"Pranchas lidas:       {pranchas}",
        f"Com carimbo:          {carimbos}"
        + (f" ({carimbos / pranchas * 100:.0f}%)" if pranchas else ""),
        f"Erros de leitura:     {erros}",
        f"Campos a revisar:     {revisar}",
        f"Tempo de máquina:     {minutos:.0f} min",
        f"Custo do dia:         US$ {custo:.2f}",
        "",
        "Por projeto:",
    ]
    for e in sorted(eventos, key=lambda e: e.quando):
        marca = "FALHOU" if e.falha else f"{e.com_carimbo}/{e.pranchas} com carimbo"
        linhas.append(f"  {e.quando[11:16]}  {e.projeto:<38} {marca}")
        if e.falha:
            linhas.append(f"            └ {e.falha[:120]}")

    if falhas:
        linhas += ["", f"ATENÇÃO: {len(falhas)} projeto(s) falharam e não avançaram de status."]
    if revisar:
        linhas += ["", f"{revisar} campo(s) com confiança baixa esperam revisão nas planilhas."]
    return "\n".join(linhas)


def montar_html(texto: str, dia: date) -> str:
    corpo = texto.replace("&", "&amp;").replace("<", "&lt;")
    return (
        "<html><body style=\"font-family:-apple-system,Segoe UI,sans-serif\">"
        f"<h2 style=\"color:#1F3864\">CAMP Vision 2 — {dia:%d/%m/%Y}</h2>"
        f"<pre style=\"font-family:ui-monospace,Menlo,monospace;font-size:13px;"
        f"background:#f6f7f9;padding:14px;border-radius:8px\">{corpo}</pre>"
        "</body></html>"
    )


def escrever(eventos: list[Evento], pasta: Path, dia: date) -> Path:
    texto = montar_texto(eventos, dia)
    pasta.mkdir(parents=True, exist_ok=True)
    destino = pasta / f"relatorio-{dia:%Y-%m-%d}.txt"
    destino.write_text(texto, encoding="utf-8")
    (pasta / f"relatorio-{dia:%Y-%m-%d}.html").write_text(
        montar_html(texto, dia), encoding="utf-8"
    )
    _log.info("Relatório diário escrito: %s", destino.name)
    return destino


def enviar_email(config: Config, texto: str, dia: date) -> str:
    """Devolve string vazia se enviou, ou a mensagem de erro. Nunca levanta."""
    if not config.email_ativo:
        return "envio de email desligado"
    senha = os.environ.get("CAMPVISION_SMTP_SENHA", "")
    faltando = [
        nome
        for nome, valor in (
            ("smtp_servidor", config.smtp_servidor),
            ("email_para", config.email_para),
            ("email_de", config.email_de),
            ("CAMPVISION_SMTP_SENHA", senha),
        )
        if not valor
    ]
    if faltando:
        return f"configuração de email incompleta: {', '.join(faltando)}"

    mensagem = EmailMessage()
    mensagem["Subject"] = f"CAMP Vision 2 — {dia:%d/%m/%Y}"
    mensagem["From"] = config.email_de
    mensagem["To"] = config.email_para
    mensagem.set_content(texto)
    mensagem.add_alternative(montar_html(texto, dia), subtype="html")

    try:
        with smtplib.SMTP(config.smtp_servidor, config.smtp_porta, timeout=60) as servidor:
            servidor.starttls()
            servidor.login(config.smtp_usuario or config.email_de, senha)
            servidor.send_message(mensagem)
    except Exception as erro:  # noqa: BLE001 - email nunca derruba o vigia
        _log.error("Falha ao enviar o relatório por email: %s", erro)
        return str(erro)
    _log.info("Relatório enviado para %s", config.email_para)
    return ""
