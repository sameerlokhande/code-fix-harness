# Product Requirements Document (PRD)

## Product Name: AutoFixer — Polyglot Autonomous Code Engineering & Verification Harness
**Document Version:** 1.0  
**Status:** Approved / Implemented  
**Author:** AI Harness Engineering Team  

---

## 1. Executive Summary & Vision

Traditional AI code generation agents rely on fragile multi-turn tool-calling loops and unverifiable model assertions (*"I have fixed your bug!"*), resulting in hallucinations, infinite retry loops, and unverified code changes.

**AutoFixer** is a self-governing software engineering harness built upon modern **AI Harness Engineering** principles. It programmatically bounds, sandboxes, and verifies model outputs using native language test runners. AutoFixer treats code modification as a verifiable scientific experiment: no code change is declared successful unless it satisfies strict formal test contracts (**Fail-to-Pass** for bugs, **Pass-to-Pass** for regressions).

---

## 2. Problem Statement

1. **Unverifiable Remediation**: Large Language Models (LLMs) frequently hallucinate success without validating that changes solve the underlying issue or compile cleanly.
2. **Tool-Calling Brittleness & Overhead**: Multi-turn ReAct loops frequently get stuck inspecting trivial files, exhaust token budgets, or drop tool call arguments on small local models.
3. **Environment Pollution & Security Risks**: Running untrusted AI-generated code directly in the host environment risks accidental data deletion, dirty git states, or secret exposure.
4. **Git Apply Incompatibilities**: Patches generated across operating systems or containing binary cache artifacts (`.pyc`, `__pycache__`) crash `git apply`.
5. **Friction in Deployment**: Developers need a seamless transition from local remediation to version control (opening pull requests or publishing newly created repositories to GitHub).

---

## 3. Target Audience & Personas

- **Autonomous DevOps / CI Engineers**: Integrating automated issue remediation pipelines that produce pull requests with green CI checks.
- **Software Engineers & Maintainers**: Rapidly fixing bugs, scaffolding new modules, or expanding unit test coverage across legacy codebases.
- **Local & Offline Developers**: Developers requiring zero-cloud-dependency, private coding agents powered by local models (e.g., Ollama `qwen2.5-coder:7b`).

---

## 4. Key Product Goals & Objectives

- **G1: High Determinism**: Replace unpredictable 10-turn tool loops with a streamlined, phased harness pipeline.
- **G2: Absolute Test Contract Enforcement**:
  - In `fix` mode: Enforce **Fail-to-Pass** (reproduction test MUST fail before fix, and MUST pass after fix) and **Pass-to-Pass** (zero regressions in existing tests).
- **G3: Strict Sandbox Isolation**: Ensure zero host machine pollution; target source files remain untouched until verification criteria are fully satisfied.
- **G4: Ephemeral Test Isolation**: In bug-fix mode, keep reproduction test files strictly inside the sandbox so user repositories are not cluttered with temporary verification files.
- **G5: Seamless Version Control Integration**: Support direct folder patching, automated remote PR creation (`autofixer/remediation-*`), and one-click GitHub publishing for local projects.
- **G6: Sub-Second Harness Overhead**: Harness setup, intent detection, and patch sanitization must complete with near-zero latency on commodity hardware.

---

## 5. System Architecture & Workflows

### 5.1 Architecture Diagram

```text
+-------------------------------------------------------------------------+
|                               USER INPUT                                |
|  - Target: Local Folder (e.g., D:\project) or Git URL (https://...)    |
|  - Objective: Natural language bug report, feature spec, or test task   |
|  - Mode: auto | fix | write-code | write-tests                          |
|  - Interactive Gate: [True | False]                                    |
+-------------------------------------------------------------------------+
                                     |
                                     v
+-------------------------------------------------------------------------+
|                    INTENT CLASSIFIER (qwen2.5:0.5b)                     |
|  - Zero-latency classification (<200ms) with keyword fallback           |
+-------------------------------------------------------------------------+
                                     |
                                     v
+-------------------------------------------------------------------------+
|                       WORKSPACE ISOLATION MANAGER                       |
|  - Clones remote git repo OR mirrors local source directory             |
|  - Initializes temporary sandbox working tree                           |
|  - Scaffolds local .sandbox_venv virtual environment                    |
|  - Installs requirements.txt / setup.py / pyproject.toml                |
+-------------------------------------------------------------------------+
                                     |
                                     v
+-------------------------------------------------------------------------+
|                       STREAMLINED HARNESS PIPELINE                      |
|                                                                         |
|  PHASE 1: Workspace Ingestion                                           |
|  - Scans workspace source files, filtering out .git, caches, & venvs    |
|                                                                         |
|  PHASE 2: Phased Execution & Model Interfacing                          |
|  +-------------------+--------------------+--------------------------+  |
|  |     FIX MODE      |  WRITE-CODE MODE   |     WRITE-TESTS MODE     |  |
|  +-------------------+--------------------+--------------------------+  |
|  | 1. Gen Repro Test | 1. Gen Impl & Test | 1. Analyze Source        |  |
|  | 2. Run Verifier   | 2. Run Verifier    | 2. Gen Test Suite        |  |
|  |    (Expect FAIL)  |    (Expect PASS)   | 3. Run Verifier          |  |
|  | 3. Gen Fix Code   |                    |    (Expect PASS)         |  |
|  | 4. Run Verifier   |                    |                          |  |
|  |    (Expect PASS)  |                    |                          |  |
|  +-------------------+--------------------+--------------------------+  |
|                                                                         |
|  PHASE 3: Backtracking on Failure                                       |
|  - On verification error, resets sandbox to clean baseline commit       |
|  - Appends runner stdout/stderr diagnostics to next attempt prompt      |
|  - Retries up to max_attempts (default: 3)                              |
+-------------------------------------------------------------------------+
                                     |
                          [ Verification Passed ]
                                     |
                                     v
+-------------------------------------------------------------------------+
|                     PATCH EXPORT & SANITIZATION                         |
|  - Strips binary diffs (.pyc, cache folders)                            |
|  - Strips ephemeral reproduction test files if mode == FIX              |
|  - Exports validated remediation.patch                                  |
+-------------------------------------------------------------------------+
                                     |
                +--------------------+--------------------+
                |                                         |
                v                                         v
   [ Local Source Target ]                     [ Remote Git Target ]
   - Direct file apply                         - Push branch:
   - Interactive prompt to publish               autofixer/remediation-*
     to new GitHub Repository                  - Generate GitHub / GitLab
     (GitHubPublisher)                           Pull Request URL
```

