# SEC — a second signup can hijack a pending verification

Status: **CONFIRMED, NOT FIXED.** Found 2026-08-04 by an adversarial audit and
verified by reading the live code path. Deliberately not patched in the same
session it was found — see "Why this is not fixed here".

Severity: **high, conditional.** Account takeover, but it requires the attacker
to act inside the victim's 30-minute verification window.

## The mechanism

`app/auth_routes.py` stores a pending signup in `kv_store` keyed **only by email
address**, and `_put_pending` overwrites the whole record:

```python
def _put_pending(db, email, value):        # auth_routes.py:167
    row = db.query(models.KVStore).filter_by(key=_pending_key(email)).first()
    if row: row.value = value              # ← wholesale replace
```

The record holds the password hash **and** the code hash together. Verification
then creates the account from whatever is currently stored:

```python
p = _get_pending(db, email)
if not secrets.compare_digest(given, p.get("code_sha") or ""): ...
resp = _create_user(db, request, email, p.get("name") or "", p["password_hash"])
```

So the account is created with the password from the **most recent POST**, not
the password belonging to the code the user actually received and typed.

## Exploit

1. Victim signs up as `victim@x.com` with password **A**.
   Pending = `{hash(A), code1}`; `code1` is emailed to the victim.
2. **More than 60 seconds later** (SEC-03's cooldown lapses), the attacker POSTs
   `/api/auth/signup` for `victim@x.com` with password **B**.
   Pending is overwritten to `{hash(B), code2}`; `code2` is emailed to the victim.
3. The victim — who is expecting a verification code — enters `code2`.
4. `_create_user` runs with `p["password_hash"]` = **hash(B)**.
5. The attacker signs in as the victim with password **B**.

The victim receives two emails. Anyone mid-signup would reasonably use the newer
code, which is the one that carries the attacker's password.

SEC-03's 60-second cooldown does not prevent this. It caps *email volume*; it
does not bind a password to a code.

## Why the obvious fix is wrong

"First writer wins — refuse to overwrite a live pending record" reverses the
exploit rather than closing it:

1. Attacker seeds a pending for `victim@x.com` with password **B**. The victim
   receives an unsolicited code.
2. The victim then signs up with their own password **A** — and is now *ignored*,
   because a live pending exists.
3. The victim, seeing a code in their inbox, enters it. It is the attacker's
   code, tied to the attacker's password.

Same takeover, opposite ordering. Picking a winner between two writers cannot
work when the loser is the one holding the mailbox.

## The correct fix

**Bind the password to the code**, so verifying a given code creates the account
with the password submitted alongside *that* code:

- Store pending signups keyed by `code_sha` (or keep a small list per address),
  each entry carrying its own `password_hash`, `name`, `expires`, `attempts`.
- On verify, look up by the submitted code and use that entry's `password_hash`.
- Consume/expire the whole set for the address once one entry verifies.
- Keep the SEC-03 send cooldown unchanged — it solves a different problem
  (email bombing) and is still needed.

This makes the attacker's entry inert: it exists, but the victim types the code
from the mail they requested, and that code carries their own password. A victim
who types the wrong code lands on a password they do not know and recovers via
reset — bad UX in a rare case, not a takeover.

A defensible alternative: require the password again at verification time and
match it against the stored hash. Simpler to reason about, but it changes the
signup UX and the client contract.

## Why this is not fixed here

This is the authentication path of a live product with real accounts. The fix
changes the shape of the pending-signup store and the verify contract, and it
interacts with SEC-03's cooldown, the `/resend-code` path and the 30-minute
expiry. It deserves its own change with its own tests — including a test that
reproduces the takeover and fails without the fix — not a patch appended to the
end of a long unrelated session.

The mitigating factors that make waiting acceptable: the attacker must know the
target address, must act inside a 30-minute window that only opens when the
victim is actively signing up, and the victim receives two verification emails.

## Test the fix must carry

A regression test that plays the sequence above end to end and asserts the
created account's password hash is the **victim's**, not the second writer's —
and that it fails when the fix is removed. The audit that found this also found
that a test which re-implements the logic inline can pass with the fix disabled;
this one must drive the real signup and verify routes.
