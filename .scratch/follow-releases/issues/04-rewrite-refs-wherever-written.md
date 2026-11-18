# 04: Rewrite refs wherever they are written

**What to build:** Install rewrites a Tag Ref in the one place its text is written: an `!include`d or local-package file, a substitution's value that is used only for this ref, the `@tag` part of the `github://owner/repo@tag` shorthand, or the ref of a remote `packages:` entry. If the text can't be rewritten safely, the source is reported as untrackable with the reason, as `!secret` refs are today. That covers a substitution used anywhere else or defined more than once, an include's `vars:`, a ref built from several parts, and the same repository referred to at two different refs in one Tracked Config.

**Blocked by:** 03

**Status:** done

- [x] Discovery tests show where the ref is written for: an inline ref, an included file, a local package, a single-use substitution, the shorthand with `@tag`, and a remote package.
- [x] Discovery tests report each untrackable case with a reason that names it.
- [x] Integration tests: Install rewrites an included file and a single-use substitution's value, and leaves every other file byte-for-byte unchanged.
- [x] Integration test: a substitution also used by `project.version` is untrackable, and no update is offered for that source.

## Comments

- Discovery now guarantees: every Tag Ref it returns has one or more `written_at` locations; any Tag Ref whose text cannot be rewritten safely is in `untrackable` instead, as "<where> source <name> at '<ref>': <reason>". So ticket 03's Install refusal ("cannot rewrite a ref that is not written as is") and its test are gone; `install_plan` still skips a move without a location.
- Locations are found in `git_sources._locate_tag_ref` from the candidate's raw (unsubstituted) ref: a `_Written` dict `ref:`; the end of a `_Written` shorthand (`@tag`); or, when the raw ref is exactly one `$x`/`${x}`, the value of substitution `x` (which carries its own file, e.g. a package that defines it). `_parse_source` no longer sets a location.
- Untrackable reasons: "its repository is referred to at several refs" (any Tag Ref whose URL appears with another ref, including a branch or no ref; the other, non-tag source stays tracked), "ref comes from an include's vars" (strings an include's `vars:` changed are marked `_FromVars`), "ref is built from several parts", "substitution x is defined more than once" (counted across the config and its local packages), "substitution x is also used elsewhere" (`$x`/`${x}` counted in every key and value of the resolved config, so substitution values referring to it count too), "substitution x is not written as is", "ref is not written as is in the YAML" (escapes, folding).
- Not covered: a remote package's own files may also use a substitution; they are never read (ADR-0006), so such a use is not counted.
- `ref: 1.10` bug fixed: the loader constructs YAML ints and floats as their text (`_Written` strings), so a ref, a substitution value or anything else numeric keeps its scalar text.
- Merged with ticket 06: an Unset Ref whose repository is referred to in more than one place (another Unset Ref or any ref) gets no insertion location, so it follows its Latest Commit, and one note per repository is logged ("is referred to in several places, so it cannot follow versions"). A ref inserted in one place only would otherwise leave the repository at two refs. 06's Unset cases with no location (a shorthand from a substitution, an empty `ref:`) keep following commits.
- A plain (unquoted) ref or substitution value is rewritten with 06's `_scalar` quoting, as inserted refs are: `ref: 1.9` moved to `1.10` becomes `ref: "1.10"`, else ESPHome would build `1.1` while the store records `1.10` (review fix). Quoted scalars and a shorthand's `@tag` are written as is. `RefLocation.plain` says which.
- Tests: discovery in `tests/test_ref_locations.py`, Install in `tests/test_ref_rewrite_install.py`. README updated.
- Review fix: the same Tag Ref written in several places (a remote package and an external component from one repository, both `ref: v1.4.0`, or one file included twice) is no longer untrackable. `GitSource.written_at` is now a tuple of locations: duplicates of one `url@ref` are merged with every distinct location, and an Install rewrites them all. If any one of those places cannot be rewritten, every place is untrackable ("its ref is also written where it cannot be rewritten"), since rewriting only some would leave the repository at two refs. Not covered: two places taking the ref from one substitution count as "substitution x is also used elsewhere"; and the same repository written as two URL spellings (with and without `.git`) is still two Sources. Unset Refs in several places still follow commits, as before.
