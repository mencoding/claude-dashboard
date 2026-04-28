# claude-dashboard

TUI de monitoramento do Claude Code — sessões vivas, consumo de tokens,
custo estimado, ferramentas disparadas e uso agregado. Desde **v0.6**
com modo interativo de abas.

## Motivação

O harness do Claude Code não expõe, nativamente, uma visão transversal de
quantas sessões estão ativas, quanto cada uma está consumindo em tokens e
custo, nem quais ferramentas (Bash, Edit, Read, Agent, etc.) foram
disparadas por cada sessão. Esses dados existem distribuídos em
`~/.claude/sessions/` e `~/.claude/projects/<workspace>/<sessionId>.jsonl`
— este projeto consolida tudo num único painel.

## Uso

### Modo interativo (TUI) — padrão

```bash
claude-dash                    # abre TUI com 4 abas
```

Atalhos de teclado:

| Tecla | Ação |
|---|---|
| `1` / `2` / `3` / `4` | Troca direta para Now / Today / Tools / Session |
| `←` / `→` | Navega entre abas |
| `r` | Refresh manual da aba atual |
| `q` | Sair |

Na aba **Session**, use `↑/↓` + `Enter` para selecionar uma sessão e ver
o drill-down (timeline de turnos, subagentes, custo por turno).

### Launcher em janela nova (opcional)

Para abrir a TUI numa janela de terminal separada (ex: atalho de teclado
do window manager):

```bash
scripts/claude-dash-tui
```

O script detecta automaticamente o emulador de terminal disponível
(gnome-terminal, kitty, alacritty, konsole, xterm, etc.). Force um
específico via `CLAUDE_DASH_TERMINAL=kitty scripts/claude-dash-tui`.

### Subcomandos explícitos (para scripting)

```bash
claude-dash now                # Live TUI das sessões ativas
claude-dash today              # agregado do dia corrente (00:00 local)
claude-dash tools              # breakdown de tool_use últimas 24h
claude-dash session <sid>      # drill-down (prefixo de sessionId aceito)
```

### Rate limits 5h/7d (via statusline próprio — v0.11+)

Desde **v0.11**, o dashboard possui seu próprio statusline
(`claude-dash-statusline`) que produz a mesma linha visual do script
bash original E captura os rate_limits automaticamente em `/tmp/` para
as views consumirem.

**Instalação em 1 comando (idempotente):**

```bash
claude-dash setup-status
```

Esse comando:
- Faz backup do `~/.claude/settings.json`
- Registra `claude-dash-statusline` como `statusLine.command`
- Se você já tinha um statusline configurado, preserva o path dele em
  `~/.claude/.claude-dash.json` como `statusline_wrap_path` — o
  statusline próprio então executa o seu script original para gerar o
  output visual (mantém aparência), mas ainda captura rate_limits.

Depois de rodar o setup, reinicie uma sessão do Claude Code. A partir
daí, o header da TUI passa a exibir as barras de 5h/7d.

#### Quando as barras aparecem — dependência cronológica

A captura acontece **a cada turno de interação** do Claude Code (o
harness invoca o statusline após cada mensagem). Consequências
práticas:

1. **Sessão recém-iniciada:** entre `setup-status` e o primeiro turno,
   não há snapshot ainda — as barras ficam ausentes. A partir da 1ª
   resposta do Claude na sessão, passam a exibir.
2. **Sessão ociosa por mais de 15 min:** snapshots são considerados
   "stale" após 15 minutos (`FRESHNESS_MS` em `rate_limits.py`).
   Sessão parada por 16+ min temporariamente perde a barra até a
   próxima interação.
3. **Múltiplas sessões:** rate limits são por conta, não por sessão.
   O dashboard exibe o "pior caso" entre todas as sessões com
   snapshot fresco.

Desde **v0.11.2**, quando a barra está indisponível o header mostra
uma linha `dim` explicando o estado exato (aguardando 1º turno /
sessão ociosa / setup-status não rodado), em vez de silenciar. A
**v0.11.3** adiciona detecção explícita de `statusLine` ausente em
`~/.claude/settings.json` — útil quando um sync cross-device ou um
editor externo sobrescreve a config e remove o registro; a mensagem
orienta rodar `setup-status` de novo em vez de deixar o usuário no
escuro.

**Para remover:** edite `~/.claude/settings.json` e restaure a partir
do backup em `~/.claude/backups/settings.json.backup.*`.

**Modo manual (pré-v0.11, ainda funciona):** você pode adicionar uma
linha no seu statusline existente com `echo "$input" | claude-dash-rate-limit-capture > /dev/null`.
O entrypoint de captura foi preservado por compatibilidade.

Sem nenhuma das formas acima, o dashboard continua funcional — apenas
omite a linha de rate limits no header. Graceful degradation.

### Audit log de tool calls

