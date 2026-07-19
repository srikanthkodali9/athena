"""HiddenLayer detection adapters.

Two implementations behind one internal ``HiddenLayerAdapter`` protocol,
selected by ``ATHENA_SECURITY_MODE``:

* :class:`LocalHiddenLayerAdapter` — deterministic, offline detection used for
  reproducible reset/replay and CI-style tests. Marked as ``mock``.
* :class:`LiveHiddenLayerAdapter` — httpx boundary to the real HiddenLayer
  runtime-security endpoint (``ATHENA_SECURITY_BASE_URL``). Marked as ``live``.

Both return a :class:`HiddenLayerVerdict` (a *trust* opinion only). Turning a
verdict into an Athena behaviour (quarantine / block / redact / ...) is the
gateway's job, not the adapter's. If a live inspection cannot be completed the
adapter raises :class:`InspectionUnavailable` so the gateway can fail closed.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from typing import Protocol

from .config import SecurityConfig
from .contracts import FindingType, InspectionStage, Severity
from .redaction import scan_secrets


class InspectionUnavailable(RuntimeError):
    """Raised when required inspection could not be completed."""


@dataclass(frozen=True)
class HiddenLayerVerdict:
    """Adapter-level trust opinion for one piece of content."""

    finding: FindingType
    severity: Severity
    confidence: float
    detector: str  # "local-mock" | "hiddenlayer-live"
    matched_categories: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_clean(self) -> bool:
        return self.finding is FindingType.NONE


class HiddenLayerAdapter(Protocol):
    name: str
    is_mock: bool

    def inspect_content(
        self, stage: InspectionStage, content: str, metadata: dict
    ) -> HiddenLayerVerdict:
        ...

    def health(self) -> bool:
        ...


# ---------------------------------------------------------------------------
# Deterministic local adapter
# ---------------------------------------------------------------------------

# Robust, case-insensitive attack signatures. Grouped by finding type; the most
# severe match wins in the adapter.
_PROMPT_INJECTION = [
    re.compile(r"ignore\s+(?:all\s+)?(?:the\s+)?previous\s+(?:instructions|restrictions|rules)", re.I),
    re.compile(r"disregard\s+(?:all\s+)?(?:prior|previous|earlier)\s+(?:instructions|rules|restrictions)", re.I),
    re.compile(r"forget\s+(?:all\s+)?(?:your\s+)?(?:previous\s+)?(?:instructions|rules)", re.I),
    re.compile(r"override\s+(?:the\s+)?(?:system|safety|security)\s+(?:prompt|rules|policy)", re.I),
    re.compile(r"you\s+are\s+now\s+(?:a|an|the)\b", re.I),
    re.compile(r"new\s+instructions?\s*:", re.I),
    re.compile(r"reveal\s+(?:your\s+)?(?:system\s+prompt|instructions|hidden\s+rules)", re.I),
    re.compile(r"act\s+as\s+(?:if\s+)?(?:there\s+are\s+)?no\s+(?:restrictions|rules|guardrails)", re.I),
]

_EXFILTRATION = [
    re.compile(r"upload\s+(?:them|it|the\s+\w+)\s+to\s+(?:an?\s+)?(?:external|remote|third[- ]party)", re.I),
    re.compile(r"(?:send|post|exfiltrate|leak|transmit)\s+(?:them|it|the\s+\w+|data|credentials|secrets)\s+to\b", re.I),
    re.compile(r"external\s+(?:diagnostics?|collection|logging|analytics)\s+endpoint", re.I),
    re.compile(r"\bcurl\s+.*\s+https?://", re.I),
    re.compile(r"(?:POST|PUT)\s+.*\s+https?://", re.I),
    re.compile(r"upload\s+.*\s+https?://", re.I),
]

_CREDENTIAL_ACCESS = [
    re.compile(r"read\s+(?:the\s+)?environment\s+(?:variables\s+)?(?:credentials|secrets|keys)", re.I),
    re.compile(r"(?:read|cat|dump|print|reveal)\s+(?:env|environment)\s+(?:credentials|secrets|variables)", re.I),
    re.compile(r"(?:cat|read|open)\s+[^\s]*(?:/\.aws/credentials|/\.ssh/id_rsa|/etc/shadow|/\.env)\b", re.I),
    re.compile(r"\bAWS_SECRET_ACCESS_KEY\b", re.I),
    re.compile(r"access\s+(?:the\s+)?(?:protected\s+)?credential\s+(?:store|paths?|files?)", re.I),
]

_SUSPICIOUS_INSTRUCTION = [
    re.compile(r"do\s+not\s+(?:tell|inform|report\s+to)\s+(?:the\s+)?(?:operator|human|user|admin)", re.I),
    re.compile(r"before\s+continuing", re.I),
    re.compile(r"without\s+(?:asking|approval|permission|logging)", re.I),
]


@dataclass
class LocalHiddenLayerAdapter:
    """Deterministic offline detector. Never touches the network."""

    name: str = "local-mock"
    is_mock: bool = True

    def inspect_content(
        self, stage: InspectionStage, content: str, metadata: dict
    ) -> HiddenLayerVerdict:
        text = content or ""
        matched: list[str] = []

        inj = _any(_PROMPT_INJECTION, text)
        exf = _any(_EXFILTRATION, text)
        cred = _any(_CREDENTIAL_ACCESS, text)
        secrets = scan_secrets(text)
        susp = _any(_SUSPICIOUS_INSTRUCTION, text)

        # Combined prompt-injection + exfiltration/credential intent is the
        # classic poisoned-runbook signature (PRD §21.3): treat as critical.
        if inj and (exf or cred):
            matched = ["prompt_injection", "exfiltration" if exf else "credential_access"]
            return HiddenLayerVerdict(
                FindingType.POISONED_ARTIFACT, Severity.CRITICAL, 0.98, self.name, tuple(matched)
            )
        if inj:
            return HiddenLayerVerdict(
                FindingType.PROMPT_INJECTION, Severity.HIGH, 0.95, self.name, ("prompt_injection",)
            )
        if exf:
            return HiddenLayerVerdict(
                FindingType.EXFILTRATION, Severity.HIGH, 0.94, self.name, ("exfiltration",)
            )
        if cred:
            return HiddenLayerVerdict(
                FindingType.CREDENTIAL_ACCESS, Severity.HIGH, 0.93, self.name, ("credential_access",)
            )
        if secrets:
            return HiddenLayerVerdict(
                FindingType.SECRET_EXPOSURE, Severity.MEDIUM, 0.9, self.name, tuple(secrets)
            )
        if susp:
            return HiddenLayerVerdict(
                FindingType.SUSPICIOUS_INSTRUCTION, Severity.LOW, 0.6, self.name, ("suspicious_instruction",)
            )
        return HiddenLayerVerdict(FindingType.NONE, Severity.INFO, 0.99, self.name)

    def health(self) -> bool:
        return True


def _any(patterns: list[re.Pattern[str]], text: str) -> bool:
    return any(p.search(text) for p in patterns)


# ---------------------------------------------------------------------------
# Live adapter boundary (HiddenLayer SDK -> interactions.analyze)
# ---------------------------------------------------------------------------


@dataclass
class LiveHiddenLayerAdapter:
    """Boundary to the real HiddenLayer runtime-security service via its SDK.

    Uses the official ``hiddenlayer`` Python SDK (pip ``hiddenlayer-sdk``,
    declared as the optional ``live`` extra). The SDK import is guarded so mock
    mode and unit tests never need the package. Auth is OAuth2 client
    credentials (``client_id``/``client_secret``) or a ``bearer_token``
    fallback. Any SDK / network / auth / parse failure raises
    :class:`InspectionUnavailable` so the gateway fails closed.

    The ``analyze_fn`` seam lets tests inject a fake ``interactions.analyze``
    callable without importing the real SDK or hitting the network.
    """

    config: SecurityConfig
    name: str = "hiddenlayer-sdk"
    is_mock: bool = False
    analyze_fn: object | None = None  # optional injected callable for tests
    #: OpenShell egress-allowlist host patterns. A URL detection pointing at a
    #: host NOT on this allowlist is treated as exfiltration; a URL on it is a
    #: low-signal anomaly. ``None`` = conservative (every external URL is
    #: treated as unapproved). Injected by the orchestrator; kept as read-only
    #: reference data so trust derivation never *enforces* capability.
    egress_allowlist: frozenset[str] | None = None
    _client: object | None = None  # lazily-created hiddenlayer.HiddenLayer

    def _get_analyze(self):
        """Return a callable equivalent to ``client.interactions.analyze``."""

        if self.analyze_fn is not None:
            return self.analyze_fn
        client = self._get_client()
        return client.interactions.analyze

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            # Guarded import: the SDK is an optional extra. Mock mode and unit
            # tests must never require it.
            from hiddenlayer import HiddenLayer
        except ImportError as exc:  # pragma: no cover - env dependent
            raise InspectionUnavailable(
                "hiddenlayer-sdk is not installed (pip install '.[live]')"
            ) from exc
        if not self.config.has_credentials:
            raise InspectionUnavailable("HiddenLayer credentials are not configured")

        # Build kwargs without ever placing secrets in a logged structure.
        kwargs: dict = {"timeout": self.config.request_timeout_seconds}
        if self.config.base_url:  # self-hosted overrides SaaS environment
            kwargs["base_url"] = self.config.base_url
        elif self.config.environment and self.config.environment != "self-hosted":
            kwargs["environment"] = self.config.environment
        if self.config.auth_url:
            kwargs["auth_url"] = self.config.auth_url
        if self.config.client_id and self.config.client_secret:
            kwargs["client_id"] = self.config.client_id
            kwargs["client_secret"] = self.config.client_secret
        elif self.config.bearer_token:
            kwargs["bearer_token"] = self.config.bearer_token
        try:
            self._client = HiddenLayer(**kwargs)
        except Exception as exc:  # constructor / auth-config error
            raise InspectionUnavailable(
                f"HiddenLayer client init failed: {type(exc).__name__}"
            ) from exc
        return self._client

    def inspect_content(
        self, stage: InspectionStage, content: str, metadata: dict
    ) -> HiddenLayerVerdict:
        try:
            analyze = self._get_analyze()
            response = analyze(
                metadata={
                    "model": str(metadata.get("model") or f"athena/{stage.value}"),
                    "requester_id": "athena",
                    "stage": stage.value,
                },
                input={"messages": [{"role": "user", "content": content}]},
            )
        except InspectionUnavailable:
            raise
        except Exception as exc:  # SDK / network / auth / timeout
            raise InspectionUnavailable(
                f"HiddenLayer inspection failed: {type(exc).__name__}"
            ) from exc
        return _map_analysis_to_verdict(
            _extract_analysis(response), self.name, self.egress_allowlist
        )

    def health(self) -> bool:
        # A real analyze call is neither free nor safe to spam during a demo.
        # Report healthy when credentials are configured and the SDK imports;
        # actual reachability surfaces as InspectionUnavailable at call time
        # (which fails closed), so this never hangs the demo.
        if self.analyze_fn is not None:
            return True
        try:
            self._get_client()
            return True
        except InspectionUnavailable:
            return False


def build_hiddenlayer_adapter(config: SecurityConfig) -> HiddenLayerAdapter:
    """Select the adapter for the configured mode."""

    if config.is_live:
        return LiveHiddenLayerAdapter(config=config)
    return LocalHiddenLayerAdapter()


def _coerce_confidence(value: object, default: float = 0.9) -> float:
    try:
        return max(0.0, min(1.0, float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _get(obj: object, key: str, default: object = None) -> object:
    """Read ``key`` from an object attribute or a dict, with a fallback."""

    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _extract_analysis(response: object) -> object:
    """Pull the analysis list out of an SDK response, dict, or namespace.

    The real SDK returns a pydantic model whose ``model_dump()["analysis"]`` is
    a list of detector dicts; fakes may pass a dict or namespace. Try each.
    """

    dump = getattr(response, "model_dump", None)
    if callable(dump):
        try:
            data = dump()
            if isinstance(data, dict) and "analysis" in data:
                return data["analysis"]
        except Exception:  # pragma: no cover - defensive
            pass
    analysis = _get(response, "analysis")
    return analysis


def _host_of(url: str) -> str:
    dest = str(url).strip()
    if "://" in dest:
        dest = dest.split("://", 1)[1]
    dest = dest.split("/", 1)[0].rsplit("@", 1)[-1].split(":", 1)[0]
    return dest.lower()


def _url_is_approved(url: str, allowlist: frozenset[str] | None) -> bool:
    """A URL is approved only if its host matches the OpenShell egress allowlist.

    ``None`` means we have no allowlist to consult, so we treat every external
    URL as unapproved (conservative). This uses the allowlist purely as
    read-only reference data for a trust heuristic — it does not *enforce*
    egress (that stays with OpenShell).
    """

    if not allowlist:
        return False
    host = _host_of(url)
    for pattern in allowlist:
        p = pattern.lower()
        if host == p or fnmatch.fnmatch(host, p) or host.endswith("." + p):
            return True
    return False


_SEVERITY_RANK = {
    Severity.INFO: 0,
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}


def _map_analysis_to_verdict(
    analysis: object,
    detector: str,
    egress_allowlist: frozenset[str] | None = None,
) -> HiddenLayerVerdict:
    """Map a real HiddenLayer ``analysis`` list to a :class:`HiddenLayerVerdict`.

    Derived from REAL captured `interactions.analyze` responses
    (tests/security/data/hl_*.json). ``analysis`` is a LIST of detector objects
    ``{name, detected, phase, findings{probabilities, urls, ...}}`` spanning
    both input and output phases.

    GATING POLICY (one sentence): quarantine/block only on high-signal or
    corroborated evidence — an unapproved-URL exfiltration, a PII/credential
    detection, or prompt_injection corroborated by another malicious signal —
    while a LONE prompt_injection is downgraded to a logged anomaly because the
    real data showed it firing at probability 1.0 on a perfectly benign
    investigation prompt (a false positive that would otherwise quarantine
    normal work). Only this verdict derivation is HiddenLayer-specific; the
    resulting action is chosen by the gateway's §14.4 response policy.

    Everything is read defensively (attr-or-dict, missing keys tolerated) so an
    unexpected shape degrades to a conservative verdict instead of crashing.
    Still, VERIFY against fresh HiddenLayer responses if detector names,
    findings keys, or phases change.
    """

    if analysis is None:
        return HiddenLayerVerdict(FindingType.NONE, Severity.INFO, 0.99, detector)
    if not isinstance(analysis, (list, tuple)):
        analysis = [analysis]

    # Collapse per-detector entries across input/output phases.
    detected: set[str] = set()
    urls: list[str] = []
    probabilities: list[float] = []
    for entry in analysis:
        name = str(_get(entry, "name", "") or "").lower().strip()
        if not name or not bool(_get(entry, "detected")):
            continue
        detected.add(name)
        findings = _get(entry, "findings", {}) or {}
        for u in (_get(findings, "urls", []) or []):
            urls.append(str(u))
        for p in (_get(findings, "probabilities", []) or []):
            try:
                probabilities.append(float(p))
            except (TypeError, ValueError):
                continue

    if not detected:
        return HiddenLayerVerdict(FindingType.NONE, Severity.INFO, 0.99, detector)

    unapproved_urls = [u for u in urls if not _url_is_approved(u, egress_allowlist)]
    has_pi = "prompt_injection" in detected
    has_pii = "personally_identifiable_information" in detected
    has_dos = "denial_of_service" in detected
    has_guardrails = "guardrails" in detected

    # prompt_injection is corroborated when a second malicious signal is present.
    pi_corroborated = bool(unapproved_urls or has_pii or has_dos or has_guardrails)

    # Precedence (strongest differentiator first). See gating policy above.
    if unapproved_urls:
        finding, severity = FindingType.EXFILTRATION, Severity.HIGH
    elif has_pi and pi_corroborated:
        finding, severity = FindingType.POISONED_ARTIFACT, Severity.CRITICAL
    elif has_pii:
        finding, severity = FindingType.SECRET_EXPOSURE, Severity.HIGH
    elif has_guardrails:
        finding, severity = FindingType.SUSPICIOUS_INSTRUCTION, Severity.MEDIUM
    elif has_dos:
        finding, severity = FindingType.ANOMALY, Severity.MEDIUM
    elif has_pi:
        # LONE prompt_injection: unreliable (benign FP at prob 1.0). Log + flag,
        # do NOT exclude/quarantine so investigation prompts keep flowing.
        finding, severity = FindingType.ANOMALY, Severity.LOW
    else:
        # Something fired (url/code, or a detector we don't specifically map).
        # Flag it as a low anomaly rather than silently treating it as clean.
        finding, severity = FindingType.ANOMALY, Severity.LOW

    confidence = max(probabilities) if probabilities else 0.9
    return HiddenLayerVerdict(
        finding, severity, confidence, detector, tuple(sorted(detected))
    )
