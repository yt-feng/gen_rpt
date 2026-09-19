# DeepSeek white-paper model

The GateX white-paper Action, CLI and research planner use `deepseek-flash`.
The direct DeepSeek client also maps saved `deepseek-v4-pro`, `deepseek-pro` and
`deepseek-v4-flash` selections, including version-suffixed names, to this model.
This applies to per-call overrides and editorial fallback configuration.
Canonical Flash requests retain the explicit
`DEEPSEEK_THINKING` setting (disabled by default) and JSON response contract.

Existing `deepseek-chat` and APIMart/OpenRouter routes retain their separate
configuration. Historical report artifacts retain their original model labels.
