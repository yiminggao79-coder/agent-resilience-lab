# Fork containment research evidence

Public Apple XNU/WebKit and libuv source excerpts, with original line numbers and connector-reported blob SHAs. Selected branches were read; none of those projects was built or tested. Apple XNU excerpts come from the official `xnu-10063.141.1` tag, resolved through its public Git tag object to commit `d8b80295118ef25ac3a784134bcf95cd8e88109f`.

XNU source is subject to Apple Public Source License 2.0; `APPLE_LICENSE` preserves the upstream license. WebKit and libuv excerpts are public-source research quotations linked to their upstream files; they are not incorporated into runtime code. The source file headers and full upstream terms remain at the linked originals.

`probe-results.json` records isolated synthetic local Seatbelt experiments. Probe 1 stores normalized observed outcomes without transient PID values; probe 2 preserves its original JSON output. All created children were known direct children of a synthetic sandbox parent, killed by that parent, and `waitpid`-reaped. No runtime, test, installation, permission configuration, credential or API-backed model was modified or used.
