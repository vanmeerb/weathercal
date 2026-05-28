---
name: "use-uv"
description: "Triggers when executing python scripts within the Astral uv environment present within a project."
---

# Skill: Managing Python Environments and Dependencies with Astral uv

## Purpose
This skill enables the agent to safely inspect, utilize, and validate the Python runtime environment using Astral `uv`. The agent will identify missing dependencies and generate installation commands without executing changes to the codebase or environment.

## Context
This repository uses an isolated Astral `uv` Python environment. The project dependencies and environment configurations are managed via a `pyproject.toml` file or explicit inline script metadata (PEP 723).

## Execution Protocol

### Step 1: Environment Detection
Before running any Python code, inspect the repository to locate the environment configuration:
* Look for `pyproject.toml` in the root directory.
* Check for explicit virtual environments (e.g., `.venv/`).
* Always run Python scripts using `uv run` from the base directory of the project.

### Step 2: Validate Dependencies (Dry-Run Mode)
To check if the current environment is missing required packages for a specific task or script, use `uv tree` or standard Python inspection. Do NOT install anything.

Execute this command to view the currently resolved dependency tree:
```bash
uv tree
```

### Step 3: Reporting Missing Packages
If a required package is missing from the environment or `pyproject.toml`, you must pause execution and report it to the user.

Your report must include:
1. A clear statement identifying which packages are missing.
2. The exact commands the user should run to add them.

#### Required Output Format for Missing Packages:
> ⚠️ **Missing Dependencies Detected**
> The following packages are required but not found in the current environment:
> * `[package_name_1]`
> * `[package_name_2]`
>
> To add these dependencies to the project, please run:
> ```bash
> uv add [package_name_1] [package_name_2]
> ```
> *Note: Do not execute this command automatically unless explicitly requested.*

### Step 4: Running Python Scripts Safely
If all dependencies are present, execute scripts using `uv run` to ensure they use the correct project virtual environment.
Make sure to run it from the base directory of the project.
Always use the following format to run scripts:
```bash
uv run path/to/script.py
```

## Constraints
* **NO AUTOMATIC INSTALLS**: Never run `uv add` or `uv pip install` autonomously. Always report missing packages first.
* **SCOPED EXECUTION**: Always use `uv run` instead of `python` to prevent leaking into global system packages.
