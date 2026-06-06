# Glossary

- **SUT (System Under Test):** the application the agents are exercising.
- **Knowledge entry:** a goal-scoped record (one `*.knowledge.yaml`) describing
  how to recognize states, what success/failure look like, alternative paths,
  and risks — never the steps.
- **Procedure:** the disposable, run-specific sequence of actions an agent took.
  Mneme deliberately does NOT persist this as truth.
- **Assertion:** any believable statement (a signal, risk, or path) carrying
  provenance + confidence + status.
- **Recognition signal:** evidence that the SUT is in a particular semantic state.
- **Success / failure signal (oracle):** observable evidence that a goal was/wasn't met.
- **Event:** an immutable observation appended to the store by one agent.
- **Projection:** the believed knowledge state derived by folding the event log.
- **Adapter:** the only runtime-specific code; bridges a runtime to the schema.
- **Poisoning:** a wrong assertion written with high confidence that spreads to
  other agents. The primary failure mode.
