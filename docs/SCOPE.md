# claude-dashboard — Escopo e decisões

**Versão do documento:** 0.1.0
**Última atualização:** 2026-04-21

## 1. Problema

O Claude Code CLI mantém múltiplas sessões em paralelo (uma por processo
`claude` em execução). Cada sessão gera um transcript em
`~/.claude/projects/<workspace>/<sessionId>.jsonl` e é listada em
`~/.claude/sessions/<pid>.json` enquanto o processo está vivo.

Não há, hoje, nenhuma ferramenta oficial que agregue essas fontes e
responda perguntas triviais como:

- Quantas sessões Claude Code estão abertas agora?
- Quantos tokens cada uma já consumiu?
- Qual o custo estimado total do dia?
- Quais ferramentas (Bash, Edit, etc.) cada sessão disparou?
- Quanto do limite 5h/7d está sendo consumido, globalmente?

## 2. Escopo do MVP

### 2.1. Subcomandos

| Comando | Saída | Refresh |
|---|---|---|
| `claude-dash now` | TUI viva com tabela de sessões ativas, totais e tools | `rich.Live`, 2s |
| `claude-dash today` | Agregado do dia corrente (00:00 local): tokens, custo, breakdown por sessão | estático |
| `claude-dash tools` | Breakdown global de `tool_use` por nome, últimas 24h | estático |
| `claude-dash session <sid>` | Drill-down: timeline de mensagens, tokens por turno, tools usados, subagentes | estático |

### 2.2. Dados coletados

Por sessão:

- PID, `sessionId`, `cwd`, `startedAt`, versão do Claude Code
- Tokens acumulados: `input_tokens`, `output_tokens`,
  `cache_creation_input_tokens` (1h e 5m separados), `cache_read_input_tokens`
- Modelo usado (pode variar por turno)
- Custo estimado (tabela de preços hard-coded por modelo)
- Número de mensagens (user + assistant)
- Tool calls por tipo (Bash, Edit, Read, Write, Grep, Glob, Agent, Task\*, etc.)
- Subagentes disparados (arquivos em `<sid>/subagents/agent-*.jsonl`) com
  consumo próprio
- Git branch (do campo `gitBranch` em entradas `user`)
- Última atividade (mtime do JSONL)
- Estado vivo/morto (cross-check com `~/.claude/sessions/` e `kill -0 <pid>`)

### 2.3. Fora do escopo (MVP)

- Processamento de CPU/memória de processos filhos disparados por
  `Bash`: o Claude Code só grava o comando e exit code, não o PID
  filho; seria necessário instrumentar o harness ou usar `ptrace`, o
  que não justifica a complexidade.
- Histórico maior que 30 dias (por enquanto não há retenção
  configurável; limitamos consulta a 30d para evitar parsing de vários
  GB).
- Escrita/mutação de estado — o dashboard é read-only.
- Suporte a Windows/macOS (foco Linux).

## 3. Decisões técnicas

### 3.1. Framework de TUI: `rich` (não `textual`)

- `rich` já está instalado globalmente no ambiente.
- `rich.Live` + `Layout` cobre 100% do caso de uso (dashboard read-only
  com refresh periódico).
- `textual` seria necessário apenas para interatividade complexa
  (navegação multi-tela, formulários), o que não é o caso.

### 3.2. Parsing de JSONL

- Parser nativo em Python (sem shelling out para `jq`): evita 1 fork
  por entrada em transcripts com milhares de linhas.
- Streaming linha-a-linha com `json.loads` — footprint O(1).
- Parsing incremental: cache indexado por `(path, mtime, byte_offset)` em
  `~/.cache/claude-dash/<sid>.json`; ao reler um transcript já
  parcialmente processado, só lê o delta novo.

### 3.3. Tabela de preços

Hard-coded em `claude_dash/pricing.py`. Preços iniciais (jan/2026):

| Modelo | Input $/M | Output $/M | Cache read $/M | Cache write 1h $/M | Cache write 5m $/M |
|---|---|---|---|---|---|
| Opus 4.7 | 15.00 | 75.00 | 1.50 | 18.75 | 18.75 |
| Sonnet 4.6 | 3.00 | 15.00 | 0.30 | 3.75 | 3.75 |
| Haiku 4.5 | 1.00 | 5.00 | 0.10 | 1.25 | 1.25 |

**Validação pendente:** confirmar contra docs oficiais Anthropic.

