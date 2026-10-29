# Build the Lever

- **Parent:** `encode-lessons` ([PRINCIPLES.md](../PRINCIPLES.md))
- **When:** any work that is not trivial: edits, migrations, analyses, checks.

Build the tool that does or proves the work (a codemod, script, generator, query) instead of doing it by hand. It does the work the same way every time, reruns for free, and is one artifact a reviewer can read and rerun: "trust me" becomes "run this". Skip it only for a couple of obvious edits you can see at a glance.

- Do the first unit by hand to learn the recipe, then build the tool; prove it by rerunning it on that unit and diffing.
- Make it safe to rerun.
- A deterministic script beats fanning workers out to hand-apply what it could do in one pass. When you do fan out, give the workers one brief with the recipe, the check and what not to touch, outside their write scope.
- Build the smallest script that does the job, never a framework. Commit it when the work outlives the session.

You skipped this when you cite it and the diff holds no script, codemod, generator or shared brief.
