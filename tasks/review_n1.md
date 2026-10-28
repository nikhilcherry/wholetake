owns:
context: ingest/witness.py ingest/transcribe.py ingest/chain.py ingest/optin.py

# Task: adversarial review of N1 (witness ingest). Write NO files.

You are reviewing code written by someone else (shown below). These four modules run as three daemons
(witness, transcriber, chain) plus the opt-in check, for three days at a hackathon, on a laptop with flaky
venue Wi-Fi, witnessing 1-5 opted-in YouTube livestreams. Downstream: a teammate's clip matcher reads
<seq>.json words + .mel.npy; an anchorer pins head.json on-chain every minute; a public verdict says
"this clip was cut / spliced / not in the record", and the claim "no unacknowledged gaps" must hold.

Find real problems. For each: severity (critical = wrong verdict or a provable-integrity claim that's
false; high = data loss or a daemon dying during the event; medium; low), the file + function, a
concrete failure scenario (exact sequence of events), and the smallest fix. Rank most severe first.
Do not pad: no style nits, no "add logging", no "add type hints". If something looks wrong but is
actually handled, don't list it. Be specific about timing, races between the three processes, restart
behaviour, and whether the security claims (hash chain, opt-in consent, received_utc) actually hold.

Put the whole review inside the NOTES section (ignore its 15-line limit for this task), as a numbered list.
