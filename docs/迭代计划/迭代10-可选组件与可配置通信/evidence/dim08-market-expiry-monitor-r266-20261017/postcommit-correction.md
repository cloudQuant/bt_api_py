# R266 post-commit metadata correction

This note supplements SDK commit `2140c0d87f2e06bfd6a1094f56de3dc5fde52ed4`; it does not rewrite that accepted history or change the R266 test/evidence.

The original manifest recorded `round-record.md` as 4,067 bytes with SHA-256 `3813a9c951359dbe9225c2f6ec8cff8ce1b624cac19c666ad2bb5f7f0e83793f`, which described its pre-commit CRLF working copy. The committed Git blob is the normalized LF form: 4,052 bytes, SHA-256 `45c8f5d7b351004f673c71392acfb392c69549c3e4f874257737005392969890`. The manifest is corrected to reference that committed blob. A Windows checkout may materialize equivalent CRLF line endings; Git normalization yields the recorded LF blob. No evidence member, test pin, QA result, or source behavior changed.
