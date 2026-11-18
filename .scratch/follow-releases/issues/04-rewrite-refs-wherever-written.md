# 04: Rewrite refs wherever they are written

**What to build:** Install rewrites a Tag Ref in the one place its text is written: an `!include`d or local-package file, a substitution's value that is used only for this ref, the `@tag` part of the `github://owner/repo@tag` shorthand, or the ref of a remote `packages:` entry. If the text can't be rewritten safely, the source is reported as untrackable with the reason, as `!secret` refs are today. That covers a substitution used anywhere else or defined more than once, an include's `vars:`, a ref built from several parts, and the same repository referred to at two different refs in one Tracked Config.

**Blocked by:** 03

**Status:** ready-for-agent

- [ ] Discovery tests show where the ref is written for: an inline ref, an included file, a local package, a single-use substitution, the shorthand with `@tag`, and a remote package.
- [ ] Discovery tests report each untrackable case with a reason that names it.
- [ ] Integration tests: Install rewrites an included file and a single-use substitution's value, and leaves every other file byte-for-byte unchanged.
- [ ] Integration test: a substitution also used by `project.version` is untrackable, and no update is offered for that source.
