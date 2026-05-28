---
name: "python-pro"
description: "Triggers when writing new Python modules, refactoring functions, reviewing code quality, or setting up formatters/linters."
---

# Python Code Quality Enforcer Skill

## Context & Purpose
Ensures all Python code adheres strictly to PEP 8 standards, utilizing modern Python conventions and prioritizing Ruff/Black rules for formatting.

## Core Rules & Constraints
* Prefer list comprehensions or generator expressions over explicit `for` loops when the loop body is a single expression that maps or filters items (no side effects, no nested loops, no multi-step logic).
* Never use mutable default arguments in functions (e.g., `def append_to(element, target=[]):`). Always use `target=None`.
* Enforce the use of `pathlib.Path` instead of the legacy `os.path` module for file operations.

## Step-by-Step Workflow
1. Analyze the proposed Python function or script.
2. Check for common anti-patterns (e.g., unhandled exceptions, bare `except:` clauses).
3. If formatting code blocks, format them strictly to Black/Ruff specifications (110-character line limit).
4. If the code already meets all quality standards, respond with a brief confirmation that no issues were found.

## Examples
### Expected Output Format (Anti-pattern vs Modern Solution):
```python
# 🚫 BAD: Legacy os.path and unhandled exception
import os
def read_data(filename):
    try:
        return open(os.path.join("/tmp", filename)).read()
    except:
        return ""

#  GOOD: Modern pathlib and explicit exception handling
from pathlib import Path

def read_data(filename: str) -> str:
    file_path = Path("/tmp") / filename
    try:
        return file_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
```
