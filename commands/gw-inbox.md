---
name: gw-inbox
description: Quick inbox scan — shows recent emails
arguments:
  - name: timeframe
    description: "How far back to scan (default: 1d). Examples: 1d, 3d, 1w, 12h"
    required: false
allowed-tools: [Bash]
user_invocable: true
---

# Gmail Inbox Scan

Scan recent inbox messages.

```bash
CLI="python3 ${CLAUDE_PLUGIN_ROOT}/cli/gw.py || python ${CLAUDE_PLUGIN_ROOT}/cli/gw.py"
```

## Steps

1. **Scan inbox** for the requested timeframe (default: 1d):
   ```bash
   $CLI gmail scan --since "{{timeframe|1d}}" --compact
   ```

2. **Summarize** the results: count, senders, subjects, any urgent items.

3. If the user wants details on a specific message, fetch it:
   ```bash
   $CLI gmail get MESSAGE_ID --compact
   ```
