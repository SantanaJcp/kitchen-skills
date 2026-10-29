# Attack the Premise

- **Parent:** `root-cause` ([PRINCIPLES.md](../PRINCIPLES.md))
- **When:** two or more fixes that share one premise have failed the same gate.

Suspect the premise, not the fixes. Each failed fix is evidence against the belief it assumed; a third fix on it spends effort where the evidence says the cause is not.

- **Write the premise down:** the one sentence every failed fix assumed.
- **Test it before the next fix.** Find an observation that would come out differently if the premise were false, and make it. Look at how the failures are distributed, not only how large they are. Make the check rerunnable.
- **Follow the evidence.** If it contradicts the premise, look for what the failures share that the premise did not predict. If it confirms it, ask the next "why" ([fix-root-causes](fix-root-causes.md)); the earlier fixes may have treated a consequence.
- **Remove the condition instead of compensating for it.** A mechanism that cleans up after the cause leaves it in place and adds work on every run.

Do not start the next fix before the premise is written down and tested.
