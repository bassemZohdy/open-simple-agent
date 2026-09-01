# Open Simple Agent Documentation

Use these documents according to the question being answered:

| Document | Purpose |
|---|---|
| [Project definition](../PROJECT_DEFINITION.md) | Product scope, principles, target architecture, and non-goals |
| [Architecture](ARCHITECTURE.md) | Components and behavior implemented in the current source tree |
| [Configuration](CONFIGURATION.md) | Exact `AgentDefinition` schema, defaults, and environment overrides |
| [API reference](API.md) | HTTP routes implemented by the Control Plane and runtime applications |
| [Contributing](../CONTRIBUTING.md) | Development setup, checks, and contribution workflow |
| [Backlog](../TODO.md) | Prioritized work, dependencies, and acceptance criteria |
| [Changelog](../CHANGELOG.md) | Development history and milestone snapshots |
| [Guides](guides/) | Task-oriented operations, deployment, security, and upgrade walkthroughs |
| [ADRs](adrs/) | Architectural decisions and their consequences |

## Documentation rule

Target architecture and current implementation must remain distinguishable.
The project definition may describe planned capabilities. README, architecture,
configuration, and API documents must state only behavior confirmed by source
and tests. `TODO.md` owns unfinished work.
