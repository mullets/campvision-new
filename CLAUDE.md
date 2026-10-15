# CLAUDE.md — campvision-new (CAMP Vision 2, "CV2")

Lê a ENTRADA (`100 - Scanners`, escrita pelas estações), faz leitura do carimbo por modelo de visão, EXIF, organização e grava os JSONs na pasta final (`ACERVOS_CAMP/<Fundo>/01 - Projetos/<Fxxx-Pxxxx - Nome>/`), que o painel lê. Roda como serviço systemd no Ubuntu `.40`. O `campvision2` é o app antigo: não usar.

## Contratos (leia antes de mexer)
- **Contrato único dos 5 sistemas: `camp-painel/docs/CONTRATO_UNICO.md`** (mapa, quem produz/consome cada arquivo e rota — ex.: `status.json`: Windows produz, CV2 consome; o `status.json` do CV2 o painel consome —, versionamento, legado). Está em outro repo: leia lá, não copie para cá.
- **CV2 ↔ painel (fonte): `docs/contrato-painel.md`**, versão em `CONTRATO` (`nucleo/config.py`, hoje 2.4); exemplos reais em `docs/contrato/exemplos/`, regenerados por `python -m tests.contrato_exemplos`. O painel guarda cópia literal em `camp-painel/tests/contrato/`.
- Guia completo do funcionamento: `docs/COMO_FUNCIONA.md`.
- **Mudou campo/rota? Suba `CONTRATO`, regenere os exemplos e atualize `docs/contrato-painel.md` NO MESMO COMMIT. Todo campo novo é OPCIONAL** (estações e painel podem estar na versão antiga).
- Board oficial para tickets transversais: "Camp todos sistemas" (Trello; só leia, escreva se mandarem).

## Como testar
```
/tmp/cv2venv/bin/python -m unittest discover -s tests -t .   # suíte inteira (~40 s); venv do projeto: .venv
python -m unittest tests.test_fotos                          # um módulo
python -m tests.contrato_exemplos                            # regenera exemplos do contrato
```
Mudou `Config`? Regere `config.exemplo.json` (`Config.modelo_json()`), senão `TestConfigExemplo` falha. Todo ticket tem teste; **teste novo deve FALHAR sem a correção** (prove). Testes usam `ClienteFalso`: nenhuma chamada real à API.

## Fluxo de trabalho
1. Sempre num branch `claude/<nome>`; um commit por ticket com `CAMP-0xx: …` no título.
2. Suíte inteira antes de cada push; `git push -u origin <branch>`. Confira `git rev-parse --show-toplevel` e use `git -C <repo>`; nada de script de commit compartilhado.
3. Quem abre PR e faz merge é o coordenador/Rafa.
4. Operação destrutiva ou que depende de dado real (apagar legado, reidentificar, regravar) entra atrás de flag DESLIGADA por padrão ou em modo simular, compatível com o outro lado.

## Armadilhas de auto-deploy
- **Auto-update horário:** o vigia puxa `origin/main` a cada `intervalo_atualizacao_minutos` (60) via `atualizar.sh --auto`, testa a versão nova e se reinicia sozinho (só ocioso, situação "vigiando"). **Tudo que chega ao `main` vai para o servidor real em até 1 h.** As estações atualizam a cada ~5 min e o painel no push: ordem de deploy importa; nunca quebre a leitura dos arquivos das estações antigas (`status`/`etapa` `enviado_windows`/`enviado_mac`, `info_lote_*_mac.json`, `manifesto.json`).
- **Rollback `--boot`:** o systemd roda `atualizar.sh --boot` (`ExecStartPre`) a cada subida; se a versão nova não confirmar "subi bem" em `CAMPVISION_BOOT_MAX` subidas (3), faz `git reset --hard` para a anterior, grava `versao_fixada` (a auto-atualização para de mexer) e registra em `~/.campvision2/rollbacks.log`. Consequências: erro de import/boot no `main` = servidor volta sozinho e FICA numa versão velha até alguém apagar `versao_fixada`; versão que só falha depois da 1ª rodada não é pega. O mesmo vale para `--voltar`/`--versao`.
- `atualizar.sh` roda a suíte na versão nova e desfaz se falhar: suíte vermelha no `main` = nenhuma atualização.
- O CV2 NUNCA inventa código de fundo nem número P (vem do painel `/reservar`); só o painel decide código.
- `--reidentificar`, `--consertar-acervo`, `--reclassificar` mexem no acervo: sem `--sim` só mostram o plano; `--reidentificar --simular` força simulação mesmo com `--sim`; as rotas de mover/mesclar do painel são de admin (`CAMP_PAINEL_SESSAO`), senão fica o roteiro em `catalogacao/reidentificacao_painel.json`.
- Flags de legado (desligadas): `desligar_modo_antigo` (modo `pasta_vigiada`/`fase`), `orientar_prancha_pelo_modelo`. Não remova código legado sem o Rafa confirmar que nenhum projeto depende.

## NUNCA
- Commitar/pushar no `main`, abrir PR, rodar `atualizar.sh`, reiniciar o serviço, tocar no QNAP/painel reais ou rodar `--sim` em produção em teste.
- Gravar chave Anthropic, token ou senha em log, config versionada, commit ou resposta (vêm de `~/.campvision2/ambiente`).
- Apagar ou mover original da entrada fora do fluxo (`quarentena_entrada`); apagar dados do acervo.
- Renomear o que está decidido no contrato/painel (séries, S07, Praça × Parque são decisão do Rafa, CAMP-045).