Desde **v0.13** o dashboard empacota um sistema de auditoria de toda
chamada de tool do Claude Code (Bash, Read, Edit, Write, WebFetch,
WebSearch, MCP, Agent, Skill). O hook `PostToolUse` com matcher `.*`
escreve em **dois destinos**, cada um com um propósito distinto:

| Destino | Conteúdo | Permissão | Sync Drive |
|:---|:---|:---|:---:|
| `/var/log/claude/tools.log` | **Completo** (cmd, path, url, query até 4 KB) | `syslog:adm 0640` | não |
| `~/.claude/iris/audit/sessions.log` | **Metadata-only** (timestamp, session, tool, status, hash, bytes) | `menzani 0600` | sim |

**Threat model.** A motivação foi um incidente de prompt injection
(2026-04-27, repo `paperclipai/paperclip` via `WebFetch`) que plantou
tags `<system-reminder>` falsificadas no conteúdo de uma página externa.
Auditoria fora do controle do agente é a defesa de última camada: o
forense local fica em `/var/log/claude/`, escrito pelo `rsyslog` (root),
imune a adulteração pelo próprio Claude. O `sessions.log` em metadata-only
é syncado para o Drive — timeline cross-device sem vazar `cmd`/`path`/`url`.
Hook é **fail-silent** (exit 0 sempre) — perda de log preferível a quebra
de fluxo da sessão.

**Bootstrap em 3 comandos:**

```bash
pipx install ~/Desenvolvimento/claude-dashboard
claude-dash setup-audit                    # parte user-mode + script root
sudo bash /tmp/claude-audit-root-setup.sh  # rsyslog + logrotate + /var/log/claude/
```

`setup-audit` é idempotente. Detecta o estado e age só no delta:

- `fresh`: nada instalado → bootstrap completo (cria `~/.claude/iris/audit/`,
  configura logrotate user, habilita systemd user timer, wira o
  `PostToolUse`, gera o script root).
- `migrate`: setup antigo em `~/.claude/iris/hooks/audit-tool.sh` → apenas
  re-wira `settings.json` para o entry point `claude-dash-audit-hook`.
- `partial`: alguns componentes presentes → completa o que falta.
- `installed`: tudo presente → no-op.

Flags relevantes:

```bash
claude-dash setup-audit --dry-run     # simula tudo
claude-dash setup-audit --print-sudo  # imprime o conteúdo dos scripts root
claude-dash setup-audit --uninstall   # reverte user-mode (preserva sessions.log)
```

**O `audit-tool.sh` legado em `~/.claude/iris/hooks/` nunca é deletado
automaticamente** — após validar a migração, remova manualmente.

**Limitação conhecida — subagentes.** Quando uma sessão pai dispara
`Agent(...)`, o hook captura **apenas a chamada inicial** (com
`subagent_type`, `description`, `duration_ms`). As tool calls internas do
subagent **não disparam `PostToolUse` no parent** e não aparecem no audit.
Granularidade no nível "Agent foi spawnado para X, levou N ms" está OK;
para saber comandos exatos, inspecionar o `transcript_path` do subagent
diretamente.

#### Receitas de consulta

Premissa: usuário no grupo `adm` lê os dois arquivos sem `sudo`.

**Tail vivo:**

```bash
tail -f ~/.claude/iris/audit/sessions.log     # metadata-only
tail -f /var/log/claude/tools.log             # com cmd/path/url
journalctl -t claude-audit -f                 # via journald
```

**Filtragem básica:**

```bash
SESSION="b531e7ac-b76b-4658-9e3f-940ea94e9cb6"
grep "$SESSION" /var/log/claude/tools.log | tail -10
grep 'tool="Bash"'    /var/log/claude/tools.log | tail -10
grep 'tool="WebFetch"' /var/log/claude/tools.log
grep 'status="error"' /var/log/claude/tools.log
```

**Janela temporal:**

```bash
# Última hora
awk -v t="$(date -d '1 hour ago' -Iseconds)" '$0 > t' /var/log/claude/tools.log
# Hoje a partir das 14h
grep "^$(date +%Y-%m-%d)T1[4-9]" /var/log/claude/tools.log
```

**Estatísticas:**

```bash
# Top tools
grep -oE 'tool="[^"]*"' ~/.claude/iris/audit/sessions.log | sort | uniq -c | sort -rn
# Top sessões
grep -oE 'session="[^"]*"' ~/.claude/iris/audit/sessions.log | sort | uniq -c | sort -rn | head -10
# Sessões com erros
grep 'status="error"' ~/.claude/iris/audit/sessions.log \
  | grep -oE 'session="[^"]*"' | sort | uniq -c
```

**Histórico (arquivos rotacionados):**

```bash
# Sistema (root-protected, 26 weeks comprimido)
ls /var/log/claude/
zgrep "session=\"$SESSION\"" /var/log/claude/tools.log-*.gz
# User-mode (metadata, syncado pro Drive)
ls ~/.claude/iris/audit/archive/
zgrep "tool=\"WebFetch\"" ~/.claude/iris/audit/archive/sessions-*.gz
```