---

## 6. Functional Requirements

### FR-1: Intent Classification
- **FR-1.1**: System shall detect intent mode (`fix`, `write-code`, `write-tests`) automatically when `--mode auto` is selected.
- **FR-1.2**: Intent classification shall execute via a local lightweight model (`qwen2.5:0.5b`) with an automated rule-based regex fallback for speed and resilience.

### FR-2: Workspace & Environment Isolation
- **FR-2.1**: Target projects must never be modified directly during model exploration or testing.
- **FR-2.2**: The harness must duplicate local directories or clone remote Git repositories into a temporary sandbox workspace.
- **FR-2.3**: Workspace manager must construct an isolated Python virtual environment (`.sandbox_venv`) inside the sandbox and install dependencies when available.
- **FR-2.4**: Upon completion or process interruption, the temporary sandbox directory must be safely cleaned up without residual artifacts.

### FR-3: Streamlined Harness Execution Pipeline
- **FR-3.1**: The system must operate without multi-turn tool loops, reducing token consumption and eliminating loop degradation.
- **FR-3.2 (Fix Mode Contract)**:
  - Generate a test reproducing the reported defect.
  - Execute test runner: test **MUST FAIL** on baseline code. If it passes, the test is rejected.
  - Generate source code fix based on reproduction diagnostics.
  - Execute test runner: test **MUST PASS**, and all baseline tests must remain green.
- **FR-3.3 (Write-Code Mode Contract)**:
  - Simultaneously generate target code and test suite.
  - Test runner must pass with exit code 0.
- **FR-3.4 (Write-Tests Mode Contract)**:
  - Generate test suite for existing codebase.
  - Prohibit modification of source code.
  - All generated tests must pass on existing code.
- **FR-3.5 (Backtracking & Diagnostics)**:
  - On failure, the harness must revert the sandbox to baseline commit (`git checkout -- .` and `git clean -fd`).
  - Runner error outputs (`stdout` and `stderr`) must be fed into the next prompt context.

### FR-4: Patch Generation & Direct Apply
- **FR-4.1**: Generate a standard unified `remediation.patch` upon successful verification.
- **FR-4.2**: Sanitize patches to remove `.pyc`, `__pycache__`, and non-text artifacts.
- **FR-4.3**: In `fix` mode, strip ephemeral reproduction tests so that only source code repairs are applied to the target folder.
- **FR-4.4**: Support direct patch application to local non-git and git folders with robust fallback.

### FR-5: Git Remote & Pull Request Flow
- **FR-5.1**: When target is a remote repository, create a dedicated branch formatted as `autofixer/remediation-<timestamp>`.
- **FR-5.2**: Push branch to origin and output a pre-formatted Pull Request creation URL for GitHub and GitLab.

### FR-6: Local GitHub Publisher
- **FR-6.1**: For local target folders, offer the option to publish the verified project directly to GitHub.
- **FR-6.2**: Check for `GITHUB_TOKEN` in environment variables or `.env`, and prompt interactively if missing.
- **FR-6.3**: Validate the token against GitHub REST API (`https://api.github.com/user`).
- **FR-6.4**: Create a new repository (prompting for repository name, private/public visibility).
- **FR-6.5**: Initialize local git repo (if not already initialized), commit all files, set remote origin, and push `main` branch.

---

## 7. Non-Functional Requirements

### NFR-1: Performance & Latency
- Intent detection must complete within 500ms.
- Workspace initialization and sandbox preparation must complete within 3 seconds (excluding dependency download).
- Harness unit test suite must execute in under 2 seconds.

### NFR-2: Security & Privacy
- **Zero Cloud Leakage**: Code synthesis and verification run on local LLM instances (Ollama) by default.
- **Secret Sanitization**: `.env` and sensitive tokens are strictly ignored by `.gitignore` and never committed or transmitted in patches.
- **Process Guardrails**: Test execution subprocesses are sandboxed to the target workspace directory with configurable execution timeouts (default: 60s).

### NFR-3: Cross-Platform Compatibility
- Fully operational on Windows (PowerShell/CMD), Linux, and macOS.
- Path handling must normalize Windows backslashes (`\`) and POSIX slashes (`/`).

---

## 8. Verification & Test Metrics

- **Unit Test Coverage**: Automated test suites covering pipeline stages (`test_runner_pipeline.py`), workspace remote branches (`test_workspace_remote.py`), and GitHub publishing (`test_publisher.py`).
- **Target Invariant Pass Rate**: 100% of tests passing (`17 passed in <1.0s`).

---

## 9. Future Roadmap

1. **Docker Container Backend**: Optional containerized sandbox execution for unverified C/Rust binary compilation.
2. **Multi-Model Routing**: Dynamic routing between local Ollama models and cloud APIs (Claude 3.7 Sonnet, GPT-4o) when higher reasoning capacity is required.
3. **Automated GitHub Issue Webhook**: GitHub Action integration to auto-remediate incoming GitHub Issues and open draft PRs without human intervention.

