# AutoFixer — Polyglot Autonomous Code Engineering & Verification Harness

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Test Suite](https://img.shields.io/badge/tests-17%20passed-brightgreen.svg)]()
[![Architecture](https://img.shields.io/badge/architecture-Harness%20Engineering-orange.svg)]()
[![License](https://img.shields.io/badge/license-MIT-green.svg)]()

**AutoFixer** is an autonomous software engineering and remediation system governed by modern **AI Harness Engineering** principles. Rather than relying on fragile multi-turn conversational tool-calling or subjective model self-reporting (*"I fixed it!"*), AutoFixer enforces deterministic execution pipelines, rigorous **Fail-to-Pass & Pass-to-Pass** contracts, ephemeral sandbox test isolation, and zero-token overhead test execution.

---

## 🌟 Key Capabilities

1. **Three Governed Execution Modes**:
   - **`fix`**: Autonomous bug diagnosis and remediation. Generates a reproducible unit test, asserts failure on the baseline (**Fail-to-Pass**), implements the minimal fix, verifies full regression suites (**Pass-to-Pass**), and strips ephemeral reproduction tests so only true fixes are applied.
   - **`write-code`**: Dual-generation of implementation code and verification suites from natural language objectives, verified against an isolated test runner.
   - **`write-tests`**: Codebase analysis that crafts comprehensive unit and edge-case test suites without mutating source application logic.
2. **Intent Classification (`auto`)**:
   - Classifies user intent into `fix`, `write-code`, or `write-tests` using a lightweight local model (`qwen2.5:0.5b`) or rule heuristics in under 200ms.
3. **Local Isolated Sandbox**:
   - Clones remote repositories or mirrors local directories into an ephemeral temporary sandbox workspace (`.sandbox_venv`).
   - Installs target dependencies (`requirements.txt`, `pyproject.toml`, `setup.py`) and executes test runners natively without container overhead or cloud API keys.
4. **Interactive Verification Gate (`--interactive` / `-i`)**:
   - Halts prior to executing candidate changes to render an inline colorized `git diff` preview and request human approval.
5. **Direct Source Apply & Ephemeral Test Filtering**:
   - Automatically cleans binary artifacts (`.pyc`, `__pycache__`) and strips temporary reproduction test files in `fix` mode, applying clean source updates directly to local folders.
6. **Remote PR Creation & GitHub Publisher**:
   - **Git Repositories**: Automatically creates a dedicated branch (`autofixer/remediation-...`), commits verified changes, pushes to remote, and generates a one-click GitHub/GitLab Pull Request URL.
   - **Local Folders**: Includes an integrated **GitHub Publisher** that creates a new public or private repository on GitHub, initializes git, and pushes local code with personal access token caching.

---

## 🏗️ Architecture

```text
               +-------------------------------------------+
               |        User Task / Objective Prompt       |
               +-------------------------------------------+
                                     |
                                     v
               +-------------------------------------------+
               |  Intent Classifier (qwen2.5:0.5b / rules) |
               |     [ fix | write-code | write-tests ]    |
               +-------------------------------------------+
                                     |
                                     v
+-------------------------------------------------------------------------+
|                  STREAMLINED HARNESS PIPELINE RUNNER                    |
|                                                                         |
|  1. Workspace Context Ingestion (Ast & source scan, ignoring caches)    |
|  2. Governed Pipeline Execution by Mode:                                |
|                                                                         |
|     [ FIX MODE ]                                                        |
|     a. Primary Model generates Reproduction Test Suite                  |
|     b. Polyglot Verifier tests baseline -> MUST FAIL (Fail-to-Pass)     |
|     c. Primary Model generates Source Code Fix                          |
|     d. Verifier runs all suites -> MUST PASS (Pass-to-Pass)             |
|                                                                         |
|     [ WRITE-CODE MODE ]                                                 |
|     a. Primary Model generates Implementation & Test Suite              |
|     b. Verifier runs test suite -> MUST PASS                            |
|                                                                         |
|     [ WRITE-TESTS MODE ]                                                |
|     a. Primary Model generates Edge-Case Test Suite                     |
|     b. Verifier asserts tests pass against unmodified source            |
|                                                                         |
|  3. Backtracking & Recovery Loop:                                       |
|     - Reverts workspace to clean git baseline on verification failures  |
|     - Feeds structured runner stdout/stderr diagnostics to next attempt |
+-------------------------------------------------------------------------+
                                     |
                          [ Verification Passed ]
                                     |
                                     v
               +-------------------------------------------+
               |  Clean Patch Sanitization & Verification  |
               |  - Removes .pyc / __pycache__ / binaries  |
               |  - Strips sandbox reproduction tests (fix)|
               +-------------------------------------------+
                                     |
                  +------------------+------------------+
                  |                                     |
                  v                                     v
    [ Local Source Folder ]                  [ Remote Git Repository ]
    - Direct file patch apply                - Push to autofixer/remediation-*
    - Option: Create & Publish               - One-click Pull Request URL
      new GitHub repository
```

---

## 🚀 Quickstart

### 1. Prerequisites
- **Python**: 3.10 or higher
- **Git**: Installed and available in your `PATH`
- **Ollama**: Running locally with recommended models:
  ```bash
  ollama pull qwen2.5-coder:7b
  ollama pull qwen2.5:0.5b
  ```

### 2. Installation
```bash
git clone https://github.com/<your-username>/Bug_fixer_agent_harness.git
cd Bug_fixer_agent_harness

python -m venv .venv
# Activate virtual environment:
# Windows (PowerShell): .\.venv\Scripts\Activate.ps1
# Linux / macOS:        source .venv/bin/activate

pip install -r requirements.txt
```

### 3. Usage

#### A. Interactive Mode (Recommended)
Launch the interactive terminal assistant:
```bash
python autofix.py
```
**Interactive Walkthrough:**
1. Enter target path (e.g., `D:\my-project` or `https://github.com/org/repo.git`).
2. Enter your objective (e.g., *"Fix division by zero and add subtract method in calculate.py"*).
3. The intent classifier suggests the execution mode (`fix`, `write-code`, or `write-tests`).
4. Choose whether to enable interactive human approval before running tests.
5. AutoFixer executes the harness, verifies against tests, exports `remediation.patch`, and offers to apply changes or push directly.

#### B. CLI Command Line
```bash
# 1. Autonomous Bug Fix
python src/main.py --target ./my-project --objective "Fix KeyError in user session lookup" --mode fix

# 2. Implement New Feature
python src/main.py --target ./my-project --objective "Add JWT token validation utility with unit tests" --mode write-code

# 3. Generate Test Suite
python src/main.py --target ./my-project --objective "Add edge case unit tests for parse_json_payload" --mode write-tests

# 4. Interactive Mode with Git Diff Gate
python src/main.py --target ./my-project --objective "Resolve race condition in worker pool" --interactive
```

---

## 🧪 Testing AutoFixer

Run the comprehensive unit test suite:
```bash
# Set PYTHONPATH to project root
# Windows (PowerShell):
$env:PYTHONPATH="."; .\.venv\Scripts\pytest tests -v

# Linux / macOS:
PYTHONPATH=. pytest tests -v
```

All 17 harness pipeline, workspace isolation, remote PR builder, and GitHub publisher tests will run in `< 1.0s`.

---

## 🛡️ Verification & Safety Invariants

| Invariant | Description |
| :--- | :--- |
| **Fail-to-Pass Verification** | In `fix` mode, a reproduction test must fail against the baseline code before any fix is accepted. |
| **Pass-to-Pass Regression Check** | Existing and new tests must pass simultaneously before output is certified. |
| **Ephemeral Test Isolation** | Reproduction tests are run strictly inside the sandbox and excluded from target repository patches. |
| **Zero-Pollution Sandbox** | Test execution occurs inside an ephemeral copy with a dedicated local virtual environment. |
| **Patch Sanitization** | Binary blobs, `__pycache__`, and compilation caches are stripped from generated patches. |
| **Secret Protection** | Tokens are excluded via `.gitignore` and handled through `.env` configurations. |

---

## 📂 Project Structure

```text
Bug_fixer_agent_harness/
├── autofix.py                 # Interactive entrypoint with GitHub publisher
├── src/
│   ├── main.py                # Command-line CLI entrypoint
│   ├── harness/
│   │   ├── runner.py          # Deterministic Harness Pipeline (Fix, Code, Test)
│   │   ├── workspace.py       # Sandbox isolation, patch export, & direct apply
│   │   ├── publisher.py       # GitHub repository creator and publisher
│   │   ├── verifier.py        # Polyglot test runner (pytest, npm, cargo, go)
│   │   ├── intent.py          # Fast LLM & heuristic intent classifier
│   │   └── interceptors.py    # Security and command interceptors
│   ├── agent/                 # Model client & system prompts
│   └── tools/                 # Sandboxed git & file system tools
├── tests/                     # Unit test suite (pipeline, workspace, publisher)
├── evals/                     # Reproduction test repositories and benchmarks
├── requirements.txt           # Python dependencies
├── README.md                  # Project overview and quickstart
└── PRD.md                     # Product Requirements Document
```

---

## 📄 License
Distributed under the MIT License. See `LICENSE` for more information.