**JSON via journald (parsing programático):**

```bash
journalctl -t claude-audit -o json --since "1 hour ago" | jq .
```

Retenção: 26 semanas (6 meses), gzip via `logrotate` sistema (cron diário)
+ `systemd --user` timer semanal (segunda 03:30 + jitter 15 min).

A aba TUI dedicada (`Audit`) com filtros e tail real-time fica para a
fase 2 (issue derivada do #24).

### MCP server — canal para agentes

A partir da v0.7, um servidor MCP expõe o estado do Claude Code como
ferramentas consultáveis por outros agentes. Útil para meta-raciocínio:
um agente pergunta "como está o fluxo de trabalho do usuário?" e recebe
uma síntese com alertas sobre sessões que merecem atenção.

Registre o servidor via CLI do Claude Code (grava em `~/.claude.json`):

```bash
claude mcp add --scope user claude-dashboard claude-dash-mcp
```

O flag `--scope user` torna o MCP disponível em **qualquer** sessão
do Claude Code neste dispositivo. Sem a flag, o registro fica
limitado ao projeto do diretório corrente. Verifique o health check
com `claude mcp list` — a entrada `claude-dashboard` deve aparecer
com `✓ Connected`.

Para remover: `claude mcp remove claude-dashboard`.

Ferramentas expostas:

| Tool | Retorna |
|---|---|
| `active_sessions` | Lista detalhada de sessões vivas |
| `today_summary` | Agregado do dia corrente |
| `tools_breakdown(hours=24)` | Breakdown de `tool_use` no período |
| `session_details(sid)` | Drill-down de 1 sessão |
| `account_info` | Conta, organização, billing type (flat-rate vs API) |
| `rate_limits` | Consumo 5h/7d (se hook opcional instalado) |
| `workflow_snapshot` | **Canal unificado** — resumo + alertas acionáveis |

## Instalação

### Pré-requisitos de sistema

- Python ≥ 3.12 (`python3 --version`)
- `pipx` (Debian/Ubuntu: `sudo apt install pipx`; outras distros:
  conforme o gerenciador de pacotes)

### Usuário final (recomendado — venv isolado via pipx)

```bash
pipx install ~/Desenvolvimento/claude-dashboard
```

Isso cria um venv dedicado em
`~/.local/share/pipx/venvs/claude-dashboard/` e expõe os entrypoints
`claude-dash`, `claude-dash-mcp`, `claude-dash-statusline` e
`claude-dash-rate-limit-capture` em `~/.local/bin/`.

Atualizações após `git pull`:

```bash
pipx install --force ~/Desenvolvimento/claude-dashboard
```

Remoção: `pipx uninstall claude-dashboard`.

### Desenvolvedor (editable)

```bash
cd ~/Desenvolvimento/claude-dashboard
pip install --user --break-system-packages -e .
```

Modo editable (`-e`) faz o CLI refletir alterações do código-fonte
sem reinstalar.

## Stack

- Python 3.12
- [`textual`](https://textual.textualize.io) — TUI framework (abas, keybindings)
- [`rich`](https://rich.readthedocs.io) — rendering de painéis e tabelas
- Parsing nativo de JSONL (sem dependência de `jq`)
- Cache incremental em `~/.cache/claude-dash/<sid>.json`

## Status

**v0.13** (em andamento, branch `feat/audit-phase1`) — empacota o hook
de auditoria de tool calls (`claude-dash-audit-hook`) e o subcomando
idempotente `claude-dash setup-audit`. Aba TUI dedicada fica para fase 2.

**v0.12** — flag `--version`, exposição de `session_name` (`/rename`)
em MCP tools e TUI.

Entregas principais anteriores:

- **v0.11.3** — detecção explícita de `statusLine` ausente em
  `~/.claude/settings.json` (robustez contra sync cross-device que
  sobrescreve a config sem preservar o registro).
- **v0.11.2** — mensagem `dim` no header explicando estados em que
  as barras de rate-limit não podem ser renderizadas, em vez de
  silenciar.
- **v0.11** — statusline próprio (`claude-dash-statusline`) com wrap
  do statusline anterior + setup em 1 comando
  (`claude-dash setup-status`).
- **v0.10** — detecção de plano real (Max/Pro/Team) via
  `credentials.json`.
- **v0.9** — hook opcional de rate-limits 5h/7d.
- **v0.8** — `account_info` (conta, organização, billing type).
- **v0.7** — servidor MCP com 7 tools (`active_sessions`,
  `today_summary`, `tools_breakdown`, `session_details`,
  `account_info`, `rate_limits`, `workflow_snapshot`).
- **v0.6** — modo TUI interativo com 4 abas (Textual).
- **v0.1–v0.5** — views `now` / `today` / `tools` / `session`.

Ver `docs/SCOPE.md` para escopo detalhado e decisões técnicas.

## Licença

Privado — uso pessoal de Leonardo Menzani.
