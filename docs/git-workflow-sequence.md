# Fork Branch Workflow

This diagram shows the recommended fork workflow:

- keep `main` in the fork synced with upstream `main`
- use `dev` as the fork-internal integration branch
- group related `feat/*` branches into a batch, merge the batch into `dev`, run one integration test, then delete the feature branches unless you expect immediate follow-up work
- create a clean upstream-facing branch only when the change set is ready, and delete it after the upstream PR is merged or closed

```mermaid
sequenceDiagram
    autonumber
    participant U as Upstream main
    participant F as Fork main
    participant D as dev
    participant B as feat/* batch
    participant P as upstream PR branch

    U->>F: Sync fork/main with upstream/main
    F->>D: Rebase dev onto latest fork/main

    B->>D: PR batch of feat/* branches -> dev

    D->>D: Integrate and test combined changes
    D->>P: Create clean branch for upstream-facing change
    P->>U: Open PR to upstream/main

    U-->>F: Upstream updates continue
    F-->>D: Rebase again when needed
```

Branch graph view:

```mermaid
flowchart TD
    U[Upstream main]
    F[Fork main]
    D[dev]
    G[feat/* batch]
    A[feat/alpha]
    B[feat/beta]
    P[upstream PR branch]

    U -->|sync| F
    F -->|rebase| D

    subgraph G1[batch]
        A --> G
        B --> G
    end

    G -->|merge into| D

    D -->|integrate and test| D
    D -->|create clean branch| P
    P -->|PR to upstream| U

    U -. future updates .-> F
    F -. rebase when needed .-> D
```

