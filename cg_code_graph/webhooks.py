"""Webhook receivers (#37, #152 part A): routes called by a third party, their signature checks and the events they handle.

For every route, the handler (and the functions it calls or dispatches to, two levels deep, plus its middleware
functions) is scanned:

  verified     a known verification call: Stripe `constructEvent` / `construct_event`, svix / standardwebhooks
               `new Webhook(secret).verify(..)`, @octokit/webhooks `verify` / `verifyAndReceive`, Twilio
               `validateRequest` / `RequestValidator(..).validate`, Shopify `webhooks.validate`; or an HMAC computed
               (`createHmac`, `hmac.new`, `hash_hmac`, ...) next to a constant-time comparison (`timingSafeEqual`,
               `hmac.compare_digest`, `hash_equals`, ...) with a signature header; or a provider token header
               (`X-Gitlab-Token`) compared in constant time; or a route middleware named like a webhook signature
               check (`VerifyWebhookSignature`).
  unverified   the handler reads a provider header (`Stripe-Signature`, `X-GitHub-Event`, `X-Twilio-Signature`, ...)
               and nothing above verifies it.

The route gets `attrs.webhook = {provider, verified, check, how, events}`; `cg routes` lists the check as a guard
(SECRET-CHECKED) or flags `WEBHOOK UNVERIFIED`. Event names compared with the event type (`switch (event.type)`,
`event["type"] == "x"`, `$event->type`, `data_get($event, 'type')`, the `X-GitHub-Event` header value) become
`endpoint:webhook:<provider>:<event>`, RECEIVED_BY the function holding the comparison and, when a branch calls at
most two project functions that are not shared by the other branches, by those too. @octokit/webhooks
`webhooks.on('push', fn)` receives `endpoint:webhook:github:push` directly.
"""
from __future__ import annotations

import re
from collections import defaultdict

from .brokers import JS_EXT, Scan as BrokerScan
from .core.model import EXACT, HEURISTIC
from .protocols import protocol_receive

# header (lower case) -> (provider, role); role sig / event / token / ts
HEADERS = {
    "stripe-signature": ("stripe", "sig"),
    "x-hub-signature-256": ("github", "sig"), "x-hub-signature": ("github", "sig"),
    "x-github-event": ("github", "event"), "x-github-delivery": ("github", "id"),
    "x-gitea-signature": ("gitea", "sig"), "x-gitea-event": ("gitea", "event"),
    "x-gitlab-token": ("gitlab", "token"), "x-gitlab-event": ("gitlab", "event"),
    "x-event-key": ("bitbucket", "event"),
    "x-slack-signature": ("slack", "sig"), "x-slack-request-timestamp": ("slack", "ts"),
    "x-shopify-hmac-sha256": ("shopify", "sig"), "x-shopify-topic": ("shopify", "event"),
    "x-twilio-signature": ("twilio", "sig"),
    "svix-signature": ("svix", "sig"), "webhook-signature": ("standard-webhooks", "sig"),
    "paddle-signature": ("paddle", "sig"), "x-razorpay-signature": ("razorpay", "sig"),
    "x-paystack-signature": ("paystack", "sig"), "x-square-hmacsha256-signature": ("square", "sig"),
    "x-signature-ed25519": ("discord", "sig"), "linear-signature": ("linear", "sig"),
    "x-cal-signature-256": ("cal.com", "sig"),
}
_HDR_ALT = "|".join(sorted((re.escape(h).replace("\\-", "[-_]") for h in HEADERS), key=len, reverse=True))
HDR_RX = re.compile(r"""['"](?:HTTP_)?(""" + _HDR_ALT + r""")(?=['".])""", re.I)
PY_HDR_RX = re.compile(r"\b(" + _HDR_ALT.replace("[-_]", "_") + r")\s*:[^=\n]*=\s*Header\s*\(", re.I)
# any other signature header: X-Partner-Signature, X-Webhook-Signature, X-Hmac-Sha256 ...
SIG_HDR_RX = re.compile(r"""['"](?:HTTP_)?(x[-_][\w-]*(?:signature|hmac)[\w-]*|signature)(?=['".])""", re.I)

VERIFY = [
    (re.compile(r"\bconstruct_?[Ee]vent(?:Async)?\s*\("), "stripe", "Stripe constructEvent", re.compile(r"(?i)stripe")),
    # `x.verify(..)`: the library is the one whose class built `x` (VERIFY_OBJ)
    (re.compile(r"\b(\w+)\s*\.\s*(?:verify|verifyAndReceive)\s*\("), None, None, re.compile(r"""@octokit/webhooks|svix|standardwebhooks|StandardWebhooks""")),
    (re.compile(r"\bvalidateRequest(?:WithBody)?\s*\(|\bRequestValidator\s*\([^)]*\)\s*(?:\.|->)\s*validate\s*\(|"
                r"\bRequestValidator\s*::\s*validate\s*\(|\$?\bvalidator\s*(?:\.|->)\s*validate\s*\("),
     "twilio", "Twilio request validation", re.compile(r"(?i)twilio")),
    (re.compile(r"\bshopify\s*\.\s*webhooks\s*\.\s*validate\s*\("), "shopify", "Shopify webhooks.validate", None),
]
VERIFY_OBJ = [   # (constructor of the verifying object, import hint, provider, how)
    (r"Webhooks", re.compile(r"""['"]@octokit/webhooks['"]"""), "github", "@octokit/webhooks verify"),
    (r"Webhook", re.compile(r"""['"]svix['"]|\bfrom\s+svix\b|\bimport\s+svix\b|Svix\\"""), "svix", "svix Webhook.verify"),
    (r"Webhook", re.compile(r"""['"]standardwebhooks['"]|\bfrom\s+standardwebhooks\b|StandardWebhooks\\"""), "standard-webhooks",
     "standardwebhooks Webhook.verify"),
]
HMAC_RX = re.compile(r"\bcreateHmac\s*\(|\bhmac\s*\.\s*new\s*\(|\bhash_hmac\s*\(|\bHmac(?:Sha\d+)?\s*::\s*new_from_slice|"
                     r"\bMac\s*\.\s*getInstance\s*\(\s*\"Hmac|\bHMAC\s*<|\bHMAC\s*\(|\bhmac\s*\.\s*digest\s*\(")
CT_RX = re.compile(r"\btimingSafeEqual\s*\(|\bcompare_digest\s*\(|\bhash_equals\s*\(|\bconstant_time_compare\s*\(|"
                   r"\bMessageDigest\s*\.\s*isEqual\s*\(|\.\s*verify_slice\s*\(|\bconstant_time_eq\s*\(|\.\s*ct_eq\s*\(|"
                   r"\bsecureCompare\s*\(|\bsafeCompare\s*\(")
MW_RX = re.compile(r"(?i)verify\w*(?:webhook|signature)|webhook\w*(?:signature|verif)|stripe[-_.]webhook")
CODE_EXT = JS_EXT + (".jsx", ".py", ".php", ".kt", ".rs", ".swift", ".rb", ".java")
SHARED_CALLERS = 6
SVIX_HINT = re.compile(r"""['"]svix['"]|\bfrom\s+svix\b|\bimport\s+svix\b|Svix\\|\bnew\s+Svix\s*\(""")
SVIX_SEND = re.compile(r"(?:\.|->)\s*message\s*(?:\.|->)\s*create\s*\(")
HTTP_OUT = re.compile(r"\bfetch\s*\(|\baxios\b|\bgot\s*\(|\brequests\s*\.\s*(?:post|put|request)\s*\(|\bhttpx\b|"
                      r"\bHttp\s*::\s*(?:post|withHeaders|withBody|send)|\bcurl_setopt|->\s*post\s*\(|\burlopen\s*\(|"
                      r"\bsession\s*\.\s*(?:post|request|send)\s*\(|\bClient\s*\(\s*\)\s*->\s*(?:post|request)|"
                      r"->\s*request\s*\(\s*['\"]POST['\"]")
# a signature built by a signing helper (`createWebhookSignature(..)`, `$signatureGenerator->generate(..)`)
SIGN_CALL = re.compile(r"\b(?:\w*[Ss]ignature\w*|sign|sign_\w+|\w+_sign)\s*\(|\$?\w*[Ss]ignature\w*\s*->\s*\w+\s*\(")
# helpers a branch calls that do not handle the event: predicates, readers, logging, responses
HELPER_NAME = re.compile(r"^(?:is|has|can|should|get|read|parse|to|format|log|audit|debug|info|warn|error|report|validate|"
                         r"verify|check|ensure|assert|json|response|abort)(?:[A-Z_]|$)")
