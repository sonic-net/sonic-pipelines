# Copilot Instructions for sonic-pipelines

## Project Overview

sonic-pipelines contains the CI/CD pipeline definitions and supporting scripts for the SONiC project. It provides reusable Azure DevOps pipeline templates used across sonic-net repositories for building, testing, and releasing SONiC components.

## Architecture

```
sonic-pipelines/
├── azure-pipelines/       # Pipeline template definitions
│   ├── templates/         # Reusable YAML templates
│   └── ...                # Pipeline configurations
├── scripts/               # Supporting scripts for CI/CD operations
├── .azure-pipelines/      # Self-CI for this repo
└── README.md
```

### Key Concepts
- **Azure DevOps**: SONiC uses Azure DevOps (dev.azure.com/mssonic) for CI/CD
- **Pipeline templates**: Reusable YAML templates consumed by other sonic-net repos
- **Build infrastructure**: Defines how SONiC images, packages, and containers are built in CI
- **Test orchestration**: Templates for running VS tests, unit tests, and integration tests

## Language & Style

- **Primary languages**: YAML (Azure Pipelines), Python, Shell
- **YAML formatting**: 2-space indentation, follow Azure Pipelines schema
- **Script conventions**: Bash scripts with `set -e`, Python scripts with standard formatting
- **Template parameters**: Use descriptive parameter names with clear defaults

## PR Guidelines

- **Signed-off-by**: Required on all commits
- **CLA**: Sign Linux Foundation EasyCLA
- **Impact assessment**: Pipeline changes affect all SONiC repos — test thoroughly
- **Backward compatibility**: Templates must remain compatible with existing consumers
- **CI**: Verify pipeline template syntax is valid

## Gotchas

- **Blast radius**: Changes here affect CI for ALL sonic-net repositories
- **Azure DevOps specifics**: Use Azure Pipelines YAML schema — not GitHub Actions syntax
- **Template versioning**: Consumer repos pin to specific branches — coordinate major changes
- **Agent pools**: Be aware of build agent capabilities and availability
- **Secret management**: Never hardcode secrets — use Azure DevOps variable groups
- **Build cache**: Understand caching strategies to avoid stale artifact issues
- **Cross-repo dependencies**: Pipeline changes may require coordinated updates in consumer repos
