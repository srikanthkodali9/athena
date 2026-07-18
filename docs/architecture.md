# Athena architecture

This document translates the proposal into a buildable logical and runtime architecture. The diagrams describe the target MVP; they do not imply that the components are already implemented.

## Logical component architecture

```mermaid
flowchart TB
    OP["SRE / reviewer"]
    UI["Judge-facing dashboard"]
    API["Athena API"]

    subgraph CORE["Athena control plane"]
        HB["Heartbeat scheduler"]
        SM["Cycle state machine"]
        PLAN["Investigation planner"]
        ROUTER["Model and tool router"]
        SYNTH["Evidence synthesis and decision"]
        APPROVAL["Approval workflow"]
    end

    subgraph EVIDENCE["Deterministic evidence plane"]
        TOOLS["Approved tool registry"]
        REGISTRY["Evidence registry"]
        METRICS["Metrics and logs"]
        PLATFORM["Kubernetes and manifests"]
        GRAPH["Dependencies and blast radius"]
        REPO["Repository and runbooks"]
    end

    subgraph MODELS["Reasoning plane"]
        MAIN["Nemotron strategic model"]
        OBS["Observability specialist"]
        PLAT["Platform specialist"]
        HIST["Incident historian [P2]"]
        SEC["Security specialist [P2]"]
        CRITIC["Critic and verifier [P2]"]
    end

    subgraph LEARNING["Memory and learning plane"]
        STORE["Durable state store"]
        EPISODE["Episodic memory"]
        SKILLS["Versioned skill registry"]
        REPLAY["Replay validator"]
        PROPOSAL["Runbook proposals"]
    end

    subgraph SECURITY["Security enforcement"]
        HL["HiddenLayer inspection gateway"]
        OS["OpenShell capability boundary"]
        AUDIT["Security and policy audit"]
    end

    OP <-->|"review and approval"| UI
    UI <-->|"HTTP and event stream"| API
    API --> HB
    HB --> SM
    SM --> PLAN
    PLAN --> ROUTER
    ROUTER --> TOOLS
    ROUTER --> OBS
    ROUTER --> PLAT
    ROUTER --> HIST
    ROUTER --> SEC
    ROUTER --> CRITIC
    ROUTER --> MAIN

    TOOLS --> METRICS
    TOOLS --> PLATFORM
    TOOLS --> GRAPH
    TOOLS --> REPO
    TOOLS --> REGISTRY
    OBS --> SYNTH
    PLAT --> SYNTH
    HIST --> SYNTH
    SEC --> SYNTH
    CRITIC --> SYNTH
    MAIN --> SYNTH
    REGISTRY --> SYNTH
    SYNTH --> SM
    SYNTH --> APPROVAL

    SM <--> STORE
    SYNTH --> EPISODE
    EPISODE --> SKILLS
    SKILLS --> REPLAY
    REPLAY --> SKILLS
    SKILLS --> PROPOSAL

    ROUTER <-->|"inspect interactions"| HL
    TOOLS <-->|"enforce actions"| OS
    EPISODE <-->|"inspect writes"| HL
    SKILLS <-->|"inspect writes"| HL
    PROPOSAL <-->|"inspect writes"| HL
    HL --> AUDIT
    OS --> AUDIT
    AUDIT --> UI
```

### Ownership boundaries

| Component | Owns | Must not own |
|---|---|---|
| Athena control plane | Heartbeat, plans, budgets, routing, synthesis, decisions, recovery | Unrestricted execution or security-policy bypass |
| Deterministic evidence plane | Fact collection, parsing, diffs, graph traversal, policy checks | Root-cause judgment |
| Council | Narrow interpretation of explicitly supplied evidence | Persistent goals, broad retrieval, final decision authority |
| Nemotron | Strategic planning, cross-domain synthesis, contradiction resolution, learning | Direct production mutation |
| HiddenLayer | Trust inspection and content-policy decisions | Filesystem or network enforcement |
| OpenShell | Filesystem, process, credential, and egress enforcement | Semantic trust decisions |
| Memory and learning | Durable episodes, patterns, skills, validation history, proposals | Promotion of unverified or quarantined material |

## Runtime and deployment layout

The MVP can run its application and state locally while using configurable GPU inference endpoints. OpenShell encloses application capabilities, while HiddenLayer remains on every required information boundary.

```mermaid
flowchart LR
    subgraph LAPTOP["MacBook or demo host"]
        BROWSER["Dashboard client"]

        subgraph SHELL["OpenShell runtime"]
            API["Athena API<br/>localhost:8000"]
            WORKER["Heartbeat worker"]
            TOOLBOX["Approved diagnostics"]
            DB[("Durable local state")]
            FILES["Fixtures, skills, proposals"]
        end
    end

    subgraph GPU["NVIDIA GPU environment"]
        VMAIN["Strategic vLLM endpoint<br/>:8001/v1"]
        VCOUNCIL["Council vLLM endpoint<br/>:8002/v1"]
    end

    subgraph TRUST["Approved security service"]
        HLGW["HiddenLayer adapter"]
    end

    subgraph SOURCES["Approved read-only sources"]
        PROM["Prometheus-compatible API"]
        K8S["Kubernetes API or fixtures"]
        GIT["Monitored repository"]
    end

    BROWSER <-->|"HTTP / SSE"| API
    API <--> WORKER
    WORKER <--> DB
    WORKER <--> FILES
    WORKER --> TOOLBOX
    WORKER -->|"OpenAI-compatible API"| VMAIN
    WORKER -->|"OpenAI-compatible API"| VCOUNCIL
    WORKER <-->|"inspect each trust transition"| HLGW
    TOOLBOX --> PROM
    TOOLBOX --> K8S
    TOOLBOX --> GIT
```

