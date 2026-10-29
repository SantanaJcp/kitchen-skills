# Test Behavior, Not Implementation

- **Parent:** `prove` ([PRINCIPLES.md](../PRINCIPLES.md))
- **When:** you write, change or keep a test.

A test is a claim about what the code does for its users: a caller, a person, another service. Behavior is what they can observe: return values, errors, state they can read back, a request sent, a row written. Implementation is what they cannot: private helpers, call order, intermediate data.

Judge each test against two imagined edits:

- **Break the behavior.** Return a wrong value, skip an effect, do it twice, revert the fix. The test must fail. If it passes, it proves nothing. Usual causes: an assertion looser than the contract, an expected value computed the way the code does, a restated constant no code path reads, an assertion on data the test built itself.
- **Change the internals.** Keep every observable result and restructure underneath. The test must pass. If it fails, it is pinned to the implementation and catches no bug.

Keep the tests that pass both; rewrite or delete the rest. Drive the code through its users' interface with a concrete input, and take the expected value from the requirement, not from a run of the code.

The contract decides, not the shape of the assertion. A mock assertion counts as behavior only when the contract promises it: "an empty cart never charges" makes "the payment client got no call" behavior, paired with a case where the charge happens. The payload an external service receives is behavior; a call to an internal helper is not. An exact error message pins wording no caller reads; the error type callers branch on is the contract. An invariant (a key present in two tables, a type check) is behavior too.

No test beats a tautological one.
