"""Lê informação da conta/assinatura Anthropic a partir de ~/.claude.json.

O Claude Code armazena dados de OAuth e estado de billing local. Esta
camada expõe isso como `AccountInfo` tipado, permitindo que as views
diferenciem usuários em **plano flat-rate** (assinatura Max/Pro onde
o "custo em USD" é desinformação — o real consumo é % do rate limit
5h/7d) de usuários em **API billing** pay-as-you-go (onde USD
estimado é a métrica correta).
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CLAUDE_JSON = Path(os.environ.get("CLAUDE_JSON", Path.home() / ".claude.json"))
DEFAULT_CREDENTIALS_JSON = Path(os.environ.get(
    "CLAUDE_CREDENTIALS",
    Path.home() / ".claude" / ".credentials.json",
))


# Valores observados no campo `billingType` do oauthAccount.
# A enum não é exaustiva — outros valores devem ser tratados como
# "desconhecido" (fallback para API billing, mais conservador do
# ponto de vista de exibição de custo).
BILLING_FLAT_RATE = "stripe_subscription"  # plano Max/Pro/similar
BILLING_API = "api"                        # pay-as-you-go
BILLING_ENTERPRISE = "enterprise"          # não observado, mas plausível


@dataclass(slots=True)
class AccountInfo:
    """Snapshot da conta/assinatura do usuário no Claude Code."""

    email: str
    display_name: str
    organization_name: str
    organization_role: str              # "admin", "member", etc
    billing_type: str                   # valor raw do campo
    has_extra_usage_enabled: bool       # pay-as-you-go além do flat rate?
    extra_usage_disabled_reason: str | None
    first_token_date: str | None        # primeira vez que usou
    account_uuid: str
    organization_uuid: str
    # Campos lidos de ~/.claude/.credentials.json (claudeAiOauth). Vazios
    # se arquivo não acessível (permissão) ou sem bloco claudeAiOauth.
    subscription_type: str = ""         # ex: "max", "pro", "team"
    rate_limit_tier: str = ""           # ex: "default_claude_max_5x"

    @property
    def is_flat_rate(self) -> bool:
        """True se a conta é de assinatura flat-rate (Max, Pro, etc).

        Nesses planos, o "custo em USD por tokens" estimado pela
        tabela de preços da API NÃO representa o custo real do
        usuário — o usuário paga uma mensalidade fixa. A métrica
        relevante é o % consumido dos rate limits 5h/7d.
        """
        return self.billing_type == BILLING_FLAT_RATE

    @property
    def billing_label(self) -> str:
        """Label do **tipo de cobrança** (Stripe / API / Enterprise).

        Nota: este é o *tipo de cobrança* (como o usuário paga).
        O **nome do plano** (Pro, Max, Team) vem de `plan_label`,
        lido de `~/.claude/.credentials.json:claudeAiOauth`.
        """
        if self.billing_type == BILLING_FLAT_RATE:
            return "Assinatura (Stripe)"
        if self.billing_type == BILLING_API:
            return "API (pay-as-you-go)"
        if self.billing_type == BILLING_ENTERPRISE:
            return "Enterprise"
        return f"Billing desconhecido ({self.billing_type})"

    @property
    def plan_label(self) -> str:
        """Nome amigável do plano, combinando `subscription_type` +
        variante detectada em `rate_limit_tier`.

        Exemplos de retorno:
            'Max 5×'   (subscription=max, tier=default_claude_max_5x)
            'Max 20×'  (subscription=max, tier=default_claude_max_20x)
            'Pro'      (subscription=pro)
            '—'        (qualquer caso em que subscription_type está vazio)

        O '—' cobre quatro situações reais, indistinguíveis do caller:
        (1) credentials.json inacessível (permissão/ausente),
        (2) JSON parseável mas sem bloco `claudeAiOauth`,
        (3) bloco existe mas `subscriptionType` é null/string vazia,
        (4) JSON manualmente corrompido (claudeAiOauth não é dict).
        Em qualquer um, o caller pode exibir `billing_label` como
        fallback.
        """
        if not self.subscription_type:
            return "—"
        name = self.subscription_type.capitalize()  # "max" → "Max"
        # Detecta variante numérica no tier (ex: 5x, 20x)
        match = re.search(r"_(\d+)x$", self.rate_limit_tier or "")
        if match:
            return f"{name} {match.group(1)}×"
        return name

    @property
    def extra_usage_label(self) -> str:
        """Status do pay-as-you-go adicional (créditos extras).

        Retorna uma descrição compacta combinando capability
        (`has_extra_usage_enabled`) com o runtime status
        (`extra_usage_disabled_reason`). Estes são dois campos
        ortogonais:
        - capability = conta está inscrita no programa pay-as-you-go
        - runtime reason = motivo pelo qual NÃO está disponível agora
          (ex: sem saldo, bloqueio temporário)

        Exemplos de retorno:
            'habilitado, sem créditos no momento'
            'habilitado, disponível'
            'desabilitado'
        """
        if not self.has_extra_usage_enabled:
            return "desabilitado"
        if self.extra_usage_disabled_reason:
            # Traduz códigos comuns para PT-BR
            reason = {
                "out_of_credits": "sem créditos no momento",
                "billing_issue": "problema de cobrança",
                "exceeded_limit": "limite de gastos excedido",
            }.get(
                self.extra_usage_disabled_reason,
                self.extra_usage_disabled_reason,
            )
            return f"habilitado, {reason}"
        return "habilitado, disponível"


def _read_credentials(
    path: Path = DEFAULT_CREDENTIALS_JSON,
) -> tuple[str, str]:
    """Lê subscriptionType e rateLimitTier de ~/.claude/.credentials.json.

    Retorna ('', '') se o arquivo não estiver acessível ou se o JSON
    não tiver o bloco `claudeAiOauth` no formato esperado. Erros
    silenciosos — o display cai em fallback via `plan_label`.

    Importante: `.credentials.json` tem permissão 0600 por design
    (contém refresh tokens). Este módulo só **lê** os campos
    não-sensíveis (tipo de plano e tier), nunca os tokens.
    """
    if not path.is_file():
        return "", ""
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        # PermissionError é subclasse de OSError — sem menção explícita.
        return "", ""
    oauth = data.get("claudeAiOauth")
    # Proteção contra JSON manualmente corrompido (claudeAiOauth pode
    # vir como lista/int/null caso alguém tenha editado o arquivo).
    if not isinstance(oauth, dict):
        return "", ""
    return (
        str(oauth.get("subscriptionType") or ""),
        str(oauth.get("rateLimitTier") or ""),
    )


def read_account_info(
    path: Path = DEFAULT_CLAUDE_JSON,
    credentials_path: Path = DEFAULT_CREDENTIALS_JSON,
) -> AccountInfo | None:
    """Lê ~/.claude.json + ~/.claude/.credentials.json e monta AccountInfo.

    Retorna None em três cenários (tratados silenciosamente, sem raise):
    1. claude.json ausente no path indicado
    2. JSON inválido (parse error) ou erro de leitura (OSError)
    3. arquivo existe e parseia mas não tem o bloco `oauthAccount`

    O credentials.json é opcional — se não acessível, os campos
    `subscription_type` e `rate_limit_tier` ficam vazios e
    `plan_label` retorna '—'. AccountInfo é retornada mesmo assim.
    """
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None

    oauth = data.get("oauthAccount") or {}
    if not oauth:
        return None

    subscription_type, rate_limit_tier = _read_credentials(credentials_path)

    return AccountInfo(
        email=oauth.get("emailAddress") or "",
        display_name=oauth.get("displayName") or "",
        organization_name=oauth.get("organizationName") or "",
        organization_role=oauth.get("organizationRole") or "",
        billing_type=oauth.get("billingType") or "",
        has_extra_usage_enabled=bool(oauth.get("hasExtraUsageEnabled", False)),
        extra_usage_disabled_reason=data.get("cachedExtraUsageDisabledReason"),
        first_token_date=data.get("claudeCodeFirstTokenDate"),
        account_uuid=oauth.get("accountUuid") or "",
        organization_uuid=oauth.get("organizationUuid") or "",
        subscription_type=subscription_type,
        rate_limit_tier=rate_limit_tier,
    )
