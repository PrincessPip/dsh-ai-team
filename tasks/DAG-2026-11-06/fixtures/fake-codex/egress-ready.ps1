# fake-codex/egress-ready.ps1 -- stand-in for <DSH-SCRIPTS>/cdp/codex-egress-check.ps1
# Prints the same single-line JSON shape the real script prints with -Json, so
# runner.check_codex_egress() can be exercised without a live ssh tunnel.
#
# PORT 18081, NOT <PROXY-PORT-TUNNEL> (2026-11-11, round-2 reviewer B1):
#   the SSH tunnel's real port is <PROXY-PORT-TUNNEL>, and the reviewer environment is REQUIRED to carry
#   HTTPS_PROXY=http://127.0.0.1:<PROXY-PORT-TUNNEL> (AGENTS.md precondition 3). If this stand-in also said <PROXY-PORT-TUNNEL>,
#   a child process that merely INHERITED the host proxy would satisfy the "proxy was injected"
#   assertion -- deleting the injection line left the bench green. 18081 cannot be inherited by luck.
# Keep pure ASCII (PowerShell 5.1 decodes non-ASCII as 936).
Write-Output '{"port":18081,"egress":"0.0.0.0","config":"FAKE (bench stand-in)","source":"fake","action":"fake ready","result":"READY"}'
exit 0