# provider named by the route path or handler (`/mailgun_webhook`, `PaddleWebhookController`) when no header names it
PROVIDER_NAMES = re.compile(r"(?i)(stripe|github|gitlab|gitea|bitbucket|slack|shopify|twilio|svix|paddle|razorpay|paystack|"
                            r"square|discord|linear|mailgun|postmark|sendgrid|brevo|mollie|paypal|braintree|lemon_?squeezy|"
                            r"clerk|resend|calendly|zoom|telegram|whatsapp|intercom|hubspot|docusign|plaid|coinbase)")
EVENT_LIT = re.compile(r"[A-Za-z][\w.:/ -]{0,79}")
GENERIC_EVENT_VARS = {"event", "evt", "payload", "body", "data", "webhook", "notification", "webhookEvent",
                      "webhook_event", "stripeEvent", "stripe_event", "msg", "message"}
TYPE_KEYS = ("type", "event", "event_type", "eventType", "object_kind")
# a resolved constant value that names a signature header (`Saleor-Signature`, `X-Hub-Signature-256`, `signature`)
_SIG_NAME = re.compile(r"(?i)^(?:x-)?[\w-]*(?:signature|hmac)[\w-]*$")
# `const NAME = "..."`, `public const NAME`, `const val NAME`, `static readonly NAME`, `pub const NAME: &str`
_CONST_DEF = re.compile(
    r"""(?m)^[ \t]*(?:export[ \t]+|pub(?:\([^)\n]*\))?[ \t]+)?"""
    r"""(?:(?:public|private|protected|static|final|readonly|const|val|String)[ \t]+)+"""
    r"""(?P<name>[A-Za-z_]\w*)[ \t]*(?::[^=\n]+)?=[ \t]*"""
    r"""(?P<q>['"])(?P<val>[^'"\n]{1,120})(?P=q)""")
_PY_CONST_DEF = re.compile(
    r"""(?m)^(?P<name>[A-Z][A-Z0-9_]{2,})[ \t]*=[ \t]*(?P<q>['"])(?P<val>[^'"\n]{1,120})(?P=q)""")
# A coercion keeps the event string. A further field (`eventName.length`, `event.status`) is not the event.
_TAIL = r"(?:\s*\.\s*(?:as_str|to_string|toString|trim)\s*(?:\(\s*\))?){0,2}"
CASHIER_PARENT = "Cashier\\Http\\Controllers\\WebhookController"
_DEFAULT_VALIDATOR = "SignatureValidator\\DefaultSignatureValidator"
# Published Stripe event types. Cashier's method is `handle` + StudlyCase of the type with `.` replaced by `_`,
# so this map is the reverse. Dots and underscores are not distinct in the method name; no two published types share one.
_STRIPE_EVENTS = """
account.application.authorized
account.application.deauthorized
account.external_account.created
account.external_account.deleted
account.external_account.updated
account.updated
application_fee.created
application_fee.refund.updated
application_fee.refunded
apps.install.created
apps.install.deleted
apps.install.updated
balance.available
balance_settings.updated
billing.alert.triggered
billing.credit_balance_transaction.created
billing.credit_grant.created
billing.credit_grant.updated
billing.meter.created
billing.meter.deactivated
billing.meter.reactivated
billing.meter.updated
billing_portal.configuration.created
billing_portal.configuration.updated
billing_portal.session.created
capability.updated
cash_balance.funds_available
charge.captured
charge.dispute.closed
charge.dispute.created
charge.dispute.funds_reinstated
charge.dispute.funds_withdrawn
charge.dispute.updated
charge.expired
charge.failed
charge.pending
charge.refund.updated
charge.refunded
charge.succeeded
charge.updated
checkout.session.async_payment_failed
checkout.session.async_payment_succeeded
checkout.session.completed
checkout.session.expired
climate.order.canceled
climate.order.created
climate.order.delayed
climate.order.delivered
climate.order.product_substituted
climate.product.created
climate.product.pricing_updated
coupon.created
coupon.deleted
coupon.updated
credit_note.created
credit_note.updated
credit_note.voided
customer.created
customer.deleted
customer.discount.created
customer.discount.deleted
customer.discount.updated
customer.source.created
customer.source.deleted
customer.source.expiring
customer.source.updated
customer.subscription.created
customer.subscription.deleted
customer.subscription.paused
customer.subscription.pending_update_applied
customer.subscription.pending_update_expired
customer.subscription.resumed
customer.subscription.trial_will_end
customer.subscription.updated
customer.tax_id.created
customer.tax_id.deleted
customer.tax_id.updated
customer.updated
customer_cash_balance_transaction.created
entitlements.active_entitlement_summary.updated
file.created
financial_connections.account.account_numbers_updated
financial_connections.account.created
financial_connections.account.deactivated
financial_connections.account.disconnected
financial_connections.account.expected_deactivation_date_updated
financial_connections.account.reactivated
financial_connections.account.refreshed_balance
financial_connections.account.refreshed_ownership
financial_connections.account.refreshed_transactions
financial_connections.account.supported_payment_method_types_updated
financial_connections.account.upcoming_account_number_expiry
financial_connections.account.upcoming_deactivation
financial_connections.authorization.expected_deactivation_date_updated
financial_connections.authorization.upcoming_deactivation
identity.verification_session.canceled
identity.verification_session.created
identity.verification_session.processing
identity.verification_session.redacted
identity.verification_session.requires_input
identity.verification_session.verified
invoice.created
invoice.deleted
invoice.finalization_failed
invoice.finalized
invoice.marked_uncollectible
invoice.overdue
invoice.overpaid
invoice.paid
invoice.payment_action_required
invoice.payment_attempt_required
invoice.payment_failed
invoice.payment_succeeded
invoice.sent
invoice.upcoming
invoice.updated
invoice.voided
invoice.will_be_due
invoice_payment.paid
invoiceitem.created
invoiceitem.deleted
issuing_authorization.created
issuing_authorization.request
issuing_authorization.updated
issuing_card.created
issuing_card.updated
issuing_cardholder.created
issuing_cardholder.updated
issuing_dispute.closed
issuing_dispute.created
issuing_dispute.funds_reinstated
issuing_dispute.funds_rescinded
issuing_dispute.submitted
issuing_dispute.updated
issuing_personalization_design.activated
issuing_personalization_design.deactivated
issuing_personalization_design.rejected
issuing_personalization_design.updated
issuing_token.created
issuing_token.updated
issuing_transaction.created
issuing_transaction.purchase_details_receipt_updated
issuing_transaction.updated
mandate.updated
payment_intent.amount_capturable_updated
payment_intent.canceled
payment_intent.created
payment_intent.partially_funded
payment_intent.payment_failed
payment_intent.processing
payment_intent.requires_action
payment_intent.succeeded
payment_link.created
payment_link.updated
payment_method.attached
payment_method.automatically_updated
payment_method.detached
payment_method.updated
payout.canceled
payout.created
payout.failed
payout.paid
payout.reconciliation_completed
payout.updated
person.created
person.deleted
person.updated
plan.created
plan.deleted
plan.updated
price.created
price.deleted
price.updated
product.created
product.deleted
product.updated
promotion_code.created
promotion_code.updated
quote.accepted
quote.canceled
quote.created
quote.finalized
radar.early_fraud_warning.created
radar.early_fraud_warning.updated
refund.created
refund.failed
refund.updated
reporting.report_run.failed
reporting.report_run.succeeded
reporting.report_type.updated
reserve.hold.created
reserve.hold.updated
reserve.plan.created
reserve.plan.disabled
reserve.plan.expired
reserve.plan.updated
reserve.release.created
review.closed
review.opened
setup_intent.canceled
setup_intent.created
setup_intent.requires_action
setup_intent.setup_failed
setup_intent.succeeded
sigma.scheduled_query_run.created
source.canceled
source.chargeable
source.failed
source.mandate_notification
source.refund_attributes_required
source.transaction.created
source.transaction.updated
subscription_schedule.aborted
subscription_schedule.canceled
subscription_schedule.completed
subscription_schedule.created
subscription_schedule.expiring
subscription_schedule.released
subscription_schedule.updated
tax.settings.updated
tax_rate.created
tax_rate.updated
terminal.reader.action_failed
terminal.reader.action_succeeded
terminal.reader.action_updated
test_helpers.test_clock.advancing
test_helpers.test_clock.created
test_helpers.test_clock.deleted
test_helpers.test_clock.internal_failure
test_helpers.test_clock.ready
topup.canceled
topup.created
topup.failed
topup.reversed
topup.succeeded
transfer.created
transfer.reversed
transfer.updated
treasury.credit_reversal.created
treasury.credit_reversal.posted
treasury.debit_reversal.completed
treasury.debit_reversal.created
treasury.debit_reversal.initial_credit_granted
treasury.financial_account.closed
treasury.financial_account.created
treasury.financial_account.features_status_updated
treasury.inbound_transfer.canceled
treasury.inbound_transfer.created
treasury.inbound_transfer.failed
treasury.inbound_transfer.succeeded
treasury.outbound_payment.canceled
treasury.outbound_payment.created
treasury.outbound_payment.expected_arrival_date_updated
treasury.outbound_payment.failed
treasury.outbound_payment.posted
treasury.outbound_payment.returned
treasury.outbound_payment.tracking_details_updated
treasury.outbound_transfer.canceled
treasury.outbound_transfer.created
treasury.outbound_transfer.expected_arrival_date_updated
treasury.outbound_transfer.failed
treasury.outbound_transfer.posted
treasury.outbound_transfer.returned
treasury.outbound_transfer.tracking_details_updated
treasury.received_credit.created
treasury.received_credit.failed
treasury.received_credit.succeeded
treasury.received_debit.created
""".split()


