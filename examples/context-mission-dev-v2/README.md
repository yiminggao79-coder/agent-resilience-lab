PUBLIC DEVELOPMENT v2, not fresh holdout. Same four task texts, candidate baseline,
protected files, oracle inputs and expected values as v1. v1 eight attempts and
32k active-window failure remain; no retrospective success or rescoring.

Prospective configuration revision before v2 live requests: active Coding request
window64000bytes; summary input budget32000bytes. Input/output Token ceilings,
50 model calls/900s perTurn,4096 outputtokens and6 automatic summaries unchanged.
At finalTurn, automatic watermark is prior active-view retained bytes bounded by
current active window (not half totalrequest below irreducible schema overhead).
Earlier Turns131072bytes. Same real bounded prefix/two advancing generations,
postsummaryCoding/externalchecks/ownedSIGKILL+newPID gates. Separate budgets remove
a pathological fixed-overhead confound; this does not prove v1 32k active success.
