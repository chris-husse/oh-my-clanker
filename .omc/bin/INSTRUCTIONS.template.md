## Limits
Timeout: 45m
Quiet timeout: 5m
Fix cycles: 4

Delete this paragraph: omc reads this section as data, and nothing else may be in
it. `Timeout:` and `Quiet timeout:` (no log growth for that long) are required, as
durations such as `90s`, `45m` or `2h`; `none` switches a limit off. `Fix cycles:` is
optional, 1 to 10: it lowers omc's default of 10 fix cycles for this stage; leave it
out, or write `none`, to keep the default.

## Done when
For the agent, in the project's terms: what makes a run end (the hook exits, or it
writes `passed` or `failed` to `$OMC_STAGE_RESULT`) and, for a verify that can be held
after a failure, what the held system offers for inspection. The hook writes the word
in one write (`echo failed > "$OMC_STAGE_RESULT"`): omc reads it as soon as the file
holds one.

## Passed when
For the agent, in the project's terms: what a pass means and what it covers.

## Fixing failures
For the agent that drives the fix loop: how to read the log, and what the fix agent
must never fix (flaky infrastructure, the stage itself).

## Rerunning a subset
Optional, the only heading that may be left out. For the agent: the arguments that
rerun only named pieces.