def _studly_event(event: str) -> str:
    parts = [p for p in event.replace(".", "_").split("_") if p]
    return "".join(p[:1].upper() + p[1:] for p in parts)


_CASHIER_BY_METHOD = None
_CONST_REF = re.compile(
    r"(?<![.\w$\\])(?:(?P<qual>\\?(?:self|static|this|[A-Za-z_][\w\\]*))\s*(?:::|\.))?(?P<name>[A-Z][A-Z0-9_]{2,})\b")
_CONST_WRITE = re.compile(
    r"(?<![.\w$\\])(?:(?P<qual>\\?(?:self|static|this|[A-Za-z_][\w\\]*))\s*(?:::|\.))?"
    r"(?P<name>[A-Z][A-Z0-9_]{2,})\s*(?P<op>\]\s*=(?!=)|\]\s*:|=>|:)")
_SIG_LIT_WRITE = re.compile(r"""['"]((?:x[-_])?[\w-]*(?:signature|hmac)[\w-]*)['"]\s*(:|=>|,|\]\s*=(?!=))""", re.I)
_PHP_USE = re.compile(r"(?m)^\s*use\s+(?:const\s+)?\\?([^;]+);")
_TS_IMPORT = re.compile(r"""(?m)^[ \t]*import\s+(?:type\s+)?\{([^}]+)\}\s+from\s+['"]([^'"]+)['"]""")
_HAS_CONST = re.compile(r"[A-Z][A-Z0-9_]{2,}")


def _cashier_map():
    global _CASHIER_BY_METHOD
    if _CASHIER_BY_METHOD is None:
        _CASHIER_BY_METHOD = {_studly_event(e): e for e in _STRIPE_EVENTS}
    return _CASHIER_BY_METHOD
WC_JOB = "WebhookClient\\Jobs\\ProcessWebhookJob"
WC_PROFILE = "WebhookProfile\\WebhookProfile"


def norm_header(value: str) -> str | None:
    """Lower-case header name when it is a known provider header or a signature / hmac header."""
    v = (value or "").strip()
    if not v or len(v) > 80 or any(c in v for c in "\n\r{}[]"):
        return None
    if v.lower().startswith("http_"):
        v = v[5:]
    key = v.lower().replace("_", "-")
    if key in HEADERS or _SIG_NAME.fullmatch(key):
        return key
    return None


def cashier_event(name: str) -> str | None:
    """`handle<Studly>` back to a Stripe event type.

    Cashier dispatches `handle` + `Str::studly(str_replace('.', '_', $type))`, so
    `customer.subscription.created` and `invoice.payment_action_required` both become Studly words
    (`handleCustomerSubscriptionCreated`, `handleInvoicePaymentActionRequired`). `.` and `_` leave no
    trace in the method name. A published Stripe type is restored exactly. A method that matches none
    is the Studly words joined with `.`; that form is ambiguous with an unpublished name that used
    underscores. `handleWebhook` is the dispatcher, not an event.
    """
    if not name or not name.startswith("handle") or name == "handleWebhook":
        return None
    rest = name[6:]
    if not rest or not rest[0].isupper():
        return None
    known = _cashier_map().get(rest)
    if known:
        return known
    parts = re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*", rest)
    if not parts:
        return None
    return ".".join(p.lower() for p in parts)


def _block(src, i, hi):
    """Offset after the `}` closing the `{` at `i` (strings and comments skipped); hi when unbalanced."""
    depth, j = 0, i
    while j < hi:
        c = src[j]
        if c in "'\"`":
            k = j + 1
            while k < hi and src[k] != c:
                k += 2 if src[k] == "\\" else 1
            j = k + 1
            continue
        if c == "/" and src.startswith("//", j):
            j = src.find("\n", j)
            j = hi if j < 0 else j
            continue
        if c == "/" and src.startswith("/*", j):
            j = src.find("*/", j)
            j = hi if j < 0 else j + 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return hi


def _py_block(src, i, hi):
    """End of the indented block whose header line holds `i` (Python)."""
    ls = src.rfind("\n", 0, i) + 1
    ind = len(src[ls:i]) - len(src[ls:i].lstrip())
    j = src.find("\n", i)
    while 0 <= j < hi:
        nxt = src.find("\n", j + 1)
        line = src[j + 1:(nxt if nxt >= 0 else hi)]
        if line.strip() and len(line) - len(line.lstrip()) <= ind:
            return j + 1
        if nxt < 0:
            return hi
        j = nxt
    return hi


def _arms(src, lo, hi):
    """(arm start, `=>` offset, arm end) of the arms of a PHP match body src[lo:hi] (depth-0 `,` separated)."""
    out, depth, start, arrow, j = [], 0, lo, None, lo
    while j < hi:
        c = src[j]
        if c in "'\"":
            k = j + 1
            while k < hi and src[k] != c:
                k += 2 if src[k] == "\\" else 1
            j = k + 1
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif depth == 0 and src.startswith("=>", j) and arrow is None:
            arrow = j
        elif depth == 0 and c == "," and arrow is not None:
            out.append((start, arrow, j))
            start, arrow = j + 1, None
        j += 1
    if arrow is not None:
        out.append((start, arrow, hi))
    return out


def _skip_ws(src, i, hi):
    """Advance past a string or comment starting at i; 0 when src[i] is neither."""
    c = src[i] if i < hi else ""
    if c in "'\"":
        k = i + 1
        while k < hi and src[k] != c:
            k += 2 if src[k] == "\\" else 1
        return k + 1
    if c == "/" and src.startswith("//", i):
        j = src.find("\n", i)
        return hi if j < 0 else j
    if c == "/" and src.startswith("/*", i):
        j = src.find("*/", i)
        return hi if j < 0 else j + 2
    return 0


