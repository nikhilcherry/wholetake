Review round 1: structure good, 41/41 pass. One real bug that would silently stop anchoring at the event; fix it,
emit changed files in full.

**Replacement-tx race.** After a stuck-tx replacement, `in_flight` tracks only the NEW tx hash. If the ORIGINAL tx
is mined anyway (common: replacement races inclusion), the nonce is consumed, the new tx can never be mined,
`receipt(new)` stays None forever, every 180 s you send another replacement with the same nonce -> "nonce too low"
(or estimate_gas reverts AlreadyTimestamped) -> the exception is swallowed by step's broad except -> in_flight never
clears -> no head is ever anchored again, with only a WARN line per minute.
Fix:
- InFlightTx keeps the list of ALL tx hashes sent for that nonce. `_handle_in_flight` checks receipts for every one
  of them; whichever has a receipt settles the nonce (use that tx's hash/block in the anchor records).
- Before replacing, and whenever a replacement send raises: if `get_transaction_count(sender, "latest") > nonce`
  the nonce is already mined: find the receipt among the known hashes; if none is found (e.g. mined by a tx from
  before a restart), fall back to getTimestamp for each target, record those with ts > 0 (tx = null, method still
  eas.multiTimestamp, add "tx_unknown": true), clear in_flight, and let the next tick pick up anything left.
  Add `nonce_mined(nonce) -> bool` to the client interface (and FakeEAS).
- A replacement that fails for any other reason must not leave the daemon wedged: after 3 consecutive failures
  for the same nonce, clear in_flight via the getTimestamp fallback above and log loudly once.
Tests (FakeEAS needs to model nonces and "original mined after replacement was sent"):
  1. original mined after replacement sent -> records anchored from the original's receipt, in_flight cleared,
     next tick anchors a newer head normally.
  2. replacement send raises "nonce too low" -> same recovery.
  3. daemon restarted with a tx from the previous process still pending, then mined: no wedge; hashes recorded
     via getTimestamp; no duplicate anchor lines for the same head.
