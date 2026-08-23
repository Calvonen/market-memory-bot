# MarketAI agent execution contract

These instructions apply to AI agents and human automation working with MarketAI.
They describe how to use the system. They do not define trading thresholds, position sizes, confidence limits, stop distances, targets, or other strategy/risk values.

## Canonical control surface

- Read tracked instruments, tracked events, and trading tasks through MarketAI's documented API/RPC/repository interfaces.
- Create or change events and trading tasks only through the documented control surface. Do not bypass it with ad-hoc writes to trading tables.
- Preserve user-approved task values exactly. Do not silently replace trigger, direction, entry, size, stop-loss, take-profit, execution-mode, or other task values with agent-selected defaults.
- If required information is missing or ambiguous, fail closed and report what is missing instead of guessing.

## Instrument identity

- Resolve broker identity through MarketAI's production instrument resolver.
- Never invent or hardcode an eToro instrument id in production workflow logic.
- Fail closed when resolution is ambiguous or the resolved identity does not match the tracked instrument.

## Event identity and time

- Keep a tracked instrument, tracked event, and trading task as separate concepts.
- Adding a tracked instrument or tracked event never creates a trade by itself.
- Persist event timestamps as timezone-aware timestamps and normalize the canonical stored timestamp to UTC.
- Preserve whether an event time is confirmed, estimated, or unknown when that information is available.
- Calendar, release, news, scanner, and manual sources are producers of the same canonical event workflow; source-specific code must not create a separate trading path.

## Trading-task execution

- Treat execution mode as explicit task data. Never infer a different execution mode from context.
- Route trade proposals through the existing StrategyEngine -> RiskEngine -> Broker flow unless the documented task type explicitly says that no trade is being requested.
- Obtain current market inputs required by StrategyEngine/RiskEngine at the decision/execution point; do not substitute stale values when the workflow requires live data.
- A value used by RiskEngine is not a broker-side protective order. If a task requires stop-loss or take-profit protection, confirm that the broker/execution layer actually created it.
- Broker acceptance is not the same as a fill. Report the broker's actual status and reconcile the resulting position/order state when the workflow supports it.
- Prevent duplicate execution: retries must reuse the canonical task/event identity and idempotency mechanism rather than submitting a second order blindly.

## Audit and changes

- Keep canonical identifiers and audit metadata for created/changed/approved tasks and events.
- Read the current version/state before changing an existing task or event and use the documented compare-and-swap/version mechanism when one exists.
- Do not silently change already approved values. A material change requires a new preview/approval cycle when the workflow defines one.

## Safety boundary between tracking and trading

- Observation/reaction monitoring may run without a trading task.
- A tracked event may produce market-reaction data without producing an order.
- Do not promote tracking status, market reaction, or agent analysis into an executable order unless a canonical trading task explicitly requests execution.

## Failure behavior

- Stop on malformed timestamps, ambiguous broker identity, invalid execution mode, missing required market data, version conflicts, broker errors, or any other state where the requested action cannot be represented exactly.
- Report the failure and retain enough identifiers/status information for the workflow to be resumed safely.