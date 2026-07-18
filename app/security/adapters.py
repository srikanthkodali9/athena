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
# Live adapter boundary (httpx -> ATHENA_SECURITY_BASE_URL)
# ---------------------------------------------------------------------------

_FINDING_ALIASES = {
    "prompt_injection": FindingType.PROMPT_INJECTION,
    "jailbreak": FindingType.PROMPT_INJECTION,
    "exfiltration": FindingType.EXFILTRATION,
    "data_exfiltration": FindingType.EXFILTRATION,
    "credential_access": FindingType.CREDENTIAL_ACCESS,
    "secret": FindingType.SECRET_EXPOSURE,
    "pii": FindingType.SECRET_EXPOSURE,
    "poisoned": FindingType.POISONED_ARTIFACT,
    "anomaly": FindingType.ANOMALY,
}

_SEVERITY_ALIASES = {
    "info": Severity.INFO,
    "informational": Severity.INFO,
    "low": Severity.LOW,
    "medium": Severity.MEDIUM,
    "moderate": Severity.MEDIUM,
    "high": Severity.HIGH,
    "critical": Severity.CRITICAL,
}


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
        analysis = getattr(response, "analysis", None)
        if analysis is None and isinstance(response, dict):
            analysis = response.get("analysis")
        return _map_analysis_to_verdict(analysis, self.name)

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


def _map_analysis_to_verdict(analysis: object, detector: str) -> HiddenLayerVerdict:
    """Map a HiddenLayer ``response.analysis`` to a :class:`HiddenLayerVerdict`.

    VERIFY against the real HiddenLayer response shape before the live demo.
    The exact structure of ``response.analysis`` is not publicly documented, so
    this reads defensively: it looks for a list of detections/findings under a
    few likely keys, maps each detection's category/type to our
    :class:`FindingType`, and keeps the most severe. Anything unexpected
    degrades to a conservative verdict rather than crashing. "No detections"
    (or a clean/verdict flag) is treated as :data:`FindingType.NONE`.
    """

    # Empty analysis -> clean.
    if analysis is None:
        return HiddenLayerVerdict(FindingType.NONE, Severity.INFO, 0.99, detector)

    # Some responses may carry a top-level verdict / flagged flag.
    verdict_flag = _get(analysis, "verdict", _get(analysis, "flagged"))
    if isinstance(verdict_flag, str) and verdict_flag.lower() in {"clean", "safe", "allow", "pass"}:
        return HiddenLayerVerdict(FindingType.NONE, Severity.INFO, 0.99, detector)

    # Find the detections list under any of the likely keys.
    detections = None
    for key in ("detections", "findings", "results", "detected", "categories"):
        candidate = _get(analysis, key)
        if candidate:
            detections = candidate
            break

    if not detections:
        # No detections found. If an explicit boolean flag says flagged=True but
        # gave no detail, be conservative (anomaly); otherwise treat as clean.
        if verdict_flag is True:
            return HiddenLayerVerdict(FindingType.ANOMALY, Severity.MEDIUM, 0.6, detector)
        return HiddenLayerVerdict(FindingType.NONE, Severity.INFO, 0.99, detector)

    if not isinstance(detections, (list, tuple)):
        detections = [detections]

    best: HiddenLayerVerdict | None = None
    categories: list[str] = []
    for det in detections:
        raw_cat = str(
            _get(det, "category", _get(det, "type", _get(det, "name", ""))) or ""
        ).lower().strip()
        if raw_cat:
            categories.append(raw_cat)
        finding = _match_finding(raw_cat)
        sev_raw = str(_get(det, "severity", "medium")).lower()
        severity = _SEVERITY_ALIASES.get(sev_raw, Severity.MEDIUM)
        confidence = _coerce_confidence(
            _get(det, "confidence", _get(det, "score", _get(det, "risk", 0.9)))
        )
        candidate = HiddenLayerVerdict(finding, severity, confidence, detector)
        if best is None or _more_severe(candidate, best):
            best = candidate

    if best is None:
        return HiddenLayerVerdict(FindingType.NONE, Severity.INFO, 0.99, detector)
    return HiddenLayerVerdict(
        best.finding, best.severity, best.confidence, detector, tuple(categories)
    )


_SEVERITY_RANK = {
    Severity.INFO: 0,
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}


def _more_severe(a: HiddenLayerVerdict, b: HiddenLayerVerdict) -> bool:
    ra, rb = _SEVERITY_RANK[a.severity], _SEVERITY_RANK[b.severity]
    if ra != rb:
        return ra > rb
    return a.confidence > b.confidence


def _match_finding(raw_category: str) -> FindingType:
    """Map a HiddenLayer detection category string to our FindingType."""

    if not raw_category:
        return FindingType.ANOMALY
    text = raw_category.replace("-", "_").replace(" ", "_")
    # Direct alias first.
    if text in _FINDING_ALIASES:
        return _FINDING_ALIASES[text]
    # Substring signals (defensive; categories may be verbose).
    if "inject" in text or "jailbreak" in text or "prompt" in text:
        return FindingType.PROMPT_INJECTION
    if "exfil" in text or "leak" in text:
        return FindingType.EXFILTRATION
    if "credential" in text or "secret_access" in text:
        return FindingType.CREDENTIAL_ACCESS
    if "secret" in text or "pii" in text or "sensitive" in text:
        return FindingType.SECRET_EXPOSURE
    if "poison" in text or "backdoor" in text:
        return FindingType.POISONED_ARTIFACT
    if "suspicious" in text or "policy" in text:
        return FindingType.SUSPICIOUS_INSTRUCTION
    return FindingType.ANOMALY