def _arm_end(src, j, hi):
    """End of a when/match arm that does not open with `{`: the next label, or a depth-0 comma."""
    depth, i = 0, j
    while i < hi:
        nxt = _skip_ws(src, i, hi)
        if nxt:
            i = nxt
            continue
        c = src[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0:
                return i
            depth -= 1
        elif depth == 0 and c == ",":
            return i + 1
        elif depth == 0 and c == "\n":
            rest = src[i + 1:hi].lstrip(" \t")
            if rest.startswith(("\"", "'", "else", "_", "default", "}")):
                return i + 1
        i += 1
    return hi


def _dispatch_arms(src, body, end, arrow):
    """(literal, literal pos, arrow pos, arm end) for Kotlin `when` (`->`) or Rust `match` (`=>`) arms.

    Labels are string literals at depth 0 or 1 (`"push"`, `Some("push")`, `"a", "b"`, `"a" | "b"`).
    `else` / `_` arms are skipped.
    """
    depth, i, lits = 0, body, []
    while i < end:
        nxt = _skip_ws(src, i, end)
        if nxt:
            if depth <= 1 and src[i] in "'\"":
                k = i + 1
                while k < end and src[k] != src[i]:
                    k += 2 if src[k] == "\\" else 1
                lits.append((src[i + 1:k], i))
            i = nxt
            continue
        c = src[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif depth == 0 and src.startswith(arrow, i) and not src.startswith(">=", i):
            j = i + len(arrow)
            while j < end and src[j] in " \t":
                j += 1
            if j < end and src[j] == "{":
                b_end = _block(src, j, end)
            else:
                b_end = _arm_end(src, j, end)
            for lit, pos in lits:
                yield lit, pos, i, b_end
            lits = []
            i = b_end
            continue
        i += 1


class Webhooks(BrokerScan):
    def __init__(self, project, b, sock=None):
        super().__init__(project, b, sock)
        self.app = getattr(project, "name", None) or self.root.name

    def run(self) -> dict:
        from .tests_index import is_test_node
        self.is_test_node = is_test_node
        self.out = defaultdict(list)
        self.callees = defaultdict(list)          # fn -> [(dst, kind, line, confidence)]
        self._index_constants()
        self.bind_webhook_client()
        handlers = defaultdict(list)
        mw = defaultdict(list)
        callers = defaultdict(set)
        for e in self.b.edges.values():
            if e.kind in ("CALLS", "DISPATCHES"):
                self.callees[e.src].append((e.dst, e.kind, e.line, e.confidence))
                callers[e.dst].add(e.src)
            elif e.kind == "ROUTES_TO":
                handlers[e.src].append(e.dst)
            elif e.kind == "USES_MIDDLEWARE" and e.src.startswith("route:"):
                mw[e.src].append(e.dst)
        # shared helpers (logging, responses) called from many functions: their headers say nothing about the route
        self.shared = {d for d, c in callers.items() if len(c) > SHARED_CALLERS}
        for rid, n in list(self.b.nodes.items()):
            if n.kind != "route" or not handlers.get(rid):
                continue
            self.route(rid, n, handlers[rid], mw.get(rid, []))
        self.octokit_on()
        self.senders(callers)
        out = {k: v for k, v in self.st.items() if v}
        if out and self.samples:
            out["samples"] = dict(self.samples)
        return out

    # ------------------------------------------------------------ helpers
    def body(self, nid):
        n = self.b.nodes.get(nid)
        if n is None or n.kind not in ("function", "method") or not n.file or not n.line or not n.file.endswith(CODE_EXT):
            return None
        src = self.s.text(n.file)
        if not src:
            return None
        lo = self.s.off(n.file, n.line)
        end = n.end_line or n.line
        hi = self.s.off(n.file, end + 1) if end < len(self.s.lines[n.file]) else len(src)
        return n.file, src, lo, hi

    def tree(self, roots, depth=2):
        seen, order, frontier = set(), [], list(roots)
        for d in range(depth + 1):
            nxt = []
            for f in frontier:
                if f in seen or f not in self.b.nodes or self.is_test_node(self.b.nodes[f]):
                    continue
                seen.add(f)
                order.append(f)
                if d < depth:
                    nxt += [c[0] for c in self.callees.get(f, ())]
            frontier = nxt
        return order

    def finds(self, rx, file, src, lo, hi):
        for m in rx.finditer(src, lo, hi):
            if not self.s.masked(file, m.start()) or m.group(0)[:1] in "'\"":
                yield m

    def verify_obj(self, file, src, var):
        """(provider, how) when `var` was built by a webhook library's verifier class (`new Webhook(secret)`)."""
        if var in ("webhooks",) and re.search(r"""['"]@octokit/webhooks-methods['"]""", src):
            return "github", "@octokit/webhooks-methods verify"
        for cls, hint, prov, how in VERIFY_OBJ:
            if hint.search(src) and re.search(rf"\b(?:this\.|self\.|\$)?{re.escape(var)}\s*(?::\s*\w+\s*)?=\s*(?:new\s+)?(?:\w+\.)?{cls}\s*\(", src):
                return prov, how
        if var in ("webhooks", "Webhooks") and VERIFY_OBJ[0][1].search(src):
            return "github", "@octokit/webhooks verify"
        return None

    # ------------------------------------------------------------ constants and framework receivers
    def _index_constants(self):
        """Class and module constants whose value is a signature header, plus explicit imports of those names.

        A use resolves in the same class (including an ancestor) or the same module, or through an import
        (`use`, `use const`, `import { NAME }`). A name that is only defined somewhere else stays unknown.
        """
        self.class_const = defaultdict(dict)
        self.extends = defaultdict(list)
        for e in self.b.edges.values():
            if e.kind != "EXTENDS" or ":" not in e.src or ":" not in e.dst:
                continue
            self.extends[e.src.split(":", 1)[1]].append(e.dst.split(":", 1)[1])
        for n in self.b.nodes.values():
            if n.kind != "constant" or not n.fqn or "::" not in n.fqn or not n.file or not n.line:
                continue
            src = self.s.text(n.file) or ""
            lines = src.splitlines()
            if not 1 <= n.line <= len(lines):
                continue
            chunk = "\n".join(lines[n.line - 1:min(len(lines), n.line + 2)])
            m = re.search(r"""=\s*(['"])(?P<val>[^'"\n]{1,120})\1""", chunk)
            hdr = norm_header(m.group("val")) if m else None
            if not hdr:
                continue
            fqcn, name = n.fqn.rsplit("::", 1)
            self.class_const[fqcn][name] = hdr
        owned = defaultdict(set)
        for fq, mp in self.class_const.items():
            node = self.b.nodes.get(f"class:{fq}")
            if node and node.file:
                owned[str(node.file)].update(mp)
        self.module_const = {}
        self.imported_header = {}
        self.imported_class = {}
        for f in sorted(self.s.files):
            if not str(f).endswith(CODE_EXT):
                continue
            src = self.s.text(f) or ""
            found = {}
            for rx in (_CONST_DEF, _PY_CONST_DEF):
                for m in rx.finditer(src):
                    if m.group("name") in owned.get(str(f), ()):
                        continue
                    hdr = norm_header(m.group("val"))
                    if hdr:
                        found[m.group("name")] = hdr
            if found:
                self.module_const[str(f)] = found
        for f in sorted(self.s.files):
            if not str(f).endswith(CODE_EXT):
                continue
            self._index_imports(str(f), self.s.text(f) or "")

    def _header_on_class(self, fqcn, name):
        seen = set()
        stack = [fqcn]
        while stack:
            c = stack.pop()
            if not c or c in seen:
                continue
            seen.add(c)
            hit = self.class_const.get(c, {}).get(name)
            if hit:
                return hit
            stack.extend(self.extends.get(c, ()))
        return None

    def _resolve_module(self, file, spec):
        if not spec.startswith("."):
            return None
        parts = str(file).rsplit("/", 1)[0].split("/") if "/" in str(file) else []
        if parts == [""]:
            parts = []
        for p in spec.split("/"):
            if p in ("", "."):
                continue
            if p == "..":
                if parts:
                    parts.pop()
                continue
            parts.append(p)
        stem = "/".join(parts)
        files = {str(f) for f in self.s.files}
        for ext in (".ts", ".tsx", ".mts", ".js", ".mjs", ".cjs", ".py", ".kt", ".rs", ".php"):
            cand = stem + ext
            if cand in files:
                return cand
        return None

    def _index_imports(self, file, src):
        headers, classes = {}, {}
        for m in _PHP_USE.finditer(src):
            clause = m.group(1).strip()
            if clause.startswith("function "):
                continue
            alias = None
            if re.search(r"\sas\s", clause):
                clause, alias = re.split(r"\s+as\s+", clause, maxsplit=1)
                alias = alias.strip()
            clause = clause.strip().lstrip("\\")
            if "::" in clause:
                fq, name = clause.split("::", 1)
                hdr = self._header_on_class(fq, name)
                if hdr:
                    headers[alias or name] = hdr
            else:
                classes[alias or clause.split("\\")[-1]] = clause
        for m in _TS_IMPORT.finditer(src):
            target = self._resolve_module(file, m.group(2))
            mods = self.module_const.get(target) or {}
            for part in m.group(1).split(","):
                part = part.strip()
                if not part or part.startswith("type "):
                    continue
                if " as " in part:
                    orig, local = [x.strip() for x in part.split(" as ", 1)]
                else:
                    orig = local = part.split(":")[0].strip()
                if orig in mods:
                    headers[local] = mods[orig]
        self.imported_header[file] = headers
        self.imported_class[file] = classes

    def resolve_header(self, file, name, qual, class_fqcn):
        """Header for this constant use, or None when the name is not in scope."""
        file = str(file)
        qual = (qual or "").strip()
        if qual in ("self", "static", "this", "$this"):
            return self._header_on_class(class_fqcn, name) if class_fqcn else None
        if qual:
            fq = qual.lstrip("\\")
            if fq not in self.class_const and f"class:{fq}" not in self.b.nodes:
                fq = (self.imported_class.get(file) or {}).get(qual.lstrip("\\"))
            if not fq:
                return None
            return self._header_on_class(fq, name)
        if name in (self.module_const.get(file) or {}):
            return self.module_const[file][name]
        return (self.imported_header.get(file) or {}).get(name)

    def header_consts(self, file, src, lo, hi, class_fqcn=None):
        """(header, file:line) for a constant in scope used in this span whose value is a signature header."""
        if not _HAS_CONST.search(src, lo, hi):
            return
        seen = set()
        for m in _CONST_REF.finditer(src, lo, hi):
            if self.s.masked(file, m.start()):
                continue
            hdr = self.resolve_header(file, m.group("name"), m.group("qual"), class_fqcn)
            if not hdr or hdr in seen:
                continue
            seen.add(hdr)
            yield hdr, f"{file}:{self.s.line_of(file, m.start())}"

    def signature_write(self, file, text, base, class_fqcn=None):
        """(offset in text, header name) of a signature header written here, including one named by a constant in scope."""
        found = []
        for m in _SIG_LIT_WRITE.finditer(text):
            if self._write_ok(file, text, base, m.start(), m.group(1), m.group(2)):
                found.append((m.start(), m.group(1)))
        for m in _CONST_WRITE.finditer(text):
            hdr = self.resolve_header(file, m.group("name"), m.group("qual"), class_fqcn)
            if hdr and self._write_ok(file, text, base, m.start(), hdr, m.group("op")):
                found.append((m.start(), hdr))
        return min(found) if found else None

    def _write_ok(self, file, text, base, start, name, op):
        if self.s.masked(file, base + start):
            return False
        before = text[max(0, start - 16):start]
        if re.search(r"(?:\bget|\bheader|getHeader|\bheaders\s*\[)\s*\(?\s*$", before) and not str(op).startswith("]"):
            return False
        if "-" not in name and not re.search(r"header", text[max(0, start - 300):start], re.I):
            return False
        return True

    def _parents(self):
        if getattr(self, "_parent_map", None) is None:
            mp = defaultdict(list)
            for e in self.b.edges.values():
                if e.kind in ("EXTENDS", "IMPLEMENTS"):
                    mp[e.src].append(e.dst)
            self._parent_map = mp
        return self._parent_map

    def _is_a(self, class_id, suffix) -> bool:
        seen = set()
        stack = [class_id]
        while stack:
            c = stack.pop()
            if not c or c in seen:
                continue
            seen.add(c)
            node = self.b.nodes.get(c)
            label = ((node.fqn or node.name) if node else "") or c
            if str(label).endswith(suffix) or str(c).endswith(suffix):
                return True
            stack.extend(self._parents().get(c, ()))
        return False

    def _class_of(self, nid):
        if not nid or not str(nid).startswith("method:") or "::" not in nid:
            return None
        return "class:" + nid[len("method:"):].rsplit("::", 1)[0]

    def _method(self, fqcn, name):
        if not fqcn or not name:
            return None
        fqcn = fqcn.strip("\\")
        nid = f"method:{fqcn}::{name}"
        if nid in self.b.nodes:
            return nid
        low = name.lower()
        prefix = f"method:{fqcn}::"
        for n in self.b.nodes.values():
            if n.id.startswith(prefix) and (n.name or "").lower() == low:
                return n.id
        return None

    def _subtypes(self, suffix):
        out = []
        for n in self.b.nodes.values():
            if n.kind == "class" and self._is_a(n.id, suffix) and not str(n.fqn or "").endswith(suffix):
                out.append(n.id)
        return out

    def cashier_methods(self, hs, rn):
        """(method id, event, file, line) for Cashier `handle<Event>` methods behind this route."""
        classes = set()
        for h in hs or []:
            cid = self._class_of(h)
            if cid and self._is_a(cid, CASHIER_PARENT):
                classes.add(cid)
        ctrl = (rn.attrs or {}).get("controller") if rn is not None else None
        if ctrl:
            cid = f"class:{ctrl}"
            if cid in self.b.nodes and self._is_a(cid, CASHIER_PARENT):
                classes.add(cid)
        out = []
        for cid in classes:
            fq = cid.split(":", 1)[1]
            prefix = f"method:{fq}::"
            for n in self.b.nodes.values():
                if n.kind == "method" and n.id.startswith(prefix):
                    ev = cashier_event(n.name or "")
                    if ev:
                        out.append((n.id, ev, n.file, n.line))
        return out

    def _secret_present(self, expr: str | None) -> bool:
        if not expr:
            return False
        e = expr.strip().rstrip(",").strip()
        if e in ("null", "false", "''", '""'):
            return False
        if re.fullmatch(r"""['"]\s*['"]""", e):
            return False
        return True

    def _default_validator(self, name: str | None) -> bool:
        if not name:
            return False
        return name.replace("/", "\\").rstrip("\\").endswith(_DEFAULT_VALIDATOR)

    def _class_checks(self, fqcn: str | None) -> bool:
        """True when a project class's own body computes or compares a signature."""
        if not fqcn:
            return False
        fqcn = fqcn.strip("\\")
        node = self.b.nodes.get(f"class:{fqcn}")
        if node is None:
            for n in self.b.nodes.values():
                if n.kind == "class" and str(n.fqn or "").endswith(fqcn):
                    node = n
                    break
        if node is None or not node.file or not node.line:
            return False
        src = self.s.text(node.file) or ""
        lo = self.s.off(node.file, node.line)
        end = node.end_line or node.line
        lines = self.s.lines[node.file] if node.file in self.s.lines else []
        hi = self.s.off(node.file, end + 1) if end < len(lines) else len(src)
        chunk = src[lo:hi]
        return bool(HMAC_RX.search(chunk) or CT_RX.search(chunk))

    def wc_profiles(self):
        """spatie/laravel-webhook-client configs: name, header, job, profile, and whether the validator counts."""
        out = []
        for f in sorted(self.s.files):
            if not str(f).endswith("webhook-client.php"):
                continue
            src = self.s.text(f) or ""
            for m in re.finditer(r"""['"](?:process_webhook_job|name)['"]\s*=>""", src):
                lo = src.rfind("[", max(0, m.start() - 1200), m.start())
                hi = src.find("]", m.end(), min(len(src), m.end() + 1200))
                if lo < 0 or hi < 0:
                    continue
                w = src[lo:hi]
                name = re.search(r"""['"]name['"]\s*=>\s*['"]([^'"]+)['"]""", w)
                if not name:
                    continue
                if any(p["name"] == name.group(1) for p in out):
                    continue
                job = re.search(r"""['"]process_webhook_job['"]\s*=>\s*\\?([A-Za-z_\\]+)::class""", w)
                header = re.search(r"""['"]signature_header_name['"]\s*=>\s*['"]([^'"]+)['"]""", w)
                profile = re.search(r"""['"]webhook_profile['"]\s*=>\s*\\?([A-Za-z_\\]+)::class""", w)
                validator = re.search(r"""['"]signature_validator['"]\s*=>\s*\\?([A-Za-z_\\]+)::class""", w)
                secret = re.search(r"""['"]signing_secret['"]\s*=>\s*([^,\n]+)""", w)
                vname = validator.group(1).strip("\\") if validator else None
                has_secret = self._secret_present(secret.group(1) if secret else None)
                verified, how = False, None
                if vname is None and not has_secret:
                    verified = False
                elif vname is None or self._default_validator(vname):
                    if has_secret:
                        verified, how = True, "spatie/laravel-webhook-client"
                elif self._class_checks(vname):
                    verified, how = True, "spatie/laravel-webhook-client custom validator"
                out.append({
                    "name": name.group(1),
                    "header": header.group(1) if header else None,
                    "job": job.group(1).strip("\\") if job else None,
                    "profile": profile.group(1).strip("\\") if profile else None,
                    "verified": verified,
                    "how": how,
                })
        return out

    def bind_webhook_client(self):
        """Point Route::webhooks routes at the configured ProcessWebhookJob and webhook profile.

        A config name that is not in `config/webhook-client.php` is left unbound.
        """
        by_name = {p["name"]: p for p in self.wc_profiles()}
        self.wc_by_name = by_name
        for rid, n in list(self.b.nodes.items()):
            if n.kind != "route" or (n.attrs or {}).get("webhook_framework") != "laravel-webhook-client":
                continue
            raw = (n.attrs or {}).get("webhook_client") or "default"
            spec = by_name.get(raw) if isinstance(raw, str) else None
            if spec is None:
                continue
            if spec.get("header"):
                n.attrs["webhook_header"] = spec["header"]
            targets = []
            j = self._method(spec.get("job") or "", "handle")
            p = self._method(spec.get("profile") or "", "shouldProcess")
            targets += [t for t in (j, p) if t]
            for t in dict.fromkeys(targets):
                self.b.add_edge(rid, t, "ROUTES_TO", n.file, n.line, HEURISTIC, how="webhook-client profile")

    # ------------------------------------------------------------ receivers
    def verifying_middleware(self, rn, mws) -> list[str]:
        """Route middleware names, and the classes they resolved to, that read as a signature / webhook check.

        Aliases are matched with `.` and `-` read as `_`: `verify.twilio.signature`, `verify-carrier-webhook`.
        """
        cands = [x for x in (rn.attrs or {}).get("middleware") or [] if isinstance(x, str)]
        for m in mws:
            cls = m.split(":", 1)[-1].split("::", 1)[0].rsplit("\\", 1)[-1]
            if cls:
                cands.append(cls)
        return [x for x in dict.fromkeys(cands) if MW_RX.search(re.sub(r"[.\-]", "_", x))]

    def route(self, rid, rn, hs, mws):
        extra = [mid for mid, _ev, _f, _ln in self.cashier_methods(hs, rn)]
        for mid in extra:
            if mid not in hs:
                self.b.add_edge(rid, mid, "ROUTES_TO", rn.file, rn.line, HEURISTIC, how="cashier webhook")
        hs = list(dict.fromkeys(list(hs) + extra))
        fns = self.tree(list(hs) + list(mws))
        headers, sig_hdrs, verified = {}, [], None
        hm = ct = None
        bodies = {}
        for f in fns:
            bd = self.body(f)
            if bd is None:
                continue
            bodies[f] = bd
            file, src, lo, hi = bd
            own = f in hs or f in mws or f not in self.shared
            for m in (HDR_RX.finditer(src, lo, hi) if own else ()):
                h = m.group(1).lower().replace("_", "-")
                headers.setdefault(h, f"{file}:{self.s.line_of(file, m.start())}")
            if file.endswith(".py") and own:
                for m in PY_HDR_RX.finditer(src, lo, hi):
                    headers.setdefault(m.group(1).lower().replace("_", "-"), f"{file}:{self.s.line_of(file, m.start())}")
            for m in (SIG_HDR_RX.finditer(src, lo, hi) if own else ()):
                sig_hdrs.append(m.group(1).lower())
            if own:
                cid = self._class_of(f)
                for hval, loc in self.header_consts(file, src, lo, hi, cid.split(":", 1)[1] if cid else None):
                    if hval in HEADERS:
                        headers.setdefault(hval, loc)
                    else:
                        sig_hdrs.append(hval)
            for rx, prov, how, hint in VERIFY:
                if verified:
                    break
                if hint is not None and not hint.search(src):
                    continue
                for m in self.finds(rx, file, src, lo, hi):
                    p_h = (prov, how) if prov else self.verify_obj(file, src, m.group(1))
                    if p_h:
                        verified = (p_h[0], p_h[1], f"{file}:{self.s.line_of(file, m.start())}")
                        break
            if hm is None:
                hm = next((f"{file}:{self.s.line_of(file, m.start())}" for m in self.finds(HMAC_RX, file, src, lo, hi)), None)
            if ct is None:
                ct = next((f"{file}:{self.s.line_of(file, m.start())}" for m in self.finds(CT_RX, file, src, lo, hi)), None)
        provs = [HEADERS[h][0] for h in headers]
        provider = next((HEADERS[h][0] for h in headers if HEADERS[h][1] == "event"), provs[0] if provs else None)
        roles = {HEADERS[h][1] for h in headers}
        if verified is None:
            if ct and "token" in roles and "sig" not in roles:
                verified = (provider, "token header", ct)
            elif hm and (ct or "sig" in roles) and (provs or sig_hdrs):
                verified = (provider or "hmac", "HMAC signature" + ("" if ct else " (plain comparison)"), ct or hm)
            else:
                names = self.verifying_middleware(rn, mws)
                if names and (provs or sig_hdrs or re.search(r"(?i)hook", rn.id)):
                    nm = PROVIDER_NAMES.search(names[0])
                    verified = (provider or (nm.group(1).lower().replace("_", "") if nm else "webhook"), f"middleware {names[0]}",
                                f"{rn.file}:{rn.line}")
        fw = (rn.attrs or {}).get("webhook_framework")
        if (fw == "laravel-cashier" or extra) and verified is None:
            verified = ("stripe", "Laravel Cashier", f"{rn.file}:{rn.line}")
        if fw == "laravel-webhook-client" and verified is None:
            raw = (rn.attrs or {}).get("webhook_client") or "default"
            spec = (getattr(self, "wc_by_name", None) or {}).get(raw) if isinstance(raw, str) else None
            if spec and spec.get("verified"):
                hdr = norm_header(spec.get("header") or "") or "signature"
                prov0 = HEADERS.get(hdr, (None,))[0]
                verified = (prov0 or "webhook", spec["how"], f"{rn.file}:{rn.line}")
                if hdr in HEADERS:
                    headers.setdefault(hdr, verified[2])
                else:
                    sig_hdrs.append(hdr)
        if verified is None and not provs:
            return
        prov = verified[0] if verified else provider
        if verified and provider and verified[0] in ("hmac", "webhook"):
            prov = provider
        if prov in (None, "hmac", "webhook"):
            nm = PROVIDER_NAMES.search(rn.id) or next((m for m in (PROVIDER_NAMES.search(h) for h in hs) if m), None)
            if nm:
                prov = nm.group(1).lower().replace("_", "")
        if verified and verified[1] == "Laravel Cashier":
            prov = "stripe"
        wh = {"provider": prov, "verified": bool(verified)}
        if verified:
            wh.update(how=verified[1], check=verified[2])
            if verified[1].startswith(("Stripe", "svix", "standardwebhooks")):
                wh["replay_protection"] = "timestamp tolerance (library)"
            if verified[1].endswith("(plain comparison)"):
                wh["constant_time"] = False
            self.st["webhook_receivers_verified"] += 1
        else:
            wh["headers"] = sorted(headers)
            self.st["webhook_receivers_unverified"] += 1
            self.miss("webhook_unverified", rid)
        rn.attrs["webhook"] = wh
        events = self.events(rid, prov, fns, bodies, headers, verified)
        if verified and verified[1] == "Laravel Cashier":
            guards = [f"webhook signature ({verified[1]})"]
            for mid, ev, file, line in self.cashier_methods(hs, rn):
                if not file or not line:
                    continue
                pos = self.s.off(file, line)
                self.recv(rid, prov, ev, mid, file, pos, pos, pos, HEURISTIC, guards)
                self.flush_branches(mid)
                events.add(ev)
        if events:
            wh["events"] = sorted(events)

    def events(self, rid, prov, fns, bodies, headers, verified):
        got = set()
        guards = [f"webhook signature ({verified[1]})"] if verified else []
        for f in fns:
            if f not in bodies:
                continue
            file, src, lo, hi = bodies[f]
            py = file.endswith(".py")
            text = src[lo:hi]
            # event-type subjects: the verified event var's type, the event header value, generic payload names
            ev_vars = set(GENERIC_EVENT_VARS)
            strong = set()
            for m in re.finditer(r"(\$?\w+)\s*(?::\s*[\w<>\[\]]+\s*)?=\s*(?:await\s+)?[^;\n]*?(?:construct_?[Ee]vent(?:Async)?|\.verify)\s*\(", text):
                strong.add(m.group(1).lstrip("$"))
            ev_vars |= strong
            vv = "|".join(sorted(map(re.escape, ev_vars), key=len, reverse=True))
            keys = "|".join(TYPE_KEYS)
            type_expr = (rf"(?:\$(?:this->)?|this\.)?(?P<v>{vv})(?:\s*(?:\.|->|\?\.)\s*(?:{keys})\b(?!\s*\()"
                         rf"|\s*\[\s*['\"](?:{keys})['\"]\s*\]|\s*\.\s*get\s*\(\s*['\"](?:{keys})['\"]\s*\))"
                         rf"|data_get\s*\(\s*\$(?:this->)?(?P<v2>{vv})\s*,\s*['\"](?:{keys})['\"]\s*\)")
            hdr_expr = r"""[^;\n]*?['"](?:HTTP_)?(?:""" + "|".join(re.escape(h).replace("\\-", "[-_]") for h, (_p, r) in HEADERS.items() if r == "event") + r""")(?:\.\d+)?['"][^;\n]*"""
            subjects = [(type_expr, None)]
            # aliases: `$type = data_get($event, 'type')`, `name = req.headers['x-github-event']`
            alias = {}
            wrap = r"(?:(?:\$this->|self::|static::|Str::)?\w+\s*\(\s*(?:\((?:string|str)\)\s*)?)?"
            for m in re.finditer(rf"(\$?\w+)\s*(?::\s*\w+\s*)?=\s*{wrap}(?:{type_expr})", text):
                v = m.group("v") or m.group("v2")
                alias[m.group(1)] = EXACT if v in strong else HEURISTIC
            for m in re.finditer(rf"(?:const|let|var|\$|\b)(\$?\w+)\s*(?::\s*\w+\s*)?=\s*{hdr_expr}", text, re.I):
                alias[m.group(1).lstrip("$") if not m.group(0).startswith("$") else m.group(1)] = EXACT
            for a in alias:
                subjects.append((r"(?<![\w$.>])(?:\$|this\.|self\.)?" + re.escape(a.lstrip("$")) + r"\b", alias[a]))
            subjects.append((r"[\w.$>\-\[\]'\"]*\[\s*['\"](?:" + "|".join(re.escape(h) for h, (_p, r) in HEADERS.items() if r == "event") + r")['\"]\s*\]", EXACT))
            for subj, conf0 in subjects:
                for lit, pos, b_lo, b_hi, v in self.compares(file, src, lo, hi, subj, py):
                    if not EVENT_LIT.fullmatch(lit):
                        continue
                    conf = conf0 or (EXACT if (v in strong) else HEURISTIC)
                    if verified is None:
                        conf = HEURISTIC if conf == HEURISTIC else EXACT
                    self.recv(rid, prov, lit, f, file, pos, b_lo, b_hi, conf, guards)
                    got.add(lit)
            self.flush_branches(f)
        return got

    def compares(self, file, src, lo, hi, subj, py):
        """(literal, pos, branch lo, branch hi, var) for `switch (subj) { case 'x': }`, `match subj: case 'x':`,
        `subj === 'x'` / `'x' == subj`."""
        def q(n):
            return rf"""(?P<q{n}>['"])(?P<l{n}>(?:(?!(?P=q{n}))[^\\\n])+)(?P=q{n})"""

        def var(m):
            g = m.groupdict()
            return g.get("v") or g.get("v2")
        for m in re.finditer(rf"\bswitch\s*\(\s*(?:{subj})\s*\)\s*\{{", src[lo:hi]):
            s0 = lo + m.start()
            if self.s.masked(file, s0):
                continue
            body = lo + m.end()
            end = _block(src, body - 1, hi)
            cases, depth = [], 0
            for c in re.finditer(r"[{}]|\bcase\s+" + q(0) + r"\s*:|\bdefault\s*:", src[body:end]):
                t = c.group(0)
                if t == "{":
                    depth += 1
                elif t == "}":
                    depth -= 1
                elif depth == 0:
                    cases.append((body + c.start(), body + c.end(), c.group("l0") if t.startswith("case") else None))
            for i, (cs, ce, lit) in enumerate(cases):
                if lit is None:
                    continue
                nxt = i + 1
                while nxt < len(cases) and src[ce:cases[nxt][0]].strip() == "":
                    nxt += 1                                # fall-through labels share the next body
                b_hi = cases[nxt][0] if nxt < len(cases) else end
                yield lit, cs, ce, b_hi, var(m)
        if not py:
            # PHP 8 `match ($event) { 'push' => .., 'a', 'b' => .., default => .. }`
            for m in re.finditer(rf"\bmatch\s*\(\s*(?:{subj})\s*\)\s*\{{", src[lo:hi]):
                body = lo + m.end()
                if self.s.masked(file, body):
                    continue
                end = _block(src, body - 1, hi) - 1
                for a_lo, arrow, a_hi in _arms(src, body, end):
                    for lm in re.finditer(r"""(['"])((?:(?!\1)[^\\\n])+)\1""", src[a_lo:arrow]):
                        yield lm.group(2), a_lo + lm.start(), arrow, a_hi, var(m)
        # Kotlin `when (event.type) { "push" -> ... }` and Rust `match event_name { "push" => ... }`
        for kind, arrow in (("when", "->"), ("match", "=>")):
            if kind == "when":
                rx = rf"\bwhen\s*\(\s*(?:{subj}){_TAIL}\s*\)\s*\{{"
            else:
                rx = rf"\bmatch\s+(?:{subj}){_TAIL}\s*\{{"
            for m in re.finditer(rx, src[lo:hi]):
                body = lo + m.end()
                if self.s.masked(file, lo + m.start()):
                    continue
                end = _block(src, body - 1, hi) - 1
                for lit, pos, arrow_at, b_hi in _dispatch_arms(src, body, end, arrow):
                    yield lit, pos, arrow_at, b_hi, var(m)
        if py:
            for m in re.finditer(rf"^[ \t]*match\s+(?:{subj})\s*:", src[lo:hi], re.M):
                s0 = lo + m.start()
                end = _py_block(src, s0, hi)
                for c in re.finditer(r"^[ \t]*case\s+" + q(0) + r"\s*:", src[s0:end], re.M):
                    p = s0 + c.start()
                    yield c.group("l0"), p, p, _py_block(src, p, end), var(m)
        for rx in (rf"(?:{subj})\s*(?P<op>===|==|!==|!=)\s*{q(1)}", rf"{q(1)}\s*(?P<op>===|==)\s*(?:{subj})"):
            for m in re.finditer(rx, src[lo:hi]):
                p0 = lo + m.start()
                if self.s.masked(file, p0):
                    continue
                lit, v = m.group("l1"), var(m)
                if "!" in m.group("op"):
                    yield lit, p0, lo + m.end(), hi, v
                    continue
                if py:
                    yield lit, p0, lo + m.end(), _py_block(src, p0, hi), v
                    continue
                e0 = lo + m.end()
                nl = src.find("\n", e0)
                ob = src.find("{", e0)
                if 0 <= ob < hi and (nl < 0 or ob <= nl):
                    yield lit, p0, e0, _block(src, ob, hi), v
                else:
                    yield lit, p0, e0, (nl if nl >= 0 else hi), v

    def recv(self, rid, prov, lit, fn, file, pos, b_lo, b_hi, conf, guards):
        line = self.s.line_of(file, pos)
        name = f"{prov or 'webhook'}:{lit}"
        key = (name, fn)
        if key not in self.done:
            self.done.add(key)
            protocol_receive(self.b, "webhook", name, fn, file, line, conf, guards=guards,
                             node_attrs={"provider": prov, "event": lit}, route=rid, how="event type")
            self.st["webhook_events"] += 1
        lo_l, hi_l = line, self.s.line_of(file, max(b_lo, b_hi - 1))
        self.out[fn].append((name, lo_l, hi_l, file, line, conf, guards, rid))

    def flush_branches(self, fn):
        """Thin branches: a branch calling at most two project functions not shared by other branches of the same
        dispatch also makes those functions receivers."""
        rows = self.out.pop(fn, [])
        if not rows:
            return
        calls = [(d, k, ln, c) for d, k, ln, c in self.callees.get(fn, ()) if ln and d in self.b.nodes and d not in self.shared
                 and self.b.nodes[d].kind in ("function", "method") and not self.is_test_node(self.b.nodes[d])
                 and not HELPER_NAME.match(re.split(r"[.:#]+", self.b.nodes[d].name or d)[-1])]
        per = []
        branches = {}                              # labels sharing one body (fall-through, `'a', 'b' =>`) count once
        for name, lo_l, hi_l, file, line, conf, guards, rid in rows:
            per.append({d for d, _k, ln, _c in calls if lo_l <= ln <= hi_l and d != fn})
            branches.setdefault((file, hi_l), set()).update(per[-1])
        shared = defaultdict(int)
        for ds in branches.values():
            for d in ds:
                shared[d] += 1
        for (name, lo_l, hi_l, file, line, conf, guards, rid), ds in zip(rows, per):
            if len(branches.get((file, hi_l)) or ()) > 2:
                continue                           # a branch doing several things inline: no single handler
            own = [d for d in ds if shared[d] == 1] if len(branches) > 1 else list(ds)
            if not 1 <= len(own) <= 2:
                continue
            for d in sorted(own):
                if (name, d) in self.done:
                    continue
                self.done.add((name, d))
                c = next((cc for dd, _k, ln, cc in calls if dd == d and lo_l <= ln <= hi_l), conf)
                protocol_receive(self.b, "webhook", name, d, file, line, HEURISTIC if HEURISTIC in (c, conf) else c,
                                 guards=guards, route=rid, how="event branch", via=fn)
                self.st["webhook_branch_handlers"] += 1

    # ------------------------------------------------------------ senders
    def senders(self, callers):
        """Outbound webhooks: svix `message.create(app, { eventType })`, spatie/laravel-webhook-server
        `WebhookCall::create()..->dispatch()`, and functions that POST with a signature header they compute (an HMAC
        here or in a function called from here) -> SENDS_TO endpoint:webhook:<this app>:<event>."""
        q = r"""(['"])((?:(?!\1)[^\\\n])+)\1"""
        for nid, n in list(self.b.nodes.items()):
            if n.kind not in ("function", "method") or self.is_test_node(n):
                continue
            bd = self.body(nid)
            if bd is None:
                continue
            file, src, lo, hi = bd
            text = src[lo:hi]
            if file.endswith(JS_EXT + (".py", ".php")) and "message" in text and SVIX_HINT.search(src):
                for m in SVIX_SEND.finditer(text):
                    if self.s.masked(file, lo + m.start()):
                        continue
                    a = text[m.end():m.end() + 600]
                    ev = re.search(r"(?:eventType|event_type)\s*(?::|=>?)\s*" + q, a)
                    self.send_event(nid, file, lo + m.start(), ev.group(2) if ev else None, "svix", "svix message.create")
            if file.endswith(".php") and "WebhookCall" in text:
                for m in re.finditer(r"\bWebhookCall\s*::\s*create\s*\(", text):
                    if self.s.masked(file, lo + m.start()):
                        continue
                    end = text.find("->dispatch", m.end())
                    chain = text[m.end():end if end > 0 else m.end() + 800]
                    ev = re.search(r"['\"](?:event|type|event_type|eventType)['\"]\s*=>\s*" + q, chain)
                    self.send_event(nid, file, lo + m.start(), ev.group(2) if ev else None, "spatie/laravel-webhook-server",
                                    "WebhookCall::create")
            if not HTTP_OUT.search(text):
                continue
            cid = self._class_of(nid)
            hdr = self.signature_write(file, text, lo, cid.split(":", 1)[1] if cid else None)
            if hdr is None:
                continue
            signed = HMAC_RX.search(text) or SIGN_CALL.search(text) or any(
                (cb := self.body(d)) is not None and HMAC_RX.search(cb[1], cb[2], cb[3]) for d, _k, _l, _c in self.callees.get(nid, ()))
            if not signed:
                continue
            self.st["webhook_signed_senders"] += 1
            ev = re.search(r"""(?:\b|['"])(?:triggerEvent|eventType|event_type|event)['"]?\s*(?::|=>?)\s*""" + q, text)
            if ev and EVENT_LIT.fullmatch(ev.group(2)):
                self.send_event(nid, file, lo + hdr[0], ev.group(2), "http", f"signed POST ({hdr[1]})")
                continue
            # the event is a parameter: the callers' literal arguments name it
            params = [x[0] for x in self.s.params(file, nid)] if hasattr(self.s, "params") else []
            idx = next((i for i, x in enumerate(params) if re.fullmatch(r"(?i)(?:trigger_?)?event(?:_?type|_?name)?|type", x.lstrip("$"))), None)
            got = False
            if idx is not None:
                for c in sorted(callers.get(nid, ())):
                    for d, _k, ln, _cf in self.callees.get(c, ()):
                        if d != nid or not ln:
                            continue
                        cn = self.b.nodes.get(c)
                        if cn is None or not cn.file:
                            continue
                        csrc = self.s.text(cn.file)
                        pos = self.s.off(cn.file, ln)
                        nm = re.split(r"[.:#]+", n.name or nid)[-1]
                        cm = re.compile(rf"\b{re.escape(nm)}\s*\(").search(csrc, pos, pos + 400)
                        if not cm:
                            continue
                        from .brokers import _args
                        a = _args(csrc, cm.end() - 1)
                        if idx < len(a):
                            val, conf = self.value(cn.file, cm.start(), a[idx])
                            if val and "{" not in val and EVENT_LIT.fullmatch(val):
                                self.send_event(c, cn.file, cm.start(), val, "http", f"signed POST ({hdr[1]})", via=nid,
                                                conf=conf)
                                got = True
            if not got:
                self.send_event(nid, file, lo + hdr[0], None, "http", f"signed POST ({hdr[1]})")

    def send_event(self, src, file, pos, event, lib, how, via=None, conf=None):
        from .protocols import protocol_send
        name = f"{self.app}:{event if event else '{event}'}"
        line = self.s.line_of(file, pos)
        if ("send", name, src, line) in self.done:
            return
        self.done.add(("send", name, src, line))
        protocol_send(self.b, "webhook", name, src, file, line, (conf or EXACT) if event else HEURISTIC,
                      test=self.is_test(file, src), role="publish", node_attrs={"provider": self.app, "event": event},
                      library=lib, how=how, via=via)
        self.st["webhook_sends"] += 1
        if not event:
            self.miss("webhook_send_event_unknown", f"{file}:{line}")

    # ------------------------------------------------------------ @octokit/webhooks
    def octokit_on(self):
        rx = re.compile(r"""\b(\w*[Ww]ebhooks)\s*\.\s*on\s*\(\s*(?:\[\s*)?(['"])([\w.]+)\2""")
        for f in sorted(self.s.files):
            if not f.endswith(JS_EXT) or f.endswith(".d.ts"):
                continue
            src = self.s.text(f)
            if "@octokit/webhooks" not in src and "probot" not in src:
                continue
            for m in rx.finditer(src):
                if self.s.masked(f, m.start()):
                    continue
                from .brokers import _args
                a = _args(src, src.find("(", m.end(1)))
                h = self.handler(f, m.start(), a[1] if len(a) > 1 else None)
                if not h:
                    continue
                if self.is_test(f, h):
                    continue
                protocol_receive(self.b, "webhook", f"github:{m.group(3)}", h, f, self.s.line_of(f, m.start()), EXACT,
                                 guards=["webhook signature (@octokit/webhooks)"], node_attrs={"provider": "github", "event": m.group(3)},
                                 how="@octokit/webhooks on")
                self.st["webhook_octokit_handlers"] += 1


def apply(project, builder, sock=None) -> dict:
    """Webhook receivers, their verification and the provider events they handle (#37 part 1)."""
    return Webhooks(project, builder, sock).run()
