# claude-dashboard

TUI de monitoramento do Claude Code — sessões vivas, consumo de tokens,
custo estimado, ferramentas disparadas e uso agregado.

## Motivação

O harness do Claude Code não expõe, nativamente, uma visão transversal de
quantas sessões estão ativas, quanto cada uma está consumindo em tokens e
custo, nem quais ferramentas (Bash, Edit, Read, Agent, etc.) foram
disparadas por cada sessão. Esses dados existem distribuídos em
`~/.claude/sessions/` e `~/.claude/projects/<workspace>/<sessionId>.jsonl`
— este projeto consolida tudo num único painel.

## Uso (previsto)

```bash
claude-dash now                # TUI viva (refresh 2s) — sessões ativas
claude-dash today              # agregado do dia corrente (00:00 local)
claude-dash tools              # breakdown global de tool_use por tipo
claude-dash session <sid>      # drill-down de 1 sessão
```

## Instalação (dev)

```bash
pipx install -e /home/menzani/Desenvolvimento/claude-dashboard
```

## Stack

- Python 3.12
- [`rich`](https://rich.readthedocs.io) — `Live` + `Layout` + `Table`
- `jq`-free: parsing de JSONL nativo em Python para reduzir overhead

## Status

Em desenvolvimento inicial. Ver `docs/SCOPE.md` para escopo, decisões e
estrutura.

## Licença

Privado — uso pessoal de Leonardo Menzani.
