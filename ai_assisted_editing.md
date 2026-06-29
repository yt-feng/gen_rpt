# AI Assisted Document Intelligence

## Architecture Overview

Phase 10 introduces the AI Intelligence layer over the Document Editing Studio. Crucially, the AI is constrained strictly to the role of an **assistant**. It cannot alter the canonical document independently; every generation creates a structured `AIProposal` that awaits human approval.

---

## 1. Structured Proposals

An AI operation (e.g., rewriting a paragraph) never returns raw text applied to the live document. Instead:
- It targets specific Canonical Nodes (`target_node_stable_ids`).
- It extracts a `context_bundle` (the Node's current payload, parent structure, etc.).
- It creates an `AIProposal` record holding the `response_content` structured JSON.
- The status defaults to `pending`.

---

## 2. Model & Provider Abstraction

To support multi-cloud architectures, the backend employs the `BaseAIProvider` class alongside the `AIProviderFactory`.
- Configured models currently support representations for `Groq`, `OpenAI`, `Anthropic`, `Gemini`, and `Local` inference engines.
- The models take the `context_bundle` and the user's explicit instructions (`prompt_text`), executing the rewrite or generation asynchronously.

---

## 3. Human Approval Workflow

Reviewers interact with the `AIProposal` instances natively from the Editor UI.
- **Accept**: The `accept_proposal` endpoint merges the proposed Markdown directly into the user's Draft `DocumentVersion`, executing the rigorous `update_node_content` ledger tracking developed in Phase 9.
- **Modify & Accept**: Reviewers can override the AI's proposal natively by supplying `modified_content` before accepting.
- **Reject**: Transitioning the proposal to `rejected` gracefully archives the model's footprint for compliance auditing without polluting the working draft.

---

## 4. Alternative Generations

Because the system tracks every proposal individually, the backend can safely request *N Alternatives* simultaneously (`num_alternatives`).
- A single request can dispatch 3 variations of an "Executive Rewrite".
- All three exist as `pending` proposals in the system.
- The UI allows the user to browse them, accept the best one, and naturally abandon the rest.

---

## 5. Token Management & Metrics

Every proposal actively tracks:
- `prompt_tokens`
- `completion_tokens`
- `execution_time_ms`

This forms the foundational layer for cost analytics, rate-limiting, and inference scaling.

*(Note: The integration tests validate this flow using robust MockProviders to guarantee the backend acts as a strict state machine orchestrating AI responses).*
