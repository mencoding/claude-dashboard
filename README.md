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

## Instalação

```bash
cd ~/Desenvolvimento/claude-dashboard
pip install --user --break-system-packages -e .
```

## Stack

- Python 3.12
- [`textual`](https://textual.textualize.io) — TUI framework (abas, keybindings)
- [`rich`](https://rich.readthedocs.io) — rendering de painéis e tabelas
- Parsing nativo de JSONL (sem dependência de `jq`)
- Cache incremental em `~/.cache/claude-dash/<sid>.json`

## Status

**v0.6.0.dev** — todas as 4 views implementadas (`now`, `today`,
`tools`, `session`) mais o modo TUI interativo com abas. Ver
`docs/SCOPE.md` para escopo, decisões técnicas e roadmap.

## Licença

Privado — uso pessoal de Leonardo Menzani.
