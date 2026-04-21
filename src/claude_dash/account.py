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
from dataclasses import dataclass
from pathlib import Path


DEFAULT_CLAUDE_JSON = Path(os.environ.get("CLAUDE_JSON", Path.home() / ".claude.json"))


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

        Nota importante: o Claude Code **não expõe o nome do plano**
        (Pro, Max, Team, etc) em `~/.claude.json`. Esse dado só viria
        via call autenticada à API da Anthropic. Aqui retornamos o
        tipo de cobrança, que é o dado disponível localmente.
        """
        if self.billing_type == BILLING_FLAT_RATE:
            return "Assinatura (Stripe)"
        if self.billing_type == BILLING_API:
            return "API (pay-as-you-go)"
        if self.billing_type == BILLING_ENTERPRISE:
            return "Enterprise"
        return f"Billing desconhecido ({self.billing_type})"

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


def read_account_info(
    path: Path = DEFAULT_CLAUDE_JSON,
) -> AccountInfo | None:
    """Lê ~/.claude.json e extrai info de conta.

    Retorna None em três cenários (tratados silenciosamente, sem raise):
    1. arquivo ausente no path indicado
    2. JSON inválido (parse error) ou erro de leitura (OSError)
    3. arquivo existe e parseia mas não tem o bloco `oauthAccount`

    Callers devem checar `is None` antes de acessar campos.
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
    )
