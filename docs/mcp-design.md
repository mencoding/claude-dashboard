# Design da superfície MCP

> Documento de decisões de design pra `claude-dash-mcp`. Resolve [#54].
> Atualizado em **2026-04-28** com a v0.15.0 (pós-merge dos PRs #56–#59).

## Contexto

`claude-dash-mcp` é um servidor MCP (`FastMCP`) que expõe o estado do
Claude Code como ferramentas consultáveis por outros agentes — sub-agentes
da própria sessão, sessões paralelas Claude Code, ou agentes externos via
relay MCP. Implementado em `src/claude_dash/mcp_server.py` (~495 linhas).

Após várias releases adicionando features (`session_name`, rate limits,
account info, audit log, alertas de erro), faz sentido revisar a
superfície inteira antes de continuar adicionando tools — em
particular, antes de **expor o audit log via MCP**, que é a próxima
decisão grande.

## Inventário das tools atuais

| Tool | Linhas | Retorno | Sensibilidade |
|---|---:|---|---|
| `account_info` | 167–198 | Plano (Max/Pro), e-mail, créditos extras, UUIDs | **Alta** (e-mail, billing) |
| `rate_limits` | 201–246 | Janelas 5h/7d (via statusline hook) | Baixa |
| `active_sessions` | 249–263 | Sessões vivas (tokens, custo, tools, `session_name`) | **Média** (`session_name` pode ser sensível) |
| `today_summary` | 266–290 | Agregado do dia | Média (mesmas pegadas das sessões) |
| `tools_breakdown` | 293–321 | Top tools em janela (default 24h) | Baixa-Média (revela padrão de uso) |
| `session_details` | 324–381 | Drill-down de 1 sessão por SID prefix | **Alta** (`cwd` absoluto, timeline de turnos) |
| `workflow_snapshot` | 382–488 | Canal unificado: account + active + alertas | **Alta** (herda tudo) |

Helpers internos: `_stats_to_dict` (cap 5/4/10 tools), `_infer_alerts`
(turno alto, sessão idle, contexto >150k, custo diário >$200).

## Lentes de revisão

### 1. Privacidade e vazamento

**Findings:**

- `account_info` retorna **e-mail e UUIDs**. Aceitável pra um sub-agente
  local? Pra um agente remoto via relay?
- `session_details` retorna `cwd` absoluto — vaza estrutura do filesystem
  (ex.: `/home/menzani/Desenvolvimento/cliente-X/repo-priv`).
- `session_name` (definido via `/rename`, ex.: `auto-normas+20260423`)
  pode conter info sensível: `cliente-X-credenciais`, `ipsec-prod`, etc.
- `tools_breakdown` revela padrão (`Bash:62%, WebFetch:1%`) que
  caracteriza o usuário — feature de profile/fingerprint.
- **Tool inputs** (`tool_input`) não estão expostos via MCP — bom.
- **Audit log NÃO está exposto** — bom, mas é gap (ver lente 2).

**Decisão:**

> **Manter tudo público no momento, com flag de scoping reservada pra
> uso futuro.** Justificativa: hoje há 1 usuário (Léo), em 3
> dispositivos (Predator/VAIO/RET-DTI), todos sob o mesmo controle.
> Privacidade pra agentes externos é hipotética. Mas adicionar agora
> uma **constante `MCP_SCOPE`** (`"local"` default vs `"public"`) e
> documentar quais tools cairiam fora do scope público facilita o
> trabalho quando o relay externo virar real.

**Action items:**
- [ ] (Issue futura) Adicionar `MCP_SCOPE` env var; tools sensíveis
  (`account_info` com e-mail/UUIDs, `session_details` com `cwd`)
  retornam subset reduzido quando scope=public.
- [ ] (Issue futura) Função `_scrub_cwd(path)` que retorna basename ou
  `<scrubbed>` quando `MCP_SCOPE=public`.

### 2. Completeness e gaps

**Findings:**

- **Audit log não está exposto.** Outros agentes não conseguem
  perguntar "que tool calls a sessão X fez?" ou "houve erros nas
  últimas 2h?". Considerando que a v0.15.0 já tem `sessions.log` como
  fonte estruturada e `partial_stats.build_partial_stats_from_audit`
  pronto, a infraestrutura está aí.
- **Health check** do próprio dashboard ausente: é o rsyslog rodando?
  Audit hook está wired? Tail incremental atualizado?
- **Versão do dashboard** não é exposta diretamente — agente que
  pergunta "que versão estou rodando?" via MCP não tem resposta direta
  (`account_info.dashboard_version` não existe).
- **Controle de alertas** (níveis, dedup) não exposto — agente não
  consegue saber qual o nível corrente de `CLAUDE_DASH_AUDIT_ALERT_LEVEL`.

**Decisão:**

> **Sim, expor audit log via MCP — em fases.** O caso de uso é claro:
> agente meta-supervisor que pergunta "o que aconteceu nas últimas 2h?"
> ou "quais tools tiveram mais erros?". Começar com `audit_entries(...)`
> (read-only, com filtros) e `audit_session_partial(sid)` que envolve
> `build_partial_stats_from_audit`. NÃO expor `tools.log` (`/var/log/claude/`)
> via MCP — só metadata da `sessions.log`. Privacidade alinhada.
>
> **Adicionar `dashboard_health()` tool**, retornando: rsyslog ativo
> (via `systemctl is-active`), audit hook wired (via `_detect_state`),
> última entry do tail incremental.
>
> **Embutir `dashboard_version`** em todos os retornos como campo
> top-level (vem de `__version__`).

**Action items:**
- [ ] (Issue derivada) `audit_entries(session=None, tool=None, status=None, hours=24, limit=500)` — wraps `filter_entries` da `views/audit.py`.
- [ ] (Issue derivada) `audit_session_partial(sid)` — wraps `build_partial_stats_from_audit`.
- [ ] (Issue derivada) `dashboard_health()` — checa rsyslog/hook/tail.
- [ ] (Issue derivada) Campo `dashboard_version` em todos os retornos.

### 3. Schema e consistência

**Findings:**

- Todas as tools retornam `dict[str, Any]` — sem schema documentado.
  Cliente MCP precisa adivinhar a estrutura.
- `active_sessions` e `session_details` retornam campos diferentes
  pra mesma sessão (subset). Não documentado em lugar nenhum.
- **Alertas** em `workflow_snapshot` são strings livres
  (`'Sessão X usando Y tokens...'`). Programaticamente não-parseáveis.
- Cores/estilos não saem do MCP (correto — formatação é responsabilidade
  do consumidor).

**Decisão:**

> **Documentar schema inline nas docstrings, em formato pseudo-TS**
> pra cada tool. Não migrar pra Pydantic models agora (overhead sem
> ganho imediato), mas docstring com schema permite agentes inferirem
> estrutura sem trial-and-error.
>
> **Estruturar alertas** em vez de strings livres. Novo shape:
> ```json
> {
>   "level": "warning|critical",
>   "code": "high_turn|idle_session|context_full|daily_cost",
>   "session_id": "...",
>   "message": "human-readable summary",
>   "data": { "tokens": 150000, "threshold": 100000 }
> }
> ```
> Mantém `message` pra UI mas adiciona `code` + `data` pra agentes
> filtrarem programaticamente.
>
> **Documentar a sobreposição** `active_sessions` ⊂ `session_details`
> num docstring conjunto.

**Action items:**
- [ ] (Issue derivada) Refactor `_infer_alerts` pra retornar lista de
  dicts estruturados; campo `message` continua pra display.
- [ ] (Issue derivada) Adicionar bloco `Schema:` nas docstrings de cada
  tool, em pseudo-TS.

### 4. Estabilidade e versionamento

**Findings:**

- Não há cabeçalho `schema_version` em payloads. Mudanças (ex.:
  adição de `session_name` em #26) podem quebrar clientes estritos.
- Política de deprecação ausente: o que fazer ao remover/renomear?
- Bump de minor (0.14 → 0.15 desta release) já indica mudanças, mas
  agente que monitora não tem sinal in-band.

**Decisão:**

> **Adicionar `_schema_version: int` top-level em cada retorno**,
> incrementado quando shape mudar de forma não-aditiva (remoção ou
> rename de campo). Adições de campos novos não bumpam (compat com
> clientes que ignoram extras — padrão JSON-friendly).
>
> Versão inicial = `1`. Documentar política em `docs/mcp-design.md`
> (este doc): aditivo → mesma versão; remoção/rename → bump.
>
> NÃO usar `dashboard_version` como surrogate (cresce com cada release;
> `_schema_version` cresce só com breaking).

**Action items:**
- [ ] (Issue derivada) Adicionar campo `_schema_version: 1` em cada
  retorno top-level. Helper `_envelope(payload)` pode encapsular.
- [ ] (Documentação) Política de versionamento gravada nesta seção.

## Política de versionamento de schema

`_schema_version` é incrementado quando:

1. Um campo é **removido** do retorno.
2. Um campo é **renomeado** (equivalente a remover + adicionar).
3. O **tipo** de um campo muda de forma incompatível (ex.: `int` → `str`).

`_schema_version` **não é** incrementado quando:

1. Um campo novo é **adicionado** (clientes não-estritos ignoram).
2. Um campo é **deprecado** mas mantido (com aviso na docstring).
3. O **comportamento** muda mas o shape continua o mesmo (ex.: cap
   subiu de 5 pra 8 tools no top — mesmo schema, dado mais rico).

Período de deprecação mínimo: 2 minor releases antes de remover.

## Categorização das tools por scope (futuro)

Quando `MCP_SCOPE=public` (placeholder, hoje sempre `local`):

| Tool | Local | Public | Notas |
|---|:---:|:---:|---|
| `account_info` | ✅ | partial | Public esconde e-mail, UUIDs |
| `rate_limits` | ✅ | ✅ | Sem dado pessoal |
| `active_sessions` | ✅ | partial | Public scrubba `cwd`, redact `session_name` |
| `today_summary` | ✅ | partial | Idem |
| `tools_breakdown` | ✅ | ✅ | Padrão de uso é genérico |
| `session_details` | ✅ | partial | Public retorna apenas counts (sem turnos individuais) |
| `workflow_snapshot` | ✅ | partial | Idem (herda) |
| `audit_entries` (futuro) | ✅ | ❌ | Não expor cross-network |
| `audit_session_partial` (futuro) | ✅ | partial | Public retorna só counts agregados |
| `dashboard_health` (futuro) | ✅ | ✅ | Status técnico, sem dado pessoal |

Default permanece `local` enquanto não houver caso de uso real de
relay externo.

## Issues derivadas (a abrir)

Cada decisão acima vira issue separada com escopo focado. Ordem
sugerida:

1. **`feat(mcp): _schema_version + envelope`** — preparação base; entra
   antes das outras pra que mudanças subsequentes já saiam versionadas.
2. **`feat(mcp): alertas estruturados (code/data)`** — refactor
   `_infer_alerts`; possibilita consumo programático.
3. **`feat(mcp): expor audit log via MCP`** — `audit_entries` +
   `audit_session_partial`. Inclui doc do shape.
4. **`feat(mcp): dashboard_health()`** — health check tool.
5. **`feat(mcp): MCP_SCOPE env var + scrubbing`** — quando o caso de
   uso de relay externo virar concreto. Hoje é vapor.

## O que **não** vamos fazer

- **Migrar pra Pydantic models** — overhead sem ganho imediato pra um
  servidor com 7 tools simples.
- **Adicionar autenticação** ao MCP server — fora do escopo do
  protocolo MCP atual; quando relay externo for real, autenticação
  acontece no nível do relay.
- **Expor `tools.log` completo** (cmd/path/url) via MCP — sensível
  demais; só `sessions.log` metadata.

## Decisão final

Implementar os action items na ordem 1→4 da seção anterior nas
próximas releases. Cada um vira PR + issue separada. O #5 (scoping)
fica em `Backlog` até haver caso de uso real.

Este documento é a fonte de verdade pra qualquer mudança futura no
`mcp_server.py` — alterações em design devem editar aqui antes de
mexer no código.
