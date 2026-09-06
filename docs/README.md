# Open Simple Agent Documentation

Use the shortest document that answers the question:

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

For implementation status, start with the README. For unfinished work, use
`TODO.md`; it contains only open, deferred, or deliberately gated tasks. The
changelog records completed changes and review resolutions.

## Documentation rule

Target architecture and current implementation must remain distinguishable.
The project definition may describe planned capabilities. README, architecture,
configuration, API, and guide documents must state only behavior confirmed by
source and tests. `TODO.md` owns unfinished work; `CHANGELOG.md` owns completed
change history. When a current-behavior document describes a known limitation,
it should link to the corresponding backlog item.
