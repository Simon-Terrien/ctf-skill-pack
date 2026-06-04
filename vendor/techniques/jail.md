# Jail / Sandbox Escape Techniques

## Python restricted exec bypass
**When:** Python REPL with `__builtins__` removed or exec/eval restricted.
**Tools:** `().__class__.__base__.__subclasses__()` to find `subprocess.Popen` or `os`.
**Caveats:** Try string encoding: `__import__('os')` → `__import__('\x6f\x73')`.

## Bash restricted shell (rbash) escape
**When:** Shell with restricted PATH or command set.
**Tools:** Tab completion to find allowed commands; `vi :!/bin/sh`; `awk 'BEGIN{system("/bin/sh")}'`
**Caveats:** Check if `/bin/sh` or `/bin/bash` symlinks are present.

## Python pyjail / AST injection
**When:** Custom Python evaluator with AST filter; specific nodes blocked.
**Tools:** Encode strings as bytes; use `chr()+chr()` concatenation; try `compile()`.
**Caveats:** Walrus operator (`:=`), f-string abuse, and `__class_getitem__` bypass many filters.

## PHP disable_functions bypass
**When:** PHP sandbox with `disable_functions` set; need RCE.
**Tools:** `dl()` to load extension; `putenv()+mail()` LD_PRELOAD; `imap_open()` SSRF.
**Caveats:** Check PHP version; some bypasses require writable `/tmp`.

## Docker / container escape
**When:** Running in a container with privileged mode or mounted Docker socket.
**Tools:** `docker.sock` → create privileged container → mount host FS.
**Caveats:** Check `--privileged` with `capsh --print`; cgroup v2 limits some techniques.