Ports in this diagram are recommended development defaults and must remain configurable. HiddenLayer and source endpoints are configured through allowlisted adapters rather than hard-coded vendor locations.

## Heartbeat and investigation sequence

```mermaid
sequenceDiagram
    autonumber
    participant H as Heartbeat scheduler
    participant A as Athena orchestrator
    participant HL as HiddenLayer gateway
    participant T as Deterministic tools
    participant C as Council via vLLM
    participant N as Nemotron via vLLM
    participant M as Durable memory
    participant U as Dashboard / reviewer

    H->>A: Start periodic or manual cycle
    A->>M: Load state and incomplete work
    A->>T: Collect checkpoints and changed inputs
    T-->>A: Schema-validated evidence candidates
    A->>HL: Inspect heartbeat and retrieved content
    HL-->>A: Allow, redact, exclude, or quarantine

    alt No meaningful change
        A->>M: Persist WAIT cycle
        A-->>U: Publish cycle result
    else Investigation required
        A->>N: Request bounded investigation plan
        N-->>A: Specialists, tools, budgets, and stop conditions
        A->>HL: Inspect plan and prompts
        HL-->>A: Policy decisions

        par Round 1: deterministic evidence and Observability
            A->>T: Run approved read-only queries
            T-->>A: Evidence envelopes
        and
            A->>C: Run Observability specialist (Nemotron-Nano)
            C-->>A: Symptoms, timeline, hypothesis with evidence IDs
        end

        A->>HL: Inspect Round 1 tool results and Observability response
        HL-->>A: Sanitised trusted context

        A->>N: Mid-cycle evaluation — CONTINUE_PLAN or REVISE_PLAN
        N-->>A: Confirmed or revised Platform specialist question

        A->>C: Run Platform specialist (Nemotron-Nano) with updated question
        C-->>A: Infrastructure findings with evidence IDs

        A->>HL: Inspect Platform response
        HL-->>A: Sanitised trusted context

        A->>N: Synthesize and resolve contradictions
        N-->>A: Root cause, confidence, and recommendation
        A->>N: Run adversarial self-critique prompt (Critic role)
        N-->>A: Validated conclusion or identified unsupported claim

        alt Approval required
            A-->>U: Create approval request
            U-->>A: Approve or reject
        end

        A->>HL: Inspect memory and learning writes
        HL-->>A: Allow or reject persistence
        A->>M: Commit cycle, episode, and approved artifacts
        A-->>U: Publish final evidence and metrics
    end
```

## Recursive learning lifecycle

```mermaid
flowchart LR
    EVENT["Operational event"] --> EPISODE["Verified episode"]
    EPISODE --> LESSON["Lesson extraction"]
    LESSON --> CANDIDATE["Versioned candidate skill"]
    CANDIDATE --> SCAN{"HiddenLayer scan"}
    SCAN -->|"blocked"| QUARANTINE["Quarantined"]
    SCAN -->|"passed"| SHADOW["Shadow evaluation"]
    SHADOW --> REPLAY{"Replay validation"}
    REPLAY -->|"failed"| REJECTED["Rejected or revised"]
    REPLAY -->|"passed"| PROMOTED["Promoted skill"]
    PROMOTED --> RETRIEVE["Later heartbeat retrieval"]
    RETRIEVE --> IMPROVEMENT["Measured incident improvement"]
    IMPROVEMENT --> EPISODE
    PROMOTED --> PROPOSAL["Reviewable runbook proposal"]
```

Promotion requires evidence provenance, security history, replay results, versioning, and any risk-based human approval. Quarantined artifacts never enter historian retrieval.

## Trust-boundary enforcement

```mermaid
flowchart TB
    INPUT["Untrusted content<br/>telemetry, repository, runbook, user, model, or tool"]
    INSPECT{"HiddenLayer inspection"}
    SAFE["Sanitized bounded context"]
    BLOCK["Redact, exclude, quarantine, or block"]
    REASON["Athena or Council reasoning"]
    INTENT["Proposed tool call or write"]
    INSPECT2{"HiddenLayer inspection"}
    POLICY{"OpenShell policy"}
    EXECUTE["Approved capability"]
    APPROVAL["Approval request"]
    DENY["Denied and audited"]
    MEMORY["Agent-owned durable state"]

    INPUT --> INSPECT
    INSPECT -->|"trusted or sanitized"| SAFE
    INSPECT -->|"unsafe"| BLOCK
    SAFE --> REASON
    REASON --> INTENT
    INTENT --> INSPECT2
    INSPECT2 -->|"unsafe"| BLOCK
    INSPECT2 -->|"allowed to evaluate"| POLICY
    POLICY -->|"allow"| EXECUTE
    POLICY -->|"approval-gated"| APPROVAL
    POLICY -->|"deny"| DENY
    EXECUTE --> MEMORY
    APPROVAL -->|"approved"| EXECUTE
    APPROVAL -->|"rejected"| DENY
```

HiddenLayer answers whether content or intent should be trusted. OpenShell independently determines whether the resulting action is technically permitted.

## Requirement traceability

| Architecture view | Primary requirements |
|---|---|
| Logical component architecture | FR-3, FR-4, FR-5, FR-6, FR-7, FR-13 |
| Runtime and deployment layout | FR-10, FR-11, FR-12 |
| Heartbeat sequence | FR-1, FR-2, FR-3, FR-6, FR-7 |
| Recursive learning lifecycle | FR-8, FR-9, FR-14 |
| Trust-boundary enforcement | FR-10, FR-11 |
