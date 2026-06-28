---
description: Compile a Context Pack (brief | standard | full) for handing off to a new thread.
allowed-tools: ["Bash"]
---

Run `basin pack --lod ${ARGUMENTS:-standard} --show` in the current project root.

Print the compiled Context Pack and remind the user they can paste it as the first
message of a new thread, or use `/basin-inherit` to register the inheritance link.