### 3.4. Estrutura de código

```
claude_dashboard/
├── pyproject.toml
├── README.md
├── docs/
│   └── SCOPE.md                 # este arquivo
├── src/claude_dash/
│   ├── __init__.py
│   ├── __main__.py              # python -m claude_dash
│   ├── cli.py                   # argparse + dispatch
│   ├── models.py                # dataclasses: Session, Usage, ToolStat
│   ├── discover.py              # lê sessions/ + projects/
│   ├── parser.py                # JSONL → eventos tipados
│   ├── aggregator.py            # soma tokens, custo, tools
│   ├── cache.py                 # cache mtime+offset
│   ├── pricing.py               # tabela de preços + cálculo
│   └── views/
│       ├── now.py               # rich.Live TUI
│       ├── today.py
│       ├── tools.py
│       └── session.py
└── tests/
    └── ...
```

### 3.5. Empacotamento

- `pyproject.toml` com PEP 621
- Instalação em dev: `pipx install -e .`
- Entrypoint console: `claude-dash`

## 4. Roadmap

### v0.1 — MVP (sprint atual)
- [x] Criar repo e skeleton
- [ ] `discover.py` + `parser.py` + `models.py`
- [ ] `aggregator.py` + `cache.py`
- [ ] `pricing.py` com tabela atual
- [ ] View `now` com `rich.Live`
- [ ] Entrypoint CLI

### v0.2 — Enriquecimento da view 'now'
- [x] Tokens discriminados na tabela (total / in·out / cache r·w / turno)
- [x] Coluna `Ctx` com tokens do contexto ativo (último turno assistant)
- [x] `SessionStats.last_usage`, `active_context_tokens`, `tokens_per_turn`

### v0.3 — Agregações históricas
- [x] View `today` (dia corrente, filtragem **exata** por timestamp das entries)
- [x] `aggregator.aggregate_transcript_since` / `collect_sessions_since`

### v0.4 — Breakdown transversal por ferramenta
- [x] View `tools` (últimas 24h, rolling window)
- [x] `ToolUsageStats` (count por sessão, histograma 24h, peak hour)
- [x] `aggregator.collect_tool_usage_since` (subagents atribuídos ao parent)
- [x] Sparkline ASCII por tool + histograma agregado por hora

### v0.5 — Drill-down por sessão
- [x] View `session <sid>` com prefix matching
- [x] `Turn` dataclass (index, timestamp, model, usage, tools_called)
- [x] `aggregator.extract_turns` — um Turn por entrada assistant com usage
- [x] Timeline tail (últimos 20 turnos com custo estimado por turno)
- [x] Painel de subagentes com custo individual

### v0.3 — Drill-down
- [ ] View `session <sid>`
- [ ] Timeline de turnos

### v0.4 — Polish
- [ ] Validação da tabela de preços contra API oficial
- [ ] Suporte a múltiplos modelos no mesmo turno (observado em
  transcripts; cada mensagem já tem `model` próprio)
- [ ] Alerta visual em `now` quando rate limit ≥85%

### Futuro (fora de MVP)
- Exportação CSV/JSON (`claude-dash export --format csv`)
- Comparação de sessões lado-a-lado
- Integração com o statusline-dashboard.sh (mesmo layout compacto)

## 5. Notas de campo

### 5.1. Schema do JSONL observado

Cada linha é um JSON completo. Tipos de `type` observados:

- `user`: input do usuário; tem `cwd`, `gitBranch`, `version`,
  `timestamp`, `sessionId`
- `assistant`: resposta do modelo; tem `message.model`,
  `message.usage` (tokens completos), `message.content[]` (com
  `tool_use` aninhados)
- `system`: notificações do harness
- `attachment`: anexos de contexto
- `permission-mode`: mudanças de modo (plan, auto, etc.)
- `last-prompt`: marcador
- `file-history-snapshot`: snapshots de arquivos

### 5.2. Subagentes

Transcripts de subagentes vivem em
`projects/<workspace>/<sessionId>/subagents/agent-<id>.jsonl`. O
consumo deles é **separado** do transcript principal; agregação total
da sessão precisa somar os dois.

### 5.3. Detecção de sessão viva

`~/.claude/sessions/<pid>.json` só existe enquanto o processo está
vivo. Cross-check extra: `kill -0 <pid>` para pegar casos de crash sem
cleanup.